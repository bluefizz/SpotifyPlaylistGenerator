import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from datetime import datetime, timedelta
import json
from collections import defaultdict
import time
from dotenv import load_dotenv

# Load environment variables (for local dev; on Streamlit Cloud use st.secrets)
load_dotenv()

# Page config
st.set_page_config(
    page_title="Vibescape - Party Playlist Generator",
    page_icon="🎵",
    layout="wide"
)

# ==================== CONFIGURATION ====================
# Cache file paths (used for playlist/genre caching only)
PLAYLIST_CACHE_FILE = "playlist_cache.json"
GENRE_CACHE_FILE = "genre_cache.json"

# Spotify API setup
SCOPE = "playlist-modify-public playlist-modify-private user-library-read"

# ==================== CACHE MANAGEMENT ====================
def load_cache(filename):
    """Load cache from JSON file"""
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cache(filename, data):
    """Save cache to JSON file"""
    with open(filename, 'w') as f:
        json.dump(data, f)

def get_cached_playlists(user_id):
    """Get cached playlist data if still valid (24 hours)"""
    cache = load_cache(PLAYLIST_CACHE_FILE)
    if user_id in cache:
        cached_time = datetime.fromisoformat(cache[user_id]['timestamp'])
        if datetime.now() - cached_time < timedelta(hours=24):
            return cache[user_id]['data']
    return None

def cache_playlists(user_id, data):
    """Cache playlist data"""
    cache = load_cache(PLAYLIST_CACHE_FILE)
    cache[user_id] = {
        'timestamp': datetime.now().isoformat(),
        'data': data
    }
    save_cache(PLAYLIST_CACHE_FILE, cache)

def get_cached_genres(artist_id):
    """Get cached artist genres if still valid (30 days)"""
    cache = load_cache(GENRE_CACHE_FILE)
    if artist_id in cache:
        cached_time = datetime.fromisoformat(cache[artist_id]['timestamp'])
        if datetime.now() - cached_time < timedelta(days=30):
            return cache[artist_id]['genres']
    return None

def cache_genres(artist_id, genres):
    """Cache artist genres"""
    cache = load_cache(GENRE_CACHE_FILE)
    cache[artist_id] = {
        'timestamp': datetime.now().isoformat(),
        'genres': genres
    }
    save_cache(GENRE_CACHE_FILE, cache)

# ==================== SPOTIFY AUTHENTICATION (MULTI-USER SAFE) ====
def ensure_spotify_authenticated():
    CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        st.error("Spotify credentials not set in environment or secrets.")
        st.stop()

    # Ensure each visitor has a unique cache path to prevent using your account
    visitor_id = st.session_state.get('visitor_id')
    if not visitor_id:
        visitor_id = str(int(time.time() * 1000))  # simple unique ID per session
        st.session_state['visitor_id'] = visitor_id

    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        show_dialog=True,
        cache_path=f".cache-{visitor_id}"
    )

    token_info = st.session_state.get("token_info")

    # Refresh if expired
    if token_info and sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            st.session_state["token_info"] = token_info
        except Exception:
            st.session_state.pop("token_info", None)
            token_info = None

    # If no token, try to get it from Spotify redirect
    if not token_info:
        query_params = st.experimental_get_query_params()
        if "code" in query_params:
            code = query_params["code"][0]
            token_info = sp_oauth.get_access_token(code)
            if isinstance(token_info, dict):
                st.session_state["token_info"] = token_info
            st.experimental_set_query_params()  # clean URL
        else:
            auth_url = sp_oauth.get_authorize_url()
            st.markdown("### 🔐 Log in with your Spotify account")
            st.markdown(f"[Login here]({auth_url})")
            st.stop()

    access_token = token_info.get("access_token")
    if not access_token:
        st.error("Failed to get Spotify access token.")
        st.stop()

    sp_client = spotipy.Spotify(auth=access_token)
    st.session_state["spotify_client"] = sp_client
    st.session_state["current_user"] = sp_client.current_user()  # now correct per login


# ==================== DATA GATHERING ====================

def validate_user_exists(sp, username):
    """Check if Spotify user exists and return user data"""
    try:
        user = sp.user(username)
        return True, user
    except Exception:
        return False, None

def get_user_playlists_data(sp, username, market):
    """Gather all tracks from user's public playlists with metadata"""
    # Check cache first
    cached_data = get_cached_playlists(username)
    if cached_data:
        return cached_data

    tracks_data = []

    try:
        # Get user's playlists
        playlists = []
        results = sp.user_playlists(username)
        while results:
            playlists.extend(results['items'])
            if results.get('next'):
                results = sp.next(results)
            else:
                break

        # Filter for public playlists
        public_playlists = [p for p in playlists if p and p.get('public')]

        for playlist in public_playlists:
            if not playlist:
                continue

            # Get all tracks from playlist
            results = sp.playlist_tracks(playlist['id'])
            tracks = results['items']

            while results.get('next'):
                results = sp.next(results)
                tracks.extend(results['items'])

            for item in tracks:
                if not item or not item.get('track'):
                    continue

                track = item['track']

                if not track or not track.get('id'):
                    continue

                # Extract track data
                track_info = {
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [a['name'] for a in track['artists']],
                    'artist_ids': [a['id'] for a in track['artists']],
                    'popularity': track.get('popularity', 0),
                    'explicit': track.get('explicit', False),
                    'album_release_date': track['album'].get('release_date', ''),
                    'url': track['external_urls'].get('spotify', ''),
                    'available_markets': track.get('available_markets', []),
                    'user_id': username,
                    'playlist_name': playlist.get('name', '')
                }

                tracks_data.append(track_info)

        # Cache the results
        cache_playlists(username, tracks_data)

        return tracks_data

    except Exception as e:
        st.error(f"Error fetching playlists for {username}: {str(e)}")
        return []

def get_all_genres_from_tracks(tracks):
    """Extract all unique genres from track data"""
    all_genres = set()
    for track in tracks:
        if 'genres' in track:
            all_genres.update(track['genres'])
    return sorted(list(all_genres))

def get_artist_genres(sp, artist_ids):
    """Fetch genres for multiple artists with caching"""
    genres_map = {}
    artists_to_fetch = []

    # Check cache first
    for artist_id in artist_ids:
        cached_genres = get_cached_genres(artist_id)
        if cached_genres is not None:
            genres_map[artist_id] = cached_genres
        else:
            artists_to_fetch.append(artist_id)

    # Fetch uncached artists in batches of 50
    for i in range(0, len(artists_to_fetch), 50):
        batch = artists_to_fetch[i:i+50]
        try:
            artists_data = sp.artists(batch)
            for artist in artists_data['artists']:
                if artist:
                    genres = artist.get('genres', [])
                    genres_map[artist['id']] = genres
                    cache_genres(artist['id'], genres)
            time.sleep(0.1)  # Rate limit protection
        except Exception as e:
            st.warning(f"Error fetching artist genres: {str(e)}")

    return genres_map

# ==================== FILTERING & RANKING ====================
def parse_release_year(release_date):
    """Extract year from release date string"""
    try:
        return int(release_date.split('-')[0])
    except:
        return None

def filter_tracks(tracks, selected_genres, year_range, popularity_range, market, market_filter_enabled, max_per_artist):
    """Apply all filters to tracks"""
    filtered = []
    artist_count = defaultdict(int)

    for track in tracks:
        # Genre filter
        track_genres = track.get('genres', [])
        if selected_genres and not any(g in track_genres for g in selected_genres):
            continue

        # Year filter
        release_year = parse_release_year(track['album_release_date'])
        if year_range and release_year:
            if not (year_range[0] <= release_year <= year_range[1]):
                continue

        # Popularity filter
        if popularity_range:
            pop = track['popularity']
            if not (popularity_range[0] <= pop <= popularity_range[1]):
                continue

        # Market filter
        if market_filter_enabled and market:
            if market not in track.get('available_markets', []):
                continue

        # Max tracks per artist
        artist_key = tuple(sorted(track['artist_ids']))
        if max_per_artist and artist_count[artist_key] >= max_per_artist:
            continue

        artist_count[artist_key] += 1
        filtered.append(track)

    return filtered

def calculate_track_scores(tracks):
    """Calculate ranking scores for tracks based on cross-user duplicates and popularity"""
    # Count cross-user duplicates
    track_user_counts = defaultdict(set)
    for track in tracks:
        track_user_counts[track['id']].add(track['user_id'])

    # Add scores to tracks
    scored_tracks = []
    for track in tracks:
        score = {
            'cross_user_dup_count': len(track_user_counts[track['id']]),
            'popularity': track['popularity'],
            'release_year': parse_release_year(track['album_release_date']) or 0
        }
        track['score'] = score
        scored_tracks.append(track)

    return scored_tracks

def allocate_tracks(tracks, allocation_mode, num_tracks, user_weights=None):
    """Allocate tracks based on mode (equal or focus)"""
    if not tracks:
        return [], {}

    # Group tracks by user
    user_tracks = defaultdict(list)
    for track in tracks:
        user_tracks[track['user_id']].append(track)

    # Sort each user's tracks by score
    for user in user_tracks:
        user_tracks[user].sort(
            key=lambda t: (t['score']['cross_user_dup_count'],
                          t['score']['popularity'],
                          t['score']['release_year']),
            reverse=True
        )

    selected = []
    used_track_ids = set()
    user_contribution = {user: 0 for user in user_tracks}
    allocation_warnings = []

    if allocation_mode == "Equal":
        # Round-robin allocation
        users = list(user_tracks.keys())
        user_indices = {user: 0 for user in users}
        target_per_user = num_tracks // len(users) if users else 0

        while len(selected) < num_tracks:
            added_this_round = False

            for user in users:
                if len(selected) >= num_tracks:
                    break

                # Find next unused track for this user
                while user_indices[user] < len(user_tracks[user]):
                    track = user_tracks[user][user_indices[user]]
                    user_indices[user] += 1

                    if track['id'] not in used_track_ids:
                        selected.append(track)
                        used_track_ids.add(track['id'])
                        user_contribution[user] += 1
                        added_this_round = True
                        break

            if not added_this_round:
                break  # No more tracks available

        # Check for shortfalls in equal mode
        for user in users:
            contributed = user_contribution[user]
            if contributed < target_per_user:
                shortfall = target_per_user - contributed
                allocation_warnings.append(f"**{user}** contributed only {contributed} tracks (target: {target_per_user}, shortfall: {shortfall})")

    else:  # Focus mode
        if not user_weights:
            user_weights = {user: 1.0 / len(user_tracks) for user in user_tracks}

        # Identify users with 0% weight - they should contribute NOTHING
        zero_weight_users = {user for user, weight in user_weights.items() if weight == 0}
        active_users = {user for user in user_tracks if user not in zero_weight_users}

        # Calculate target tracks per user (only for active users)
        user_targets = {}
        for user in user_tracks:
            if user in zero_weight_users:
                user_targets[user] = 0
            else:
                user_targets[user] = int(num_tracks * user_weights[user])

        user_selected = {user: 0 for user in user_tracks}

        # First pass: allocate target amounts (skip zero-weight users)
        for user in active_users:
            target = user_targets[user]
            available = len([t for t in user_tracks[user] if t['id'] not in used_track_ids])

            for track in user_tracks[user]:
                if user_selected[user] >= target:
                    break
                if track['id'] not in used_track_ids:
                    selected.append(track)
                    used_track_ids.add(track['id'])
                    user_selected[user] += 1
                    user_contribution[user] += 1

            # Check if user met their target
            if user_selected[user] < target:
                shortfall = target - user_selected[user]
                allocation_warnings.append(f"**{user}** contributed only {user_selected[user]} tracks (target: {target}, shortfall: {shortfall})")

        # Fill remaining slots with best available tracks (ONLY from active users)
        remaining_tracks = []
        for user in active_users:  # Exclude zero-weight users
            for track in user_tracks[user]:
                if track['id'] not in used_track_ids:
                    remaining_tracks.append(track)

        remaining_tracks.sort(
            key=lambda t: (t['score']['cross_user_dup_count'],
                          t['score']['popularity'],
                          t['score']['release_year']),
            reverse=True
        )

        for track in remaining_tracks:
            if len(selected) >= num_tracks:
                break
            if track['id'] not in used_track_ids:
                selected.append(track)
                used_track_ids.add(track['id'])
                user_contribution[track['user_id']] += 1

    allocation_info = {
        'user_contribution': user_contribution,
        'warnings': allocation_warnings
    }

    return selected[:num_tracks], allocation_info

def get_top_consensus_tracks(all_tracks, selected_track_ids, limit=10):
    """Get top tracks with high cross-user consensus not in final playlist"""
    # Filter out already selected tracks
    candidate_tracks = [t for t in all_tracks if t['id'] not in selected_track_ids]

    # Only tracks that appear for at least 2 users
    track_user_counts = defaultdict(set)
    for track in candidate_tracks:
        track_user_counts[track['id']].add(track['user_id'])

    consensus_tracks = [t for t in candidate_tracks if len(track_user_counts[t['id']]) >= 2]

    # Deduplicate and sort
    seen = set()
    unique_tracks = []
    for track in consensus_tracks:
        if track['id'] not in seen:
            seen.add(track['id'])
            unique_tracks.append(track)

    unique_tracks.sort(
        key=lambda t: (len(track_user_counts[t['id']]), t['popularity']),
        reverse=True
    )

    return unique_tracks[:limit]

# ==================== UI COMPONENTS ====================

def main():
    st.title("🎵 Vibescape - Party Playlist Generator")
    st.markdown("Create the perfect party playlist from your friends' Spotify music tastes!")

    # Landing page info box
    st.info("""
    💡 **Tip for Best Results:**  
    For a more accurate and personalized mix, guests should temporarily make some of their playlists public. 
    The more public playlists available, the better the playlist will reflect everyone's taste!
    """)

    # Ensure the current visitor is authenticated with Spotify
    ensure_spotify_authenticated()

    # Now safe to use spotify client & current_user from session_state
    sp = st.session_state["spotify_client"]
    current_user = st.session_state["current_user"]

    st.sidebar.success(f"✅ Logged in as: **{current_user.get('display_name', current_user.get('id','Unknown'))}**")

    # ==================== INPUT SECTION ====================

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Guest List")
        guest_input = st.text_area(
            "Spotify usernames (one per line)",
            help="Enter Spotify usernames or profile IDs. Users with few public playlists may want to temporarily make their playlists public for better results.",
            height=150,
            key="guest_input"
        )
        guests = [g.strip() for g in guest_input.split('\n') if g.strip()]

        if guests:
            st.info(f"👥 {len(guests)} guest(s) added")

            # Real-time validation
            if st.button("✓ Validate Usernames", key="validate_btn"):
                with st.spinner("Validating usernames..."):
                    validated_guests = {}
                    for guest in guests:
                        exists, user_data = validate_user_exists(sp, guest)
                        validated_guests[guest] = exists
                    st.session_state.validated_guests = validated_guests

            # Show validation status
            if 'validated_guests' in st.session_state:
                st.markdown("**Validation Status:**")
                for guest in guests:
                    if guest in st.session_state.validated_guests:
                        if st.session_state.validated_guests[guest]:
                            st.markdown(f"✅ {guest}")
                        else:
                            st.markdown(f"❌ {guest} - Not found")
                    else:
                        st.markdown(f"⚪ {guest} - Not validated yet")

    with col2:
        st.subheader("Playlist Settings")
        playlist_name = st.text_input("Playlist name", "Vibescape Playlist")
        num_tracks = st.number_input("Number of tracks", min_value=10, max_value=200, value=40)
        allocation_mode = st.radio("Allocation mode", ["Equal", "Focus"])

        if allocation_mode == "Focus":
            st.info("⚙️ Set weights below after validating guests")

    st.header("2️⃣ Filters")

    # Genre selection - only show if data is gathered
    if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
        all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)
        total_tracks_found = len(st.session_state.all_tracks)
        guest_list = ", ".join(st.session_state.guests)

        if all_genres:
            st.info(f"🎵 Found **{len(all_genres)} unique genres** and **{total_tracks_found} tracks** from: {guest_list}")

    col3, col4, col5 = st.columns(3)

    with col3:
        # Genre multiselect dropdown - populated after data gathering
        if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
            all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)

            if all_genres:
                selected_genres = st.multiselect(
                    "Select genres",
                    options=all_genres,
                    help="Select one or multiple genres from your guests' music. Leave empty to include all genres."
                )
                # Already lowercase from Spotify
            else:
                st.warning("No genres found in guests' playlists")
                selected_genres = []
        else:
            st.info("👆 Validate guests first to see available genres")
            selected_genres = []

    with col4:
        year_filter = st.checkbox("Filter by year", value=False)
        if year_filter:
            year_range = st.slider("Release year range", 1960, 2025, (2018, 2025))
        else:
            year_range = None

    with col5:
        popularity_options = {
            "All (0-100)": (0, 100),
            "Underground (0-33)": (0, 33),
            "Midrange (34-66)": (34, 66),
            "Mainstream (67-100)": (67, 100)
        }
        popularity_choice = st.selectbox("Popularity range", list(popularity_options.keys()))
        popularity_range = popularity_options[popularity_choice]

    market_filter = st.checkbox("Filter by market availability", value=True)
    max_per_artist = st.number_input("Max tracks per artist", min_value=1, max_value=20, value=5)

    # ==================== VALIDATION & DATA GATHERING ====================

    if st.button("🔍 Validate Guests & Gather Data", type="primary"):
        if not guests:
            st.error("Please add at least one guest!")
            st.stop()

        st.session_state.validation_complete = False
        st.session_state.all_tracks = []

        with st.spinner("🔎 We're analyzing you and your friends' music taste — fetching playlists, scanning tracks, and identifying genres. This may take 2–3 minutes. Grab a drink and relax while we work our magic! ☕🎵"):
            progress_bar = st.progress(0)
            status_text = st.empty()

            all_tracks = []
            invalid_users = []
            users_no_playlists = []

            for idx, guest in enumerate(guests):
                status_text.text(f"Processing {guest}...")

                # Validate user exists
                exists, user_data = validate_user_exists(sp, guest)
                if not exists:
                    invalid_users.append(guest)
                    progress_bar.progress((idx + 1) / len(guests))
                    continue

                # Get user's playlist data
                user_market = current_user.get('country', 'US')
                tracks = get_user_playlists_data(sp, guest, user_market)

                if not tracks:
                    users_no_playlists.append(guest)
                else:
                    # Get all unique artist IDs
                    artist_ids = set()
                    for track in tracks:
                        artist_ids.update(track['artist_ids'])

                    # Fetch genres for all artists
                    genres_map = get_artist_genres(sp, list(artist_ids))

                    # Add genres to tracks
                    for track in tracks:
                        track_genres = []
                        for artist_id in track['artist_ids']:
                            track_genres.extend(genres_map.get(artist_id, []))
                        track['genres'] = list(set(track_genres))

                    all_tracks.extend(tracks)

                progress_bar.progress((idx + 1) / len(guests))

            status_text.empty()
            progress_bar.empty()

            # Check for invalid users
            if invalid_users:
                st.error(f"❌ The following Spotify usernames do not exist: {', '.join(invalid_users)}")
                st.error("Please correct the usernames and try again.")
                st.stop()

            # Warn about users with no playlists
            if users_no_playlists:
                st.warning(f"⚠️ No public playlists found for: {', '.join(users_no_playlists)}")

            if not all_tracks:
                st.error("No tracks found across all guests. Please check that guests have public playlists.")
                st.stop()

            st.session_state.all_tracks = all_tracks
            st.session_state.guests = guests
            st.session_state.validation_complete = True

            st.success(f"✅ Successfully gathered {len(all_tracks)} tracks from {len(guests)} guests!")
            st.rerun()  # Force UI refresh to show genre selector

    # ==================== FOCUS MODE WEIGHTS ====================
    if allocation_mode == "Focus" and st.session_state.get('validation_complete', False):
        st.subheader("Focus Mode: Weight Distribution")
        st.info("Adjust how much each guest's music taste influences the playlist. Total must equal 100%.")

        # Initialize weights if not exist or if guest list changed
        if 'user_weight_values' not in st.session_state or set(st.session_state.user_weight_values.keys()) != set(st.session_state.guests):
            equal_weight = (100 // len(st.session_state.guests) // 10) * 10  # Round to nearest 10
            st.session_state.user_weight_values = {guest: equal_weight for guest in st.session_state.guests}
            # Adjust last one to make exactly 100
            remaining = 100 - (equal_weight * (len(st.session_state.guests) - 1))
            st.session_state.user_weight_values[st.session_state.guests[-1]] = remaining

        weights = {}
        total_weight = 0

        for idx, guest in enumerate(st.session_state.guests):
            # Use existing value if available, otherwise use default
            default_value = st.session_state.user_weight_values.get(guest, 0)
            weight = st.slider(
                f"{guest}",
                0,
                100,
                default_value,
                step=10,  # 10% increments
                key=f"weight_{guest}"
            )
            st.session_state.user_weight_values[guest] = weight
            weights[guest] = weight / 100.0
            total_weight += weight

        # Show total and warning if not 100%
        if total_weight == 100:
            st.success(f"✅ Total weight: {total_weight}%")
        else:
            st.error(f"⚠️ Total weight: {total_weight}% - Must equal 100% to generate playlist!")

        st.session_state.user_weights = weights
        st.session_state.weights_valid = (total_weight == 100)

    # ==================== PLAYLIST GENERATION ====================
    if st.session_state.get('validation_complete', False):
        # Check if Focus mode weights are valid
        can_generate = True
        if allocation_mode == "Focus":
            if not st.session_state.get('weights_valid', False):
                can_generate = False
                st.warning("⚠️ Please adjust weights to total 100% before generating playlist")

        if can_generate and st.button("🎨 Generate Playlist", type="primary"):
            all_tracks = st.session_state.all_tracks

            with st.spinner("Filtering and ranking tracks..."):
                # Apply filters
                user_market = current_user.get('country', 'US')
                filtered_tracks = filter_tracks(
                    all_tracks,
                    selected_genres,
                    year_range,
                    popularity_range,
                    user_market,
                    market_filter,
                    max_per_artist
                )

                if not filtered_tracks:
                    st.error("❌ No tracks match your filter criteria.")
                    st.info("💡 Try: widening the year range, selecting different genres, or changing the popularity range")
                    st.stop()

                # Calculate scores
                scored_tracks = calculate_track_scores(filtered_tracks)

                # Allocate tracks
                user_weights = st.session_state.get('user_weights', None) if allocation_mode == "Focus" else None
                selected_tracks, allocation_info = allocate_tracks(
                    scored_tracks,
                    allocation_mode,
                    num_tracks,
                    user_weights
                )

                st.session_state.selected_tracks = selected_tracks
                st.session_state.filtered_tracks = filtered_tracks
                st.session_state.allocation_info = allocation_info

                # Get top consensus tracks
                selected_ids = {t['id'] for t in selected_tracks}
                top_consensus = get_top_consensus_tracks(scored_tracks, selected_ids)
                st.session_state.top_consensus = top_consensus

            st.success("✅ Playlist generated successfully!")

    # ==================== ANALYTICS & PREVIEW ====================
    if 'selected_tracks' in st.session_state:
        st.header("3️⃣ Playlist Preview")

        selected_tracks = st.session_state.selected_tracks
        filtered_tracks = st.session_state.filtered_tracks
        all_tracks = st.session_state.all_tracks
        allocation_info = st.session_state.get('allocation_info', {})

        # Summary analytics
        st.markdown("### 📊 Summary Analytics")

        genre_display = ", ".join(selected_genres) if selected_genres else "All"
        pop_display = f"{popularity_range[0]}–{popularity_range[1]}"
        year_display = f"{year_range[0]}–{year_range[1]}" if year_range else "All"

        metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns(4)
        with metrics_col1:
            st.metric("Friends", len(st.session_state.guests))
            st.metric("Selected Genres", genre_display)
        with metrics_col2:
            st.metric("Popularity", pop_display)
            st.metric("Time Range", year_display)
        with metrics_col3:
            st.metric("Total Candidates", len(all_tracks))
            st.metric("After Filters", len(filtered_tracks))
        with metrics_col4:
            st.metric("Chosen", len(selected_tracks))
            shortfall = num_tracks - len(selected_tracks)
            st.metric("Shortfall", shortfall, delta="Need more tracks" if shortfall > 0 else "Complete")

        # User contribution breakdown
        if 'user_contribution' in allocation_info:
            st.markdown("### 👥 Track Contribution by Guest")
            contrib_cols = st.columns(len(allocation_info['user_contribution']))
            for idx, (user, count) in enumerate(allocation_info['user_contribution'].items()):
                with contrib_cols[idx]:
                    percentage = (count / len(selected_tracks) * 100) if selected_tracks else 0
                    st.metric(user, f"{count} tracks", f"{percentage:.1f}%")

        # Show allocation warnings
        if allocation_info.get('warnings'):
            st.warning("⚠️ **Allocation Notices:**")
            for warning in allocation_info['warnings']:
                st.markdown(f"- {warning}")
            st.info("💡 Remaining slots were filled with the best-ranked tracks from users who had available songs.")

        # Track preview
        st.markdown("### 🎵 Track List")

        if 'tracks_to_remove' not in st.session_state:
            st.session_state.tracks_to_remove = set()

        for idx, track in enumerate(selected_tracks):
            if track['id'] in st.session_state.tracks_to_remove:
                continue

            col_track, col_button = st.columns([5, 1])

            with col_track:
                genres_display = ", ".join(track['genres'][:3]) if track['genres'] else "No genre"
                year = parse_release_year(track['album_release_date'])
                artists_display = ', '.join([a for a in track['artists'] if a]) or "Unknown Artist"

                st.markdown(f"""
                **{idx + 1}. {track['name']}** by {artists_display}  
                `Friend: {track['user_id']}` • `Popularity: {track['popularity']}` • `Year: {year}` • `Genres: {genres_display}`
                """)

            with col_button:
                if st.button("🗑️", key=f"remove_{track['id']}"):
                    st.session_state.tracks_to_remove.add(track['id'])
                    st.rerun()

        # Refill button
        if st.session_state.tracks_to_remove:
            if st.button("🔄 Refill Removed Slots"):
                # Get remaining tracks
                selected_ids = {t['id'] for t in selected_tracks if t['id'] not in st.session_state.tracks_to_remove}
                remaining_tracks = [t for t in filtered_tracks if t['id'] not in selected_ids]
                remaining_tracks.sort(
                    key=lambda t: (t['score']['cross_user_dup_count'],
                                  t['score']['popularity'],
                                  t['score']['release_year']),
                    reverse=True
                )

                # Refill
                num_to_add = len(st.session_state.tracks_to_remove)
                new_tracks = remaining_tracks[:num_to_add]

                # Update selected tracks
                kept_tracks = [t for t in selected_tracks if t['id'] not in st.session_state.tracks_to_remove]
                st.session_state.selected_tracks = kept_tracks + new_tracks
                st.session_state.tracks_to_remove = set()
                st.rerun()

        # Top consensus tracks
        if st.session_state.get('top_consensus'):
            st.markdown("### ⭐ Top Genre Songs (Not in Playlist)")
            st.info("High-consensus tracks that didn't make it into the playlist")

            for track in st.session_state.top_consensus:
                col_consensus, col_add = st.columns([5, 1])

                with col_consensus:
                    genres_display = ", ".join(track['genres'][:3]) if track['genres'] else "No genre"
                    year = parse_release_year(track['album_release_date'])
                    artists_display = ', '.join([a for a in track['artists'] if a]) or "Unknown Artist"

                    st.markdown(f"""
                    **{track['name']}** by {artists_display}  
                    `Consensus: {track['score']['cross_user_dup_count']} users` • `Popularity: {track['popularity']}` • `Year: {year}`
                    """)

                with col_add:
                    if st.button("➕", key=f"add_{track['id']}"):
                        st.session_state.selected_tracks.append(track)
                        st.rerun()

    # ==================== CREATE PLAYLIST ====================
    if 'selected_tracks' in st.session_state:
        st.header("4️⃣ Create Playlist")

        make_public = st.checkbox("Make playlist public", value=False)

        if st.button("💾 Save to Spotify", type="primary"):
            final_tracks = [t for t in st.session_state.selected_tracks if t['id'] not in st.session_state.tracks_to_remove]

            if not final_tracks:
                st.error("No tracks to save!")
                st.stop()

            with st.spinner("Creating playlist on Spotify..."):
                try:
                    # Create playlist under the currently authenticated user
                    sp = st.session_state["spotify_client"]
                    current_user = st.session_state["current_user"]

                    playlist = sp.user_playlist_create(
                        user=current_user['id'],
                        name=playlist_name,
                        public=make_public
                    )

                    # Add tracks in batches of 100
                    track_uris = [f"spotify:track:{t['id']}" for t in final_tracks]
                    skipped = []

                    for i in range(0, len(track_uris), 100):
                        batch = track_uris[i:i+100]
                        try:
                            sp.playlist_add_items(playlist['id'], batch)
                        except Exception as e:
                            skipped.extend(batch)

                    st.success(f"🎉 Playlist '{playlist_name}' created successfully!")
                    st.markdown(f"[Open in Spotify]({playlist['external_urls']['spotify']})")

                    if skipped:
                        st.warning(f"⚠️ {len(skipped)} tracks were unavailable and skipped")

                except Exception as e:
                    st.error(f"Error creating playlist: {str(e)}")

if __name__ == "__main__":
    main()