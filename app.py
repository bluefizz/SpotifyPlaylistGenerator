# app_merged_v11.py
# Merged: v11 features/UI + app.py multi-user-safe authentication (single file, Option A)
# Sources: v11.py and app.py. See file citations in chat.

import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from datetime import datetime, timedelta
import json
from collections import defaultdict
import time
from dotenv import load_dotenv
import random
import math
import glob
import base64  # <- added for cover upload

# Load environment variables (for local dev; on Streamlit Cloud use st.secrets)
load_dotenv()

# ----------------- SMALL UTILS FROM app.py -----------------
def clean_spotify_cache():
    """Delete any existing .cache-* files to force fresh Spotify login"""
    cache_files = glob.glob(".cache-*")
    for f in cache_files:
        try:
            os.remove(f)
            print(f"Deleted old cache file: {f}")
        except Exception as e:
            print(f"Failed to delete {f}: {e}")

# Call this early in your app, before Spotify OAuth
clean_spotify_cache()

# Page config (keep v11 look & feel)
st.set_page_config(
    page_title="Vibescape - Party Playlist Generator",
    page_icon="🎵",
    layout="wide"
)

# ==================== CONFIGURATION ====================
# Cache file paths
PLAYLIST_CACHE_FILE = "playlist_cache.json"
GENRE_CACHE_FILE = "genre_cache.json"

# Spotify API setup - same scope used in v11 & app.py
SCOPE = "ugc-image-upload playlist-modify-public playlist-modify-private user-library-read"

# ==================== CACHE MANAGEMENT (from v11) ====================
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

# ==================== SPOTIFY AUTHENTICATION (from app.py) ====================
def ensure_spotify_authenticated():
    """
    Multi-user-safe authentication. Shows login page if no token.
    Sets:
      - st.session_state['token_info']
      - st.session_state['spotify_client']
      - st.session_state['current_user']
    """
    CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        st.error("Spotify credentials not set in environment or secrets.")
        st.stop()

    # Unique cache per visitor session
    visitor_id = st.session_state.get('visitor_id')
    if not visitor_id:
        visitor_id = str(int(time.time() * 1000))  # simple unique ID
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

    # Refresh token if expired
    if token_info and sp_oauth.is_token_expired(token_info):
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            st.session_state["token_info"] = token_info
        except Exception:
            st.session_state.pop("token_info", None)
            token_info = None

    # If no token, check for Spotify redirect
    if not token_info:
        query_params = st.experimental_get_query_params()
        if "code" in query_params:
            code = query_params["code"][0]
            try:
                token_info = sp_oauth.get_access_token(code)
                # Some spotipy versions return dict, sometimes different object — handle dict case
                if isinstance(token_info, dict):
                    st.session_state["token_info"] = token_info
                st.experimental_set_query_params()  # clean URL
            except Exception as e:
                st.error(f"Error fetching access token: {e}")
                st.stop()
        else:
            auth_url = sp_oauth.get_authorize_url()
            # Dedicated login page (Option A): show only login and stop
            st.markdown("# 🔐 Log in with Spotify")
            st.markdown("To continue, please log in with your Spotify account.")
            st.markdown(f"[Login with Spotify]({auth_url})")
            st.stop()

    # Use access token
    access_token = token_info.get("access_token") if token_info else None
    if not access_token:
        st.error("Failed to get Spotify access token.")
        st.stop()

    # Initialize Spotify client
    sp_client = spotipy.Spotify(auth=access_token)
    st.session_state["spotify_client"] = sp_client

    # Get current user safely
    try:
        current_user = sp_client.current_user()
        st.session_state["current_user"] = current_user
    except spotipy.exceptions.SpotifyException as e:
        st.error(f"Error fetching current user: {e}")
        # Suggest clearing cache
        st.info("💡 Try deleting any `.cache-*` files and logging in again.")
        st.stop()

# ==================== DATA GATHERING (from v11) ====================
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
            if results['next']:
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

# ==================== FILTERING & RANKING (from v11) ====================
def parse_release_year(release_date):
    """Extract year from release date string"""
    try:
        return int(release_date.split('-')[0])
    except:
        return None

def filter_tracks(tracks, selected_genres, year_range, popularity_range,
                  market, market_filter_enabled, max_per_artist):
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
        if year_range:
            if release_year is None:
                continue  # Skip tracks with no year when filter is active
            if not (year_range[0] <= release_year <= year_range[1]):
                continue

        # Popularity filter
        if popularity_range:
            pop = track['popularity']
            if not (popularity_range[0] <= pop <= popularity_range[1]):
                continue

        # Market filter (if enabled)
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

def allocate_tracks(tracks, allocation_mode, num_tracks, user_weights=None, selected_genres=None):
    """
    Allocate tracks based on mode (equal or focus) with v11 genre-distribution logic.
    Returns: selected_tracks, allocation_info, genre_contribution
    """
    if not tracks:
        return [], {}, {}

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
    genre_contribution = defaultdict(int)

    # Genre distribution logic (if multiple selected genres)
    if selected_genres and len(selected_genres) > 1:
        # Try to distribute equally among selected genres
        genre_tracks = defaultdict(list)
        for track in tracks:
            track_genres = track.get('genres', [])
            for genre in selected_genres:
                if genre in track_genres:
                    genre_tracks[genre].append(track)
                    break  # Only count track once

        # Sort tracks within each genre by score
        for genre in genre_tracks:
            genre_tracks[genre].sort(
                key=lambda t: (t['score']['cross_user_dup_count'],
                               t['score']['popularity'],
                               t['score']['release_year']),
                reverse=True
            )

        # Round-robin through genres until filled
        genre_indices = {g: 0 for g in selected_genres}
        while len(selected) < num_tracks:
            added_this_round = False

            for genre in selected_genres:
                if len(selected) >= num_tracks:
                    break

                # Find next unused track for this genre
                while genre_indices[genre] < len(genre_tracks[genre]):
                    track = genre_tracks[genre][genre_indices[genre]]
                    genre_indices[genre] += 1

                    if track['id'] not in used_track_ids:
                        selected.append(track)
                        used_track_ids.add(track['id'])
                        user_contribution[track['user_id']] += 1
                        genre_contribution[genre] += 1
                        added_this_round = True
                        break

            if not added_this_round:
                # Fill remaining with any available tracks
                for track in tracks:
                    if len(selected) >= num_tracks:
                        break
                    if track['id'] not in used_track_ids:
                        selected.append(track)
                        used_track_ids.add(track['id'])
                        user_contribution[track['user_id']] += 1
                        # Track genre for stats
                        track_genres = track.get('genres', [])
                        for genre in selected_genres:
                            if genre in track_genres:
                                genre_contribution[genre] += 1
                                break
                break

    elif allocation_mode == "Equal":
        # Round-robin allocation across users
        users = list(user_tracks.keys())
        if not users:
            return [], {}, {}
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

        # Always show shortfall warnings like v11
        for user in users:
            contributed = user_contribution[user]
            if contributed < target_per_user:
                shortfall = target_per_user - contributed
                allocation_warnings.append(
                    f"**{user}** contributed only {contributed} tracks (target: {target_per_user}, shortfall: {shortfall})"
                )

    else:  # Focus mode
        if not user_weights:
            user_weights = {user: 1.0 / len(user_tracks) for user in user_tracks}

        zero_weight_users = {user for user, weight in user_weights.items() if weight == 0}
        active_users = {user for user in user_tracks if user not in zero_weight_users}

        # Calculate target tracks per user (integer floor)
        user_targets = {}
        for user in user_tracks:
            if user in zero_weight_users:
                user_targets[user] = 0
            else:
                user_targets[user] = int(num_tracks * user_weights.get(user, 0))

        user_selected = {user: 0 for user in user_tracks}

        # First pass: allocate target amounts
        for user in active_users:
            target = user_targets[user]

            for track in user_tracks[user]:
                if user_selected[user] >= target:
                    break
                if track['id'] not in used_track_ids:
                    selected.append(track)
                    used_track_ids.add(track['id'])
                    user_selected[user] += 1
                    user_contribution[user] += 1

            if user_selected[user] < target:
                shortfall = target - user_selected[user]
                allocation_warnings.append(
                    f"**{user}** contributed only {user_selected[user]} tracks (target: {target}, shortfall: {shortfall})"
                )

        # Fill remaining with best available from active users
        remaining_tracks = []
        for user in active_users:
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

    # If genre_contribution empty but selected_genres present, calculate contributions
    if not genre_contribution and selected_genres:
        for track in selected:
            track_genres = track.get('genres', [])
            for genre in selected_genres:
                if genre in track_genres:
                    genre_contribution[genre] += 1
                    break

    # Shuffle the selected tracks to mix user contributions
    random.shuffle(selected[:num_tracks])

    return selected[:num_tracks], allocation_info, dict(genre_contribution)

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

# ==================== GENRE RECOMMENDER (v11 logic) ====================
def get_genre_recommendations(all_tracks, guests):
    """New genre recommendation system with Consensus and Discovery logic (v11)"""
    # Count genres per user and total tracks per user
    user_genres = defaultdict(lambda: defaultdict(int))
    user_total_tracks = defaultdict(int)

    for track in all_tracks:
        user = track['user_id']
        user_total_tracks[user] += 1
        for genre in track.get('genres', []):
            user_genres[user][genre] += 1

    # Calculate proportions
    user_genre_proportions = {}
    for user in user_genres:
        user_genre_proportions[user] = {}
        total = user_total_tracks[user]
        if total > 0:
            for genre, count in user_genres[user].items():
                user_genre_proportions[user][genre] = count / total

    num_users = len(guests)

    # CASES adapted from v11 (keeps v11 behavior)
    if num_users == 1:
        user = guests[0]
        genre_counts = user_genres.get(user, {})
        if not genre_counts:
            return [], [], "No genres found in user's playlists."

        top_5 = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        consensus = [(genre, 1, count, 0) for genre, count in top_5]
        discovery = []
        discovery_message = "Discovery genres require at least 2 guests."

        return consensus, discovery, discovery_message

    if num_users in (2, 3):
        max_users_for_discovery = 1
    else:
        max_users_for_discovery = math.floor(num_users / 2)

    if num_users == 2:
        user1, user2 = guests[0], guests[1]
        all_genres = set(user_genres[user1].keys()) | set(user_genres[user2].keys())
        consensus_scores = []

        for genre in all_genres:
            count1 = user_genres[user1].get(genre, 0)
            count2 = user_genres[user2].get(genre, 0)
            intersection = min(count1, count2)
            if intersection > 0:
                consensus_scores.append((genre, 2, intersection, 0))

        consensus_scores.sort(key=lambda x: x[2], reverse=True)
        consensus = consensus_scores[:5]
        consensus_genres = {g[0] for g in consensus}

        discovery_candidates = []
        for genre in all_genres:
            if genre in consensus_genres:
                continue
            users_with_genre = []
            if genre in user_genres[user1]:
                users_with_genre.append((user1, user_genres[user1][genre]))
            if genre in user_genres[user2]:
                users_with_genre.append((user2, user_genres[user2][genre]))
            if len(users_with_genre) == 1:
                user, count = users_with_genre[0]
                discovery_candidates.append((genre, user, count))

        discovery_candidates.sort(key=lambda x: x[2], reverse=True)

        # v11: apply 2-per-user limit for discovery when num_users == 2
        user_contribution_count = defaultdict(int)
        discovery = []

        for genre, user, count in discovery_candidates:
            if len(discovery) >= 5:
                break
            if user_contribution_count[user] < 2:
                discovery.append((genre, [user], count))
                user_contribution_count[user] += 1

        if len(discovery) < 5:
            for genre, user, count in discovery_candidates:
                if len(discovery) >= 5:
                    break
                if (genre, [user], count) not in discovery:
                    discovery.append((genre, [user], count))

        discovery_message = (
            "No discovery genres found - music tastes are similar."
            if not discovery
            else None
        )

        return consensus, discovery, discovery_message

    # 3+ users
    min_users_required = math.ceil(num_users / 2)
    all_genres = set()
    for user_genre_dict in user_genres.values():
        all_genres.update(user_genre_dict.keys())

    consensus_scores = []
    for genre in all_genres:
        users_with_genre = [u for u in guests if genre in user_genres[u]]
        num_users_with_genre = len(users_with_genre)
        if num_users_with_genre < min_users_required:
            continue
        normalized_user_count = num_users_with_genre / num_users
        proportions = [user_genre_proportions[u][genre] for u in users_with_genre]
        avg_proportion = sum(proportions) / len(proportions)
        consensus_score = 0.9 * normalized_user_count + 0.1 * avg_proportion
        consensus_scores.append((genre, num_users_with_genre, avg_proportion, consensus_score))

    consensus_scores.sort(key=lambda x: x[3], reverse=True)
    consensus = consensus_scores[:5]
    consensus_genres = {g[0] for g in consensus}

    discovery_candidates = []
    for genre in all_genres:
        if genre in consensus_genres:
            continue
        users_with_genre = [u for u in guests if genre in user_genres[u]]
        num_users_with_genre = len(users_with_genre)
        if num_users_with_genre == 0 or num_users_with_genre > max_users_for_discovery:
            continue
        total_count = sum(user_genres[u][genre] for u in users_with_genre)
        discovery_candidates.append((genre, users_with_genre, total_count))

    discovery_candidates.sort(key=lambda x: x[2], reverse=True)

    user_contribution_count = defaultdict(int)
    discovery = []
    for genre, users_list, count in discovery_candidates:
        if len(discovery) >= 5:
            break
        can_add = any(user_contribution_count[u] < 2 for u in users_list)
        if can_add:
            discovery.append((genre, users_list, count))
            for u in users_list:
                user_contribution_count[u] += 1

    if len(discovery) < 5:
        for genre, users_list, count in discovery_candidates:
            if len(discovery) >= 5:
                break
            if (genre, users_list, count) not in discovery:
                discovery.append((genre, users_list, count))

    discovery_message = (
        "No discovery genres found - music tastes are similar."
        if not discovery
        else None
    )

    return consensus, discovery, discovery_message

# ==================== UI (v11 layout, login gating via ensure_spotify_authenticated) ====
def get_display_name(username):
    """Get display name for a username, fallback to username if not found"""
    if 'username_to_display_name' in st.session_state:
        return st.session_state.username_to_display_name.get(username, username)
    return username

def main():
    # Page title and intro (kept from v11)
    st.title("🎵 Vibescape - Party Playlist Generator")
    st.markdown("Create the perfect party playlist from your friends' Spotify music tastes!")

    st.info("""
    💡 **Tip for Best Results:**  
    For a more accurate and personalized mix, guests should temporarily make some of their playlists public. 
    The more public playlists available, the better the playlist will reflect everyone's taste!
    """)

    # --- Authenticate first (dedicated login page shown by ensure_spotify_authenticated when needed) ---
    ensure_spotify_authenticated()

    # After authenticate returns, spotify_client and current_user are available
    sp = st.session_state["spotify_client"]
    current_user = st.session_state["current_user"]

    st.sidebar.success(f"✅ Logged in as: **{current_user.get('display_name', current_user.get('id','Unknown'))}**")

    # ==================== TOP LAYOUT: PARTY SETUP (LEFT) & FILTERS/SETTINGS (RIGHT) ====================
    top_left, top_right = st.columns(2)

    # ---------- LEFT: PARTY SETUP ----------
    with top_left:
        st.subheader("Guest List")
        guest_input = st.text_area(
            "Spotify usernames (one per line)",
            help="Enter Spotify usernames or profile IDs. Users with few public playlists may want to temporarily make their playlists public for better results.",
            height=150,
            key="guest_input"
        )

        # Parse and deduplicate guests
        raw_guests = [g.strip() for g in guest_input.split('\n') if g.strip()]
        guests = []
        seen = set()
        duplicates = []

        for guest in raw_guests:
            guest_lower = guest.lower()
            if guest_lower in seen:
                duplicates.append(guest)
            else:
                seen.add(guest_lower)
                guests.append(guest)

        if duplicates:
            st.warning(f"⚠️ Duplicate usernames removed: {', '.join(duplicates)}")

        if guests:
            st.info(f"👥 {len(guests)} guest(s) added")

            # Real-time validation
            if st.button("✓ Validate Usernames", key="validate_btn"):
                with st.spinner("Validating usernames."):
                    validated_guests = {}
                    username_to_display_name = {}

                    for guest in guests:
                        exists, user_data = validate_user_exists(sp, guest)
                        validated_guests[guest] = {
                            'exists': exists,
                            'data': user_data
                        }
                        if exists and user_data:
                            display_name = user_data.get('display_name', guest)
                            username_to_display_name[guest] = display_name

                    st.session_state.validated_guests = validated_guests
                    st.session_state.username_to_display_name = username_to_display_name

            # Show validation status with profile pictures
            if 'validated_guests' in st.session_state:
                st.markdown("**Validation Status:**")
                for guest in guests:
                    if guest in st.session_state.validated_guests:
                        validation_info = st.session_state.validated_guests[guest]
                        if validation_info['exists']:
                            user_data = validation_info['data']
                            profile_image = user_data.get('images', [{}])[0].get('url', '') if user_data.get('images') else ''

                            col_img, col_text = st.columns([1, 9])
                            with col_img:
                                if profile_image:
                                    st.image(profile_image, width=40)
                                else:
                                    st.write("👤")
                            with col_text:
                                display_name = user_data.get('display_name', guest)
                                st.markdown(f"✅ **{display_name}** (@{guest})")
                        else:
                            st.markdown(f"❌ {guest} - Not found")
                    else:
                        st.markdown(f"⚪ {guest} - Not validated yet")

        st.markdown("---")
        if guests and st.button("🔍 Scan Playlists and Gather Music Taste", type="primary", key="gather_data_btn"):
            if not guests:
                st.error("Please add at least one guest!")
                st.stop()

            st.session_state.validation_complete = False
            st.session_state.all_tracks = []

            with st.spinner("🔎 We're analyzing you and your friends' music taste — fetching playlists, scanning tracks, and identifying genres. Depending on the number of friends on the guest list and the amount of tracks they have in public playlists, this may take a few minutes. Grab a drink and relax while we work our magic! ☕🎵"):
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
                    st.info("💡 **Note:** Users without public playlists cannot contribute their music taste to the playlist creation. Please ask them to make some playlists public temporarily.")

                if not all_tracks:
                    st.error("No tracks found across all guests. Please check that guests have public playlists.")
                    st.stop()

                st.session_state.all_tracks = all_tracks
                st.session_state.guests = guests
                st.session_state.validation_complete = True

                # Build display name mapping if not already done
                if 'username_to_display_name' not in st.session_state:
                    username_to_display_name = {}
                    for guest in guests:
                        if 'validated_guests' in st.session_state and guest in st.session_state.validated_guests:
                            user_data = st.session_state.validated_guests[guest].get('data')
                            if user_data:
                                username_to_display_name[guest] = user_data.get('display_name', guest)
                            else:
                                username_to_display_name[guest] = guest
                        else:
                            username_to_display_name[guest] = guest
                    st.session_state.username_to_display_name = username_to_display_name

                st.success(f"✅ Successfully gathered {len(all_tracks)} tracks from {len(guests)} guests!")
                st.rerun()  # Force UI refresh to show genre selector

    # ---------- RIGHT: FILTERS & SETTINGS ----------
    with top_right:
        st.header("Filters & Settings")

        # Defaults for later analytics (in case user hasn't generated yet)
        selected_genres = []
        year_range = None
        popularity_range = (0, 100)
        max_per_artist = 5

        # Genre selection - only show if data is gathered
        if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
            all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)
            total_tracks_found = len(st.session_state.all_tracks)
            guest_display_names = [get_display_name(g) for g in st.session_state.guests]
            guest_list = ", ".join(guest_display_names)

            if all_genres:
                st.info(f"🎵 Found **{len(all_genres)} unique genres** and **{total_tracks_found} tracks** from: {guest_list}")

        col3, col4 = st.columns([2, 1])

        with col3:
            # Genre multiselect dropdown - populated after data gathering
            if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
                all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)

                if all_genres:
                    # Get genre recommendations
                    consensus, discovery, discovery_message = get_genre_recommendations(
                        st.session_state.all_tracks,
                        st.session_state.guests
                    )

                    # Display Consensus Genres
                    if consensus:
                        st.markdown("**🎯 Consensus Top 5 Genres**")
                        for idx, item in enumerate(consensus, 1):
                            genre = item[0]
                            num_users = item[1]
                            st.markdown(f"{idx}. **{genre}** ({num_users} users)")
                        st.markdown("---")

                    # Display Discovery Genres - always show the section for 2+ users
                    if len(st.session_state.guests) >= 2:
                        st.markdown("**🔍 Discovery Genres**")
                        if discovery:
                            for idx, item in enumerate(discovery, 1):
                                genre = item[0]
                                users = item[1]
                                user_display_names = [get_display_name(u) for u in users]
                                user_list = ", ".join(user_display_names)
                                st.markdown(f"{idx}. **{genre}** (from {user_list})")
                        elif discovery_message:
                            st.info(discovery_message)
                        st.markdown("---")

                    selected_genres = st.multiselect(
                        "Select genres for your playlist",
                        options=all_genres,
                        help="Select one or multiple genres from your guests' music. Leave empty to include all genres."
                    )
                else:
                    st.warning("No genres found in guests' playlists")
                    selected_genres = []
            else:
                st.info("👆 Validate guests first to see available genres")
                selected_genres = []

        with col4:
            st.markdown("**Playlist Settings**")
            playlist_name = st.text_input("Playlist name", "Vibescape Playlist", label_visibility="collapsed")

            # ✅ optional playlist cover uploader
            playlist_image = st.file_uploader(
                "Upload playlist cover (optional, JPG only)",
                type=["jpg", "jpeg"]
            )

            num_tracks = st.number_input("Number of tracks", min_value=10, max_value=200, value=40)
            allocation_mode = st.radio("Allocation mode", ["Equal", "Focus"])

            if allocation_mode == "Focus":
                st.info("⚙️ Set weights below")

        st.markdown("---")

        col5, col6, col7 = st.columns(3)
        with col5:
            year_filter = st.checkbox("Filter by year", value=False)
            if year_filter:
                year_range = st.slider("Release year range", 1960, 2025, (2018, 2025))
            else:
                year_range = None

        with col6:
            popularity_options = {
                "All (0-100)": (0, 100),
                "Underground (0-33)": (0, 33),
                "Midrange (34-66)": (34, 66),
                "Mainstream (67-100)": (67, 100)
            }
            popularity_choice = st.selectbox("Popularity range", list(popularity_options.keys()))
            popularity_range = popularity_options[popularity_choice]

        with col7:
            market_filter = st.checkbox("Filter by market availability", value=True)
            max_per_artist = st.number_input("Max tracks per artist", min_value=1, max_value=20, value=5)

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
        else:
            st.session_state.weights_valid = True  # for Equal mode

        # ==================== VALIDATE & GENERATE ====================
        if st.session_state.get('validation_complete', False):
            can_generate = True
            if allocation_mode == "Focus" and not st.session_state.get('weights_valid', False):
                can_generate = False
                st.warning("⚠️ Please adjust weights to total 100% before generating playlist")

            if can_generate and st.button("🎨 Generate Playlist", type="primary"):
                all_tracks = st.session_state.all_tracks

                with st.spinner("Filtering and ranking tracks..."):
                    filtered_tracks = filter_tracks(
                        all_tracks,
                        selected_genres,
                        year_range,
                        popularity_range,
                        current_user.get('country', 'US'),
                        market_filter,
                        max_per_artist
                    )

                    if not filtered_tracks:
                        st.error("❌ No tracks match your filter criteria.")
                        st.info("💡 Try: widening the year range, selecting different genres, or changing the popularity range")
                        st.stop()

                    # Calculate scores
                    scored_tracks = calculate_track_scores(filtered_tracks)

                    # Allocate
                    user_weights = st.session_state.get('user_weights', None) if allocation_mode == "Focus" else None
                    selected_tracks, allocation_info, genre_contribution = allocate_tracks(
                        scored_tracks,
                        allocation_mode,
                        num_tracks,
                        user_weights,
                        selected_genres
                    )

                    st.session_state.selected_tracks = selected_tracks
                    st.session_state.filtered_tracks = scored_tracks
                    st.session_state.allocation_info = allocation_info
                    st.session_state.genre_contribution = genre_contribution

                    if 'tracks_to_remove' not in st.session_state:
                        st.session_state.tracks_to_remove = set()

                    selected_ids = {t['id'] for t in selected_tracks}
                    top_consensus = get_top_consensus_tracks(scored_tracks, selected_ids)
                    st.session_state.top_consensus = top_consensus

                st.success("✅ Playlist generated successfully!")

    # ==================== SUMMARY ANALYTICS & LIST (from v11) ====================
    if 'selected_tracks' in st.session_state:
        st.markdown("---")
        st.header("📊 Summary Analytics")

        selected_tracks = st.session_state.selected_tracks
        filtered_tracks = st.session_state.filtered_tracks
        all_tracks = st.session_state.all_tracks
        allocation_info = st.session_state.get('allocation_info', {})
        genre_contribution = st.session_state.get('genre_contribution', {})

        # Note: selected_genres, popularity_range, year_range come from above scope
        genre_display = ", ".join(selected_genres) if selected_genres else "All"
        pop_display = f"{popularity_range[0]}–{popularity_range[1]}"
        year_display = f"{year_range[0]}–{year_range[1]}" if year_range else "All"

        metrics_col1, metrics_col2, metrics_col3, metrics_col4, metrics_col5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])

        with metrics_col1:
            st.markdown("**Track Contribution by Guest**")
            if 'user_contribution' in allocation_info and allocation_info['user_contribution']:
                for user, count in allocation_info['user_contribution'].items():
                    percentage = (count / len(selected_tracks) * 100) if selected_tracks else 0
                    st.markdown(f"**{get_display_name(user)}**  \n{count} tracks • {percentage:.1f}%")
            else:
                st.markdown("No track contributions available.")

            st.markdown("---")

            # ✅ checkbox: default = private
            make_public = st.checkbox("Make playlist public", value=False, key="make_public")

            col_save, col_refill = st.columns(2)

            with col_save:
                if st.button("💾 Save Playlist to Spotify", type="primary", key="save_playlist_btn"):
                    final_tracks = [
                        t for t in st.session_state.selected_tracks
                        if t['id'] not in st.session_state.get('tracks_to_remove', set())
                    ]

                    if not final_tracks:
                        st.error("No tracks to save!")
                    else:
                        with st.spinner("Creating playlist on Spotify..."):
                            try:
                                # 🔒 Create playlist PRIVATE by default
                                playlist = sp.user_playlist_create(
                                    user=current_user['id'],
                                    name=playlist_name,
                                    public=False  # always start as private
                                )

                                # If user checked "Make playlist public" → flip to public
                                if make_public:
                                    try:
                                        sp.playlist_change_details(
                                            playlist_id=playlist['id'],
                                            public=True
                                        )
                                    except Exception as e:
                                        st.error(f"Failed to set playlist public: {e}")

                                # Add tracks
                                track_uris = [f"spotify:track:{t['id']}" for t in final_tracks]
                                skipped = []

                                for i in range(0, len(track_uris), 100):
                                    batch = track_uris[i:i+100]
                                    try:
                                        sp.playlist_add_items(playlist['id'], batch)
                                    except Exception:
                                        skipped.extend(batch)

                                # ✅ Upload playlist cover if user uploaded an image
                                if playlist_image is not None:
                                    try:
                                        image_bytes = playlist_image.getvalue()
                                        encoded_image = base64.b64encode(image_bytes).decode("utf-8")
                                        sp.playlist_upload_cover_image(playlist['id'], encoded_image)
                                        st.success("🖼️ Custom playlist cover uploaded!")
                                    except Exception as e:
                                        st.error(f"Failed to upload playlist cover: {e}")
                                else:
                                    st.info("No cover image uploaded → Spotify will generate the default one.")

                                st.success(f"🎉 Playlist '{playlist_name}' created successfully!")
                                try:
                                    st.markdown(f"[Open in Spotify]({playlist['external_urls']['spotify']})")
                                except Exception:
                                    pass

                                if skipped:
                                    st.warning(f"⚠️ {len(skipped)} tracks were unavailable and skipped")

                            except Exception as e:
                                st.error(f"Error creating playlist: {str(e)}")

            with col_refill:
                if st.session_state.get('tracks_to_remove'):
                    if st.button("🔄 Refill Removed Slots", key="refill_slots_btn"):
                        permanently_removed = st.session_state.tracks_to_remove.copy()
                        all_selected_ids = {t['id'] for t in selected_tracks}
                        remaining_tracks = [
                            t for t in filtered_tracks
                            if t['id'] not in all_selected_ids and t['id'] not in permanently_removed
                        ]
                        remaining_tracks.sort(
                            key=lambda t: (t['score']['cross_user_dup_count'],
                                           t['score']['popularity'],
                                           t['score']['release_year']),
                            reverse=True
                        )
                        num_to_add = len(st.session_state.tracks_to_remove)
                        new_tracks = remaining_tracks[:num_to_add]
                        kept_tracks = [t for t in selected_tracks if t['id'] not in st.session_state.tracks_to_remove]
                        st.session_state.selected_tracks = kept_tracks + new_tracks
                        st.session_state.tracks_to_remove = set()
                        if 'display_order' in st.session_state:
                            del st.session_state.display_order
                        st.rerun()

        with metrics_col2:
            st.metric("Friends", len(st.session_state.guests))
            st.metric("Selected Genres", genre_display)

        with metrics_col3:
            st.metric("Popularity", pop_display)
            st.metric("Time Range", year_display)

        with metrics_col4:
            st.metric("Total Candidates", len(all_tracks))
            st.metric("After Filters", len(filtered_tracks))

        with metrics_col5:
            st.metric("Chosen", len(selected_tracks))
            shortfall = num_tracks - len(selected_tracks)
            st.metric("Shortfall", shortfall, delta="Need more tracks" if shortfall > 0 else "Complete")

        if allocation_info.get('warnings'):
            st.warning("⚠️ **Allocation Notices:**")
            for warning in allocation_info['warnings']:
                st.markdown(f"- {warning}")
            st.info("💡 Remaining slots were filled with the best-ranked tracks from users who had available songs.")

        # Track preview + remove / refill logic (v11)
        st.markdown("### 🎵 Track List")
        if 'tracks_to_remove' not in st.session_state:
            st.session_state.tracks_to_remove = set()

        # Maintain display order if user toggles adding/removing
        if 'display_order' not in st.session_state:
            st.session_state.display_order = list(range(len(st.session_state.selected_tracks)))

        for display_idx, idx in enumerate(st.session_state.display_order):
            if idx >= len(st.session_state.selected_tracks):
                continue
            track = st.session_state.selected_tracks[idx]
            if track['id'] in st.session_state.tracks_to_remove:
                continue

            col_track, col_button = st.columns([5, 1])
            with col_track:
                genres_display = ", ".join(track.get('genres', [])[:3]) if track.get('genres') else "No genre"
                year = parse_release_year(track.get('album_release_date', ''))
                artists_display = ', '.join([a for a in track.get('artists', []) if a]) or "Unknown Artist"
                friend_display_name = get_display_name(track.get('user_id', 'Unknown'))

                actual_position = display_idx + 1
                st.markdown(f"""
                    **{actual_position}. {track['name']}** by {artists_display}  
                    `Friend: {friend_display_name}` • `Popularity: {track['popularity']}` • `Year: {year}` • `Genres: {genres_display}`
                    """)

            with col_button:
                if st.button("🗑️", key=f"remove_{track['id']}_{display_idx}"):
                    st.session_state.tracks_to_remove.add(track['id'])
                    if 'display_order' in st.session_state:
                        del st.session_state.display_order
                    st.rerun()

        # ---------- RIGHT: TOP GENRE SONGS / CONSENSUS TRACKS ----------
        bottom_left, bottom_right = st.columns([3, 2])
        with bottom_right:
            st.subheader("⭐ Top Consensus Songs (Not in the Playlist)")

            current_selected_ids = {
                t['id'] for t in selected_tracks
                if t['id'] not in st.session_state.get('tracks_to_remove', set())
            }

            if 'filtered_tracks' in st.session_state:
                scored_tracks = st.session_state.filtered_tracks
                top_consensus = get_top_consensus_tracks(scored_tracks, current_selected_ids)
                st.session_state.top_consensus = top_consensus

            if st.session_state.get('top_consensus'):
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
                            if 'display_order' in st.session_state:
                                del st.session_state.display_order
                            st.rerun()
            else:
                st.info("No additional consensus tracks found that aren't already in the playlist.")

if __name__ == "__main__":
    main()