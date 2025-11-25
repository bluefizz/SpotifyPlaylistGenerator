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
import re
import glob

# NEW: for custom HTML/JS (copy-to-clipboard)
import streamlit.components.v1 as components

#for cover upload PNG support 
import base64
from io import BytesIO
from PIL import Image

# Load environment variables
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

# Page config 
st.set_page_config(
   # page_title="CrowdSync - Party Playlist Generator",
   # page_icon="🎵",
    layout="wide"
)

# ==== Blue UI (STREAMLIT) ====
st.markdown("""
<style>
:root {
    --primary-color: #0E6EFF;
    --accent-color: #4DB8FF;
}

.stButton>button {
    background-color: var(--primary-color) !important;
    color: white !important;
    border-radius: 8px;
    border: none;
    transition: 0.2s ease;
}

.stButton>button:hover {
    background-color: var(--accent-color) !important;
    transform: translateY(-2px);
    box-shadow: 0px 0px 12px rgba(14,110,255,0.5);
}

.streamlit-expanderHeader {
    color: var(--primary-color) !important;
}

.css-1n76uvr, .css-1v3fvcr, .css-ffhzg2 {
    color: var(--primary-color) !important;
}

.stProgress > div > div > div > div {
    background-color: var(--primary-color) !important;
}

.stAlert {
    border-left: 4px solid var(--primary-color) !important;
    border-radius: 6px;
}

</style>
""", unsafe_allow_html=True)


def get_logo_base64():
    with open("crowdsync.png", "rb") as f:
        return base64.b64encode(f.read()).decode()

logo_base64 = get_logo_base64()

st.markdown(
    f"""
    <div style="
        position: absolute;
        top: -20px;
        left: 15px;
        z-index: 9999;
    ">
        <img src="data:image/png;base64,{logo_base64}" style="width: 350px;">
    </div>
    <div style="height: 60px;"></div>
    """,
    unsafe_allow_html=True
)

# ==================== CONFIGURATION ====================
# Cache file paths
PLAYLIST_CACHE_FILE = "playlist_cache.json"
GENRE_CACHE_FILE = "genre_cache.json"

# Spotify API setup - 
SCOPE = "ugc-image-upload playlist-modify-public playlist-modify-private user-library-read"

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

        
    return sp_client, current_user
# ==================== DATA GATHERING ====================

def extract_username_from_url(url):
    """Extract username from Spotify profile URL"""
    match = re.search(r'/user/([^?/\s]+)', url)
    if match:
        return match.group(1)
    return None

def validate_user_exists(sp, username):
    """Check if Spotify user exists and return user data"""
    try:
        user = sp.user(username)
        return True, user
    except Exception as e:
        return False, None

def get_genre_recommendations(all_tracks, guests):
    """New genre recommendation system with Consensus and Discovery logic"""
    
    user_genres = defaultdict(lambda: defaultdict(int))
    user_total_tracks = defaultdict(int)
    
    for track in all_tracks:
        user = track['user_id']
        user_total_tracks[user] += 1
        for genre in track.get('genres', []):
            user_genres[user][genre] += 1
    
    user_genre_proportions = {}
    for user in user_genres:
        user_genre_proportions[user] = {}
        total = user_total_tracks[user]
        if total > 0:
            for genre, count in user_genres[user].items():
                user_genre_proportions[user][genre] = count / total
    
    num_users = len(guests)
    
    if num_users == 1:
        user = guests[0]
        genre_counts = user_genres.get(user, {})
        if not genre_counts:
            return [], [], "No genres found in user's playlists."
        
        top_5 = sorted(genre_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        consensus = [(genre, 1, count, 0) for genre, count in top_5 if count > 0]
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
                users_with_genre = sum([1 for u in [user1, user2] if user_genres[u].get(genre, 0) > 0])
                consensus_scores.append((genre, users_with_genre, intersection, 0))
        
        consensus_scores.sort(key=lambda x: x[2], reverse=True)
        consensus = consensus_scores[:5]
        consensus_genres = {g[0] for g in consensus}
        
        discovery_candidates = []
        for genre in all_genres:
            if genre in consensus_genres:
                continue
            
            users_with_genre = []
            if user_genres[user1].get(genre, 0) > 0:
                users_with_genre.append((user1, user_genres[user1][genre]))
            if user_genres[user2].get(genre, 0) > 0:
                users_with_genre.append((user2, user_genres[user2][genre]))
            
            if len(users_with_genre) == 1:
                user, count = users_with_genre[0]
                discovery_candidates.append((genre, user, count))
        
        discovery_candidates.sort(key=lambda x: x[2], reverse=True)
        
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
        
        discovery_message = "No discovery genres found - music tastes are similar." if not discovery else None
        
        return consensus, discovery, discovery_message
    
    min_users_required = math.ceil(num_users / 2)
    all_genres = set()
    for user_genre_dict in user_genres.values():
        all_genres.update(user_genre_dict.keys())
    
    consensus_scores = []
    for genre in all_genres:
        users_with_genre = [u for u in guests if user_genres[u].get(genre, 0) > 0]
        num_users_with_genre = len(users_with_genre)
        
        if num_users_with_genre < min_users_required:
            continue
        
        normalized_user_count = num_users_with_genre / num_users
        proportions = [user_genre_proportions[u][genre] for u in users_with_genre]
        avg_proportion = sum(proportions) / len(proportions) if proportions else 0
        consensus_score = 0.9 * normalized_user_count + 0.1 * avg_proportion
        
        consensus_scores.append((genre, num_users_with_genre, avg_proportion, consensus_score))
    
    consensus_scores.sort(key=lambda x: x[3], reverse=True)
    consensus = consensus_scores[:5]
    consensus_genres = {g[0] for g in consensus}
    
    discovery_candidates = []
    for genre in all_genres:
        if genre in consensus_genres:
            continue
        
        users_with_genre = [u for u in guests if user_genres[u].get(genre, 0) > 0]
        num_users_with_genre = len(users_with_genre)
        
        if num_users_with_genre == 0 or num_users_with_genre > max_users_for_discovery:
            continue
        
        total_count = sum(user_genres[u][genre] for u in users_with_genre)
        
        if total_count > 0:
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
    
    discovery_message = "No discovery genres found - music tastes are similar." if not discovery else None
    
    return consensus, discovery, discovery_message

def get_user_playlists_data(sp, username, market):
    """Gather all tracks from user's public playlists - NO CACHING"""
    
    tracks_data = []
    
    try:
        playlists = []
        results = sp.user_playlists(username)
        while results:
            playlists.extend(results['items'])
            if results['next']:
                results = sp.next(results)
            else:
                break
        
        public_playlists = [p for p in playlists if p and p['public']]
        
        for playlist in public_playlists:
            if not playlist:
                continue
                
            results = sp.playlist_tracks(playlist['id'])
            tracks = results['items']
            
            while results['next']:
                results = sp.next(results)
                tracks.extend(results['items'])
            
            for item in tracks:
                if not item or not item['track']:
                    continue
                    
                track = item['track']
                
                if not track or not track['id']:
                    continue
                
                track_info = {
                    'id': track['id'],
                    'name': track['name'],
                    'artists': [a['name'] for a in track['artists']],
                    'artist_ids': [a['id'] for a in track['artists']],
                    'popularity': track['popularity'],
                    'explicit': track['explicit'],
                    'album_release_date': track['album']['release_date'],
                    'url': track['external_urls']['spotify'],
                    'available_markets': track.get('available_markets', []),
                    'user_id': username,
                    'playlist_name': playlist['name']
                }
                
                tracks_data.append(track_info)
        
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
    """Fetch genres for multiple artists - NO CACHING"""
    genres_map = {}
    
    for i in range(0, len(artist_ids), 50):
        batch = artist_ids[i:i+50]
        try:
            artists_data = sp.artists(batch)
            for artist in artists_data['artists']:
                if artist:
                    genres = artist.get('genres', [])
                    genres_map[artist['id']] = genres
            time.sleep(0.1)
        except Exception as e:
            st.warning(f"Error fetching artist genres: {str(e)}")
    
    return genres_map

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
        track_genres = track.get('genres', [])
        if selected_genres and not any(g in track_genres for g in selected_genres):
            continue
        
        release_year = parse_release_year(track['album_release_date'])
        if year_range:
            if release_year is None:
                continue
            if not (year_range[0] <= release_year <= year_range[1]):
                continue
        
        if popularity_range:
            pop = track['popularity']
            if not (popularity_range[0] <= pop <= popularity_range[1]):
                continue
        
        if market_filter_enabled and market:
            if market not in track.get('available_markets', []):
                continue
        
        artist_key = tuple(sorted(track['artist_ids']))
        if max_per_artist and artist_count[artist_key] >= max_per_artist:
            continue
        
        artist_count[artist_key] += 1
        filtered.append(track)
    
    return filtered

def allocate_tracks(tracks, allocation_mode, num_tracks, user_weights=None, selected_genres=None):
    """
    NEW allocation logic:

    ✔ Multi-genre: 
        - User fairness = highest priority
        - Each user gets (target_per_user / #genres) per genre (rounded)
        - If a user lacks a genre: their quota goes into a global genre pool
        - Pool tracks get redistributed fairly to users who have the genre
        - Users without ANY selected genre contribute 0.

    ✔ Single-genre:
        - Fixes the bug where fewer tracks than requested (e.g., 33/40) were produced
        - Phase 1: give each user their target tracks (equal or weighted)
        - Phase 2: ALWAYS fill to exact num_tracks using remaining valid tracks
        - Now also returns correct genre_contribution for 1 selected genre.
    """

    import random
    from collections import defaultdict

    if not tracks:
        return [], {"user_contribution": {}, "warnings": []}, {}

    # Determine selected genres
    if selected_genres:
        genre_list = list(selected_genres)
    else:
        # Auto-detect all genres if none selected
        all_g = set()
        for t in tracks:
            for g in t.get("genres", []):
                all_g.add(g)
        genre_list = sorted(all_g)

    multi_genre = len(genre_list) > 1

    # ----------------------- GROUP TRACKS BY USER & GENRE -----------------------
    user_genre_tracks = defaultdict(lambda: defaultdict(list))
    user_other_tracks = defaultdict(list)
    users_set = set()

    for track in tracks:
        u = track["user_id"]
        users_set.add(u)

        genres = track.get("genres", []) or []
        present = [g for g in genre_list if g in genres]

        # assign track to a primary genre bucket if possible
        if multi_genre and present:
            primary = present[0]
            user_genre_tracks[u][primary].append(track)
        else:
            user_other_tracks[u].append(track)

    users = sorted(list(users_set))

    # ----------------------- DETERMINE USER TARGETS -----------------------
    user_targets = {u: 0 for u in users}

    if allocation_mode == "Focus" and user_weights:
        weights = {u: max(0.0, float(user_weights.get(u, 0))) for u in users}
        total_w = sum(weights.values())
        if total_w <= 0:
            allocation_mode = "Equal"
        else:
            assigned = 0
            fractional = []
            for u in users:
                exact = num_tracks * (weights[u] / total_w)
                base = int(exact)
                user_targets[u] = base
                assigned += base
                fractional.append((exact - base, u))
            leftover = num_tracks - assigned
            fractional.sort(reverse=True)
            for _, u in fractional[:leftover]:
                user_targets[u] += 1

    if allocation_mode == "Equal":
        if len(users) > 0:
            base = num_tracks // len(users)
            remainder = num_tracks % len(users)
            for u in users:
                user_targets[u] = base
            for u in users[:remainder]:
                user_targets[u] += 1

    # ----------------------- MULTI-GENRE LOGIC -----------------------
    selected_tracks = []
    used_ids = set()
    user_contrib = {u: 0 for u in users}
    genre_contrib = defaultdict(int)

    if multi_genre:
        G = len(genre_list)

        # -------- PER-USER GENRE DISTRIBUTION --------
        for u in users:
            target = user_targets[u]
            if target <= 0:
                continue

            # total tracks user has in selected genres
            available = sum(len(user_genre_tracks[u][g]) for g in genre_list)
            available += len(user_other_tracks[u])

            effective_target = min(target, available)
            if effective_target <= 0:
                continue

            base_pg = effective_target // G
            rem_pg = effective_target % G

            desired_pg = {g: base_pg for g in genre_list}
            for g in genre_list[:rem_pg]:
                desired_pg[g] += 1

            # ----- Try satisfying each genre quota -----
            for g in genre_list:
                need = desired_pg[g]
                bucket = user_genre_tracks[u][g]
                while need > 0 and bucket:
                    t = bucket.pop()
                    if t["id"] in used_ids:
                        continue
                    selected_tracks.append(t)
                    used_ids.add(t["id"])
                    user_contrib[u] += 1
                    genre_contrib[g] += 1
                    need -= 1

            remaining = effective_target - user_contrib[u]

            # Fill remaining from any genre bucket
            for g in genre_list:
                if remaining <= 0:
                    break
                bucket = user_genre_tracks[u][g]
                while remaining > 0 and bucket:
                    t = bucket.pop()
                    if t["id"] in used_ids:
                        continue
                    selected_tracks.append(t)
                    used_ids.add(t["id"])
                    user_contrib[u] += 1
                    genre_contrib[g] += 1
                    remaining -= 1

            # Fill remaining from non-genre tracks
            bucket_other = user_other_tracks[u]
            while remaining > 0 and bucket_other:
                t = bucket_other.pop()
                if t["id"] in used_ids:
                    continue
                selected_tracks.append(t)
                used_ids.add(t["id"])
                user_contrib[u] += 1
                remaining -= 1

        # ---------- PHASE 2: GLOBAL FILL ----------
        if len(selected_tracks) < num_tracks:
            global_pool = []
            for u in users:
                for g in genre_list:
                    global_pool.extend(
                        [t for t in user_genre_tracks[u][g] if t["id"] not in used_ids]
                    )
                global_pool.extend(
                    [t for t in user_other_tracks[u] if t["id"] not in used_ids]
                )

            random.shuffle(global_pool)

            while len(selected_tracks) < num_tracks and global_pool:
                t = global_pool.pop()
                if t["id"] in used_ids:
                    continue
                selected_tracks.append(t)
                used_ids.add(t["id"])
                user_contrib[t["user_id"]] += 1
                
                gs = t.get("genres", []) or []
                for g in genre_list:
                    if g in gs:
                        genre_contrib[g] += 1
                        break

        warnings = []
        total_sel = len(selected_tracks)
        effective_users = [u for u in users if user_contrib[u] > 0]
        if effective_users:
            ideal = total_sel / len(effective_users)
            for u in effective_users:
                if abs(user_contrib[u] - ideal) >= 2:
                    warnings.append(
                        f"**{u}** contributed {user_contrib[u]} tracks (ideal ≈ {ideal:.1f})"
                    )

        allocation_info = {
            "user_contribution": user_contrib,
            "warnings": warnings
        }

        random.shuffle(selected_tracks)
        if len(selected_tracks) > num_tracks:
            selected_tracks = selected_tracks[:num_tracks]

        return selected_tracks, allocation_info, dict(genre_contrib)

    # ----------------------- SINGLE-GENRE LOGIC (FIXED) -----------------------
    user_tracks = defaultdict(list)
    for t in tracks:
        user_tracks[t["user_id"]].append(t)

    for u in user_tracks:
        random.shuffle(user_tracks[u])

    used_ids = set()
    selected_tracks = []
    user_contrib = {u: 0 for u in user_tracks}
    warnings = []

    # EQUAL MODE
    if allocation_mode == "Equal":
        users_eq = list(user_tracks.keys())
        m = len(users_eq)
        if m > 0:
            base = num_tracks // m
            remainder = num_tracks % m

            per_user_targets = {u: base for u in users_eq}
            for u in users_eq[:remainder]:
                per_user_targets[u] += 1

            user_idx = {u: 0 for u in users_eq}

            # Phase 1: try hitting per-user targets
            for u in users_eq:
                need = per_user_targets[u]
                bucket = user_tracks[u]

                while need > 0 and user_idx[u] < len(bucket):
                    t = bucket[user_idx[u]]
                    user_idx[u] += 1
                    if t["id"] in used_ids:
                        continue
                    selected_tracks.append(t)
                    used_ids.add(t["id"])
                    user_contrib[u] += 1
                    need -= 1

                if need > 0:
                    warnings.append(
                        f"**{u}** could only contribute {user_contrib[u]} of {per_user_targets[u]} expected tracks."
                    )

            # Phase 2: global fill
            if len(selected_tracks) < num_tracks:
                pool = []
                for u in users_eq:
                    for t in user_tracks[u][user_idx[u]:]:
                        if t["id"] not in used_ids:
                            pool.append(t)

                random.shuffle(pool)

                while len(selected_tracks) < num_tracks and pool:
                    t = pool.pop()
                    if t["id"] in used_ids:
                        continue
                    selected_tracks.append(t)
                    used_ids.add(t["id"])
                    user_contrib[t["user_id"]] += 1

    else:
        # FOCUS MODE
        if not user_weights:
            user_weights = {u: 1.0 / len(user_tracks) for u in user_tracks}

        active = [u for u in user_tracks if user_weights.get(u, 0) > 0]
        total_w = sum(user_weights.get(u, 0) for u in active)

        if total_w <= 0:
            return allocate_tracks(tracks, "Equal", num_tracks, None, selected_genres)

        user_targets_f = {}
        assigned = 0
        fractional = []
        for u in active:
            exact = num_tracks * (user_weights[u] / total_w)
            base = int(exact)
            user_targets_f[u] = base
            assigned += base
            fractional.append((exact - base, u))

        leftover = num_tracks - assigned
        fractional.sort(reverse=True)
        for _, u in fractional[:leftover]:
            user_targets_f[u] += 1

        user_idx = {u: 0 for u in user_tracks}

        # Phase 1
        for u in active:
            need = user_targets_f[u]
            bucket = user_tracks[u]

            while need > 0 and user_idx[u] < len(bucket):
                t = bucket[user_idx[u]]
                user_idx[u] += 1
                if t["id"] in used_ids:
                    continue
                selected_tracks.append(t)
                used_ids.add(t["id"])
                user_contrib[u] += 1
                need -= 1

            if need > 0:
                warnings.append(
                    f"**{u}** could only contribute {user_contrib[u]} of {user_targets_f[u]} expected."
                )

        # Phase 2 global fill
        if len(selected_tracks) < num_tracks:
            pool = []
            for u in active:
                for t in user_tracks[u][user_idx[u]:]:
                    if t["id"] not in used_ids:
                        pool.append(t)

            random.shuffle(pool)

            while len(selected_tracks) < num_tracks and pool:
                t = pool.pop()
                if t["id"] in used_ids:
                    continue
                selected_tracks.append(t)
                used_ids.add(t["id"])
                user_contrib[t["user_id"]] += 1

    allocation_info = {
        "user_contribution": user_contrib,
        "warnings": warnings
    }

    random.shuffle(selected_tracks)

    if len(selected_tracks) > num_tracks:
        selected_tracks = selected_tracks[:num_tracks]

    # ✅ NEW: compute genre_contribution for single-genre case too
    genre_contrib = defaultdict(int)
    genres_for_count = selected_genres if selected_genres else genre_list

    for t in selected_tracks:
        track_genres = t.get("genres", []) or []
        for g in genres_for_count:
            if g in track_genres:
                genre_contrib[g] += 1
                break

    return selected_tracks, allocation_info, dict(genre_contrib)


def get_top_consensus_tracks(all_tracks, selected_track_ids, limit=10):
    """Get top tracks sorted ONLY by number of users"""
    candidate_tracks = [t for t in all_tracks if t['id'] not in selected_track_ids]
    
    track_user_counts = defaultdict(set)
    for track in candidate_tracks:
        track_user_counts[track['id']].add(track['user_id'])
    
    consensus_tracks = [t for t in candidate_tracks if len(track_user_counts[t['id']]) >= 2]
    
    seen = set()
    unique_tracks = []
    for track in consensus_tracks:
        if track['id'] not in seen:
            seen.add(track['id'])
            track['user_count'] = len(track_user_counts[track['id']])
            unique_tracks.append(track)
    
    unique_tracks.sort(key=lambda t: t['user_count'], reverse=True)
    
    return unique_tracks[:limit]

def get_display_name(username):
    """Get display name for username"""
    if 'username_to_display_name' in st.session_state:
        return st.session_state.username_to_display_name.get(username, username)
    return username

# 🔧 NEW: helper to process any image (upload OR camera) for Spotify
def process_image_for_spotify(image_bytes):
    """
    Resize + convert + compress the image until Spotify accepts it (<256 KB JPEG)
    """
    try:
        img = Image.open(BytesIO(image_bytes))
    except Exception:
        return None

    # Always convert to JPEG (Spotify requirement)
    img = img.convert("RGB")

    # Resize if very large (phone cameras etc.)
    max_dimension = 640
    img.thumbnail((max_dimension, max_dimension))

    # Compress until < 256 KB or quality too low
    quality = 90
    while quality >= 10:
        buffer = BytesIO()
        img.save(buffer, format="JPEG", quality=quality)
        data = buffer.getvalue()
        if len(data) <= 256 * 1024:
            return data
        quality -= 5

    return None  # couldn't get under 256 KB

def main():
    # st.title("🎵 Crowdsync - Party Playlist Generator")
    st.markdown("Crowdsync is an intelligent party-playlist generator that blends the music tastes of you and your friends into one perfectly balanced playlist. Simply enter your guests' Spotify usernames, scan their public playlists, choose the genres and settings you want — and Crowdsync builds a personalized party soundtrack based on everyone's real listening history.")
    
    st.info("""
    💡 **Tip for Best Results:**  
    For a more accurate and personalized mix, guests should temporarily make some of their playlists public and ensure these public playlists are actually linked to their Spotify profile. When making playlists public, also select "Add to your profile" to allow Crowdsync to access them. The more public playlists available, the better the playlist will reflect everyone's taste!
    """)

    # 🔐 NEW: use the login/authentication flow first
    sp, current_user = ensure_spotify_authenticated()
    
    st.sidebar.success(f"✅ Logged in as: **{current_user.get('display_name', current_user.get('id', 'Unknown'))}**")
    
    # ⛔ Removed old st.header("Step 1: Create Guest List")
    
    top_left, top_right = st.columns(2)
    
    with top_left:
        # ✅ Renamed this subheader as requested
        st.header("Step 1: Create Guest List")
        guest_input = st.text_area(
            "Spotify usernames, profile IDs, or profile URLs (one per line)",
            help="Enter Spotify usernames, profile IDs, or paste profile URLs (e.g., https://open.spotify.com/user/USERNAME)",
            height=150,
            key="guest_input"
        )
        
        raw_guests = [g.strip() for g in guest_input.split('\n') if g.strip()]
        processed_guests = []
        
        for guest in raw_guests:
            if 'spotify.com/user/' in guest or '/user/' in guest:
                username = extract_username_from_url(guest)
                if username:
                    processed_guests.append(username)
                else:
                    processed_guests.append(guest)
            else:
                processed_guests.append(guest)
        
        guests = []
        seen = set()
        duplicates = []
        
        for guest in processed_guests:
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
            
            if st.button("✓ Validate Usernames", key="validate_btn"):
                with st.spinner("Validating usernames..."):
                    validated_guests = {}
                    username_to_display_name = {}
                    invalid_users = []
                    
                    for guest in guests:
                        exists, user_data = validate_user_exists(sp, guest)
                        validated_guests[guest] = {
                            'exists': exists,
                            'data': user_data
                        }
                        if exists and user_data:
                            display_name = user_data.get('display_name', guest)
                            username_to_display_name[guest] = display_name
                        else:
                            invalid_users.append(guest)
                    
                    st.session_state.validated_guests = validated_guests
                    st.session_state.username_to_display_name = username_to_display_name
                    
                    if invalid_users:
                        st.error(f"❌ Invalid usernames found: {', '.join(invalid_users)}")
                        st.error("Please correct these usernames before continuing.")
                        st.session_state.all_validated = False
                    else:
                        st.session_state.all_validated = True
            
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
        
        can_scan = st.session_state.get('all_validated', False)
        
        if guests and st.button("🔍 Scan Playlists and Gather Music Taste", type="primary", key="gather_data_btn", disabled=not can_scan):
            if not guests:
                st.error("Please add at least one guest!")
                st.stop()
            
            if not can_scan:
                st.error("⚠️ Please validate all usernames before scanning!")
                st.stop()
            
            st.session_state.validation_complete = False
            st.session_state.all_tracks = []
            
            with st.spinner("🔎 We're analyzing you and your friends' music taste — fetching playlists, scanning tracks, and identifying genres. This may take a few minutes. Grab a drink! ☕🎵"):
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                all_tracks = []
                users_no_playlists = []
                
                for idx, guest in enumerate(guests):
                    status_text.text(f"Processing {guest}...")
                    
                    user_market = current_user.get('country', 'US')
                    tracks = get_user_playlists_data(sp, guest, user_market)
                    
                    if not tracks:
                        users_no_playlists.append(guest)
                    else:
                        artist_ids = set()
                        for track in tracks:
                            artist_ids.update(track['artist_ids'])
                        
                        genres_map = get_artist_genres(sp, list(artist_ids))
                        
                        for track in tracks:
                            track_genres = []
                            for artist_id in track['artist_ids']:
                                track_genres.extend(genres_map.get(artist_id, []))
                            track['genres'] = list(set(track_genres))
                        
                        all_tracks.extend(tracks)
                    
                    progress_bar.progress((idx + 1) / len(guests))
                
                status_text.empty()
                progress_bar.empty()
                
                if users_no_playlists:
                    users_no_playlists_display = [get_display_name(u) if u in st.session_state.get('username_to_display_name', {}) else u for u in users_no_playlists]
                    st.warning(f"⚠️ No public playlists found for: {', '.join(users_no_playlists_display)}")
                    st.info("💡 **Note:** Users without public playlists cannot contribute their music taste.")
                
                if not all_tracks:
                    st.error("No tracks found. Please check that guests have public playlists.")
                    st.stop()
                
                st.session_state.all_tracks = all_tracks
                st.session_state.guests = guests
                st.session_state.validation_complete = True
                
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
                st.rerun()
    
    with top_right:
        st.header("Step 2: Select Genre & Filters")
        
        selected_genres = []
        year_range = None
        popularity_range = (0, 100)
        max_per_artist = 5
        
        if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
            all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)
            total_tracks_found = len(st.session_state.all_tracks)
            guest_display_names = [get_display_name(g) for g in st.session_state.guests]
            guest_list = ", ".join(guest_display_names)
            
            if all_genres:
                st.info(f"🎵 Found **{len(all_genres)} unique genres** and **{total_tracks_found} tracks** from: {guest_list}")
        
        col3, col4 = st.columns([2, 1])
        
        with col3:
            if st.session_state.get('validation_complete', False) and 'all_tracks' in st.session_state:
                all_genres = get_all_genres_from_tracks(st.session_state.all_tracks)
                
                if all_genres:
                    consensus, discovery, discovery_message = get_genre_recommendations(
                        st.session_state.all_tracks, 
                        st.session_state.guests
                    )
                    
                    if consensus:
                        st.markdown("**🎯 Consensus Top 5 Genres**")
                        for idx, item in enumerate(consensus, 1):
                            genre = item[0]
                            num_users = item[1]
                            st.markdown(f"{idx}. **{genre}** ({num_users} users)")
                        st.markdown("---")
                    
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
                    
                    st.markdown("""
                    <div style="background-color: #f0f8ff; padding: 15px; border-radius: 5px; border-left: 4px solid #1e90ff;">
                        <h4 style="margin: 0 0 10px 0; color: #1e90ff;">🎵 Select Genres for Your Playlist</h4>
                        <p style="margin: 0; font-size: 0.9em; color: #555;">This is the most important setting! Choose genres to customize your playlist, or leave empty for a general mix.</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    st.markdown("")
                    
                    selected_genres = st.multiselect(
                        "Choose one or multiple genres",
                        options=all_genres,
                        help="Select genres to filter your playlist. Leave empty to include all genres.",
                        label_visibility="collapsed"
                    )
                else:
                    st.warning("No genres found in guests' playlists")
                    selected_genres = []
            else:
                st.info("👆 Validate guests first to see available genres")
                selected_genres = []
        
        with col4:
            st.markdown("**Playlist Settings**")
            
            st.markdown("*Playlist Name*")
            playlist_name = st.text_input("Playlist name", "Crowdsync Playlist", label_visibility="collapsed", key="playlist_name_input")
            
            st.markdown("*Number of Tracks*")
            num_tracks = st.number_input("Number of tracks", min_value=10, max_value=200, value=40, label_visibility="collapsed")
            
            st.markdown("*Allocation Mode*")
            allocation_mode = st.radio("Allocation mode", ["Equal", "Focus"], label_visibility="collapsed")
            
            if allocation_mode == "Focus":
                st.info("⚙️ Set weights below")
        
        st.markdown("---")
        
        col5, col6, col7 = st.columns(3)
        with col5:
            st.markdown("*Filter by Year*")
            year_filter = st.checkbox("Enable", value=False, key="year_filter_checkbox")
            if year_filter:
                year_range = st.slider("Release year range", 1960, 2025, (2018, 2025))
            else:
                year_range = None
        
        with col6:
            st.markdown("*Popularity Range*")
            popularity_options = {
                "All (0-100)": (0, 100),
                "Underground (0-33)": (0, 33),
                "Midrange (34-66)": (34, 66),
                "Mainstream (67-100)": (67, 100)
            }
            popularity_choice = st.selectbox("Popularity", list(popularity_options.keys()), label_visibility="collapsed")
            popularity_range = popularity_options[popularity_choice]
        
        with col7:
            st.markdown("*Max Tracks per Artist*")
            max_per_artist = st.number_input("Max per artist", min_value=1, max_value=20, value=5, label_visibility="collapsed")
        
        if allocation_mode == "Focus" and st.session_state.get('validation_complete', False):
            st.subheader("Focus Mode: Weight Distribution")
            st.info("Adjust how much each guest's music taste influences the playlist. Total must equal 100%.")
            
            if 'user_weight_values' not in st.session_state or set(st.session_state.user_weight_values.keys()) != set(st.session_state.guests):
                equal_weight = (100 // len(st.session_state.guests) // 10) * 10
                st.session_state.user_weight_values = {guest: equal_weight for guest in st.session_state.guests}
                remaining = 100 - (equal_weight * (len(st.session_state.guests) - 1))
                st.session_state.user_weight_values[st.session_state.guests[-1]] = remaining
            
            weights = {}
            total_weight = 0
            
            for idx, guest in enumerate(st.session_state.guests):
                display_name = get_display_name(guest)
                default_value = st.session_state.user_weight_values.get(guest, 0)
                weight = st.slider(
                    f"{display_name}", 
                    0, 
                    100, 
                    default_value,
                    step=10,
                    key=f"weight_{guest}"
                )
                st.session_state.user_weight_values[guest] = weight
                weights[guest] = weight / 100.0
                total_weight += weight
            
            if total_weight == 100:
                st.success(f"✅ Total weight: {total_weight}%")
            else:
                st.error(f"⚠️ Total weight: {total_weight}% - Must equal 100% to generate playlist!")
            
            st.session_state.user_weights = weights
            st.session_state.weights_valid = (total_weight == 100)
        else:
            st.session_state.weights_valid = True
        
        if st.session_state.get('validation_complete', False):
            can_generate = True
            if allocation_mode == "Focus":
                if not st.session_state.get('weights_valid', False):
                    can_generate = False
                    st.warning("⚠️ Please adjust weights to total 100% before generating playlist")
            
            if can_generate and st.button("🎨 Generate Playlist", type="primary"):
                all_tracks = st.session_state.all_tracks
                
                with st.spinner("Filtering and ranking tracks..."):
                    user_market = current_user.get('country', 'US')
                    filtered_tracks = filter_tracks(
                        all_tracks,
                        selected_genres,
                        year_range,
                        popularity_range,
                        user_market,
                        True,
                        max_per_artist
                    )
                    
                    if not filtered_tracks:
                        st.error("❌ No tracks match your filter criteria.")
                        st.info("💡 Try: widening the year range, selecting different genres, or changing the popularity range")
                        st.stop()
                    
                    if len(filtered_tracks) < num_tracks:
                        st.warning(f"⚠️ Your current genre selection only provides **{len(filtered_tracks)} tracks**, which is fewer than your requested **{num_tracks} tracks**. To reach your desired playlist length, please add more genres or broaden your filters.")
                    
                    user_weights = st.session_state.get('user_weights', None) if allocation_mode == "Focus" else None
                    selected_tracks, allocation_info, genre_contribution = allocate_tracks(
                        filtered_tracks,
                        allocation_mode,
                        num_tracks,
                        user_weights,
                        selected_genres
                    )
                    
                    st.session_state.selected_tracks = selected_tracks
                    st.session_state.filtered_tracks = filtered_tracks
                    st.session_state.allocation_info = allocation_info
                    st.session_state.genre_contribution = genre_contribution
                    
                    if 'tracks_to_remove' not in st.session_state:
                        st.session_state.tracks_to_remove = set()
                    
                    selected_ids = {t['id'] for t in selected_tracks}
                    top_consensus = get_top_consensus_tracks(filtered_tracks, selected_ids)
                    st.session_state.top_consensus = top_consensus
                
                st.success("✅ Playlist generated successfully!")
    
    if 'selected_tracks' in st.session_state:
        st.markdown("---")
        st.header("Step 3: Create Playlist")
        
        selected_tracks = st.session_state.selected_tracks
        filtered_tracks = st.session_state.filtered_tracks
        all_tracks = st.session_state.all_tracks
        allocation_info = st.session_state.get('allocation_info', {})
        
        genre_display = ", ".join(selected_genres) if selected_genres else "All"
        pop_display = f"{popularity_range[0]}–{popularity_range[1]}"
        year_display = f"{year_range[0]}–{year_range[1]}" if year_range else "All"
        
        metrics_col1, metrics_col2, metrics_col3, metrics_col4, metrics_col5 = st.columns([2, 1.5, 1.5, 1.5, 1.5])
        
        with metrics_col1:
            st.markdown("**Track Contribution by Guest**")
            if 'user_contribution' in allocation_info and allocation_info['user_contribution']:
                for user, count in allocation_info['user_contribution'].items():
                    display_name = get_display_name(user)
                    percentage = (count / len(selected_tracks) * 100) if selected_tracks else 0
                    st.markdown(f"**{display_name}**  \n{count} tracks • {percentage:.1f}%")
            else:
                st.markdown("No track contributions available.")
            
            if selected_genres:
                st.markdown("---")
                st.markdown("**Genre Distribution**")
                genre_contribution = st.session_state.get('genre_contribution', {})
                for genre in selected_genres:
                    count = genre_contribution.get(genre, 0)
                    percentage = (count / len(selected_tracks) * 100) if selected_tracks else 0
                    st.markdown(f"**{genre}**  \n{count} tracks • {percentage:.1f}%")
            
            st.markdown("---")
            
            col_save, col_refill = st.columns(2)
            
            with col_save:
                # 📸 NEW: allow upload OR live photo
                uploaded_cover = st.file_uploader(
                    "Upload playlist cover (JPG or PNG)",
                    type=["jpg", "jpeg", "png"],
                    key="playlist_cover_uploader"
                )

                photo = st.camera_input("📸 Or take a photo")

                # Decide which image to use (camera has priority)
                final_image_bytes = None
                if photo is not None:
                    final_image_bytes = photo.getvalue()
                elif uploaded_cover is not None:
                    final_image_bytes = uploaded_cover.getvalue()

                # ▶️ NEW: Button + link + copy side by side
                btn_col, link_col = st.columns([1, 2])

                with btn_col:
                    save_clicked = st.button(
                        "💾 Save Playlist to Spotify",
                        type="primary",
                        key="save_playlist_btn"
                    )

                with link_col:
                    playlist_url = st.session_state.get("created_playlist_url")
                    if playlist_url:
                        # Text + copy button using HTML/JS
                        components.html(
                            f"""
                            <div style="display:flex; align-items:center; gap:8px;">
                                <input id="playlist-link-input" type="text" value="{playlist_url}" style="width:100%; padding:4px;" readonly />
                                <button style="padding:4px 8px; cursor:pointer;"
                                    onclick="navigator.clipboard.writeText(document.getElementById('playlist-link-input').value)">
                                    Copy
                                </button>
                            </div>
                            """,
                            height=50,
                        )

                if save_clicked:
                    final_tracks = [t for t in st.session_state.selected_tracks if t['id'] not in st.session_state.get('tracks_to_remove', set())]
                    
                    if not final_tracks:
                        st.error("No tracks to save!")
                    else:
                        with st.spinner("Creating playlist on Spotify..."):
                            try:
                                # Always create as PUBLIC playlist
                                playlist = sp.user_playlist_create(
                                    user=current_user['id'],
                                    name=playlist_name,
                                    public=True
                                )

                                # Save link to session state so we can show + copy it
                                st.session_state["created_playlist_url"] = playlist['external_urls']['spotify']
                                
                                track_uris = [f"spotify:track:{t['id']}" for t in final_tracks]
                                skipped = []
                                
                                for i in range(0, len(track_uris), 100):
                                    batch = track_uris[i:i+100]
                                    try:
                                        sp.playlist_add_items(playlist['id'], batch)
                                    except Exception as e:
                                        skipped.extend(batch)

                                # 🎨 NEW: handle cover upload (upload OR camera) AFTER playlist is created
                                if final_image_bytes is not None:
                                    try:
                                        processed_bytes = process_image_for_spotify(final_image_bytes)
                                        if processed_bytes is None:
                                            st.error("Image could not be reduced below 256 KB. Try a smaller or simpler photo.")
                                        else:
                                            encoded_cover = base64.b64encode(processed_bytes)
                                            sp.playlist_upload_cover_image(playlist['id'], encoded_cover)
                                            st.success("📸 Custom playlist cover uploaded!")
                                    except Exception as cover_err:
                                        st.warning(f"Playlist created, but the cover image could not be processed or uploaded: {cover_err}")
                                
                                st.success(f"🎉 Public playlist '{playlist_name}' created successfully!")
                                st.markdown(f"[Open in Spotify]({playlist['external_urls']['spotify']})")
                                
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
                        random.shuffle(remaining_tracks)
                        
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
        
        # ✅ NEW: build allocation notices including guests with no tracks in selected genres
        all_warnings = allocation_info.get('warnings', []).copy()

        if selected_genres:
            # Guests who have NO tracks at all in the selected genres (in their public playlists)
            users_with_selected_genre = set()
            for t in all_tracks:
                if t['user_id'] in st.session_state.guests:
                    track_genres = t.get('genres', []) or []
                    if any(g in track_genres for g in selected_genres):
                        users_with_selected_genre.add(t['user_id'])
            
            for guest in st.session_state.guests:
                if guest not in users_with_selected_genre:
                    display_name = get_display_name(guest)
                    all_warnings.append(
                        f"**{display_name}** does not contribute any tracks because they have no songs in the selected genres in their public playlists."
                    )

        if all_warnings:
            st.warning("⚠️ **Allocation Notices:**")
            for warning in all_warnings:
                updated_warning = warning
                # Preserve existing display-name replacement for username-based warnings
                for guest in st.session_state.guests:
                    display_name = get_display_name(guest)
                    if f"**{guest}**" in updated_warning:
                        updated_warning = updated_warning.replace(f"**{guest}**", f"**{display_name}**")
                st.markdown(f"- {updated_warning}")
            st.info("💡 Remaining slots were filled with the best-ranked tracks from users who had available songs.")
        
        st.markdown("---")
        
        bottom_left, bottom_right = st.columns([3, 2])
        
        with bottom_left:
            st.subheader("🎵 Track List")
            
            if 'tracks_to_remove' not in st.session_state:
                st.session_state.tracks_to_remove = set()
            
            display_tracks = [t for t in selected_tracks if t['id'] not in st.session_state.tracks_to_remove]
            
            if 'display_order' not in st.session_state or len(st.session_state.display_order) != len(display_tracks):
                st.session_state.display_order = list(range(len(display_tracks)))
                random.shuffle(st.session_state.display_order)
            
            for display_idx in st.session_state.display_order:
                if display_idx >= len(display_tracks):
                    continue
                    
                track = display_tracks[display_idx]
                
                col_track, col_button = st.columns([5, 1])
                
                with col_track:
                    genres_display = ", ".join(track['genres'][:3]) if track['genres'] else "No genre"
                    year = parse_release_year(track['album_release_date'])
                    artists_display = ', '.join([a for a in track['artists'] if a]) or "Unknown Artist"
                    friend_display_name = get_display_name(track['user_id'])
                    
                    actual_position = st.session_state.display_order.index(display_idx) + 1
                    
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
        
        with bottom_right:
            st.subheader("⭐ Top Consensus Songs (Not in the Playlist)")
            
            current_selected_ids = {t['id'] for t in selected_tracks if t['id'] not in st.session_state.get('tracks_to_remove', set())}
            
            if 'filtered_tracks' in st.session_state:
                top_consensus = get_top_consensus_tracks(st.session_state.filtered_tracks, current_selected_ids)
                st.session_state.top_consensus = top_consensus
            
            if st.session_state.get('top_consensus'):
                st.info("High-consensus tracks that didn't make it into the playlist")
                
                for track in st.session_state.top_consensus:
                    col_consensus, col_add = st.columns([5, 1])
                    
                    with col_consensus:
                        genres_display = ", ".join(track['genres'][:3]) if track['genres'] else "No genre"
                        year = parse_release_year(track['album_release_date'])
                        artists_display = ', '.join([a for a in track['artists'] if a]) or "Unknown Artist"
                        user_count = track.get('user_count', 0)
                        
                        st.markdown(f"""
                        **{track['name']}** by {artists_display}  
                        `{user_count} users` • `Popularity: {track['popularity']}` • `Year: {year}`
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