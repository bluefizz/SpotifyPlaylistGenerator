import streamlit as st
import spotipy
from spotipy.oauth2 import SpotifyOAuth
import os
from datetime import datetime, timedelta
import json
from collections import defaultdict
import time
from dotenv import load_dotenv
import uuid
import glob

# Load environment variables
load_dotenv()

# Page config
st.set_page_config(
    page_title="Vibescape - Party Playlist Generator",
    page_icon="🎵",
    layout="wide"
)

# ==================== CONFIGURATION ====================
PLAYLIST_CACHE_FILE = "playlist_cache.json"
GENRE_CACHE_FILE = "genre_cache.json"
SCOPE = "playlist-modify-public playlist-modify-private user-library-read"

# ==================== CACHE MANAGEMENT ====================
def load_cache(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_cache(filename, data):
    with open(filename, 'w') as f:
        json.dump(data, f)

def get_cached_playlists(user_id):
    cache = load_cache(PLAYLIST_CACHE_FILE)
    if user_id in cache:
        cached_time = datetime.fromisoformat(cache[user_id]['timestamp'])
        if datetime.now() - cached_time < timedelta(hours=24):
            return cache[user_id]['data']
    return None

def cache_playlists(user_id, data):
    cache = load_cache(PLAYLIST_CACHE_FILE)
    cache[user_id] = {'timestamp': datetime.now().isoformat(), 'data': data}
    save_cache(PLAYLIST_CACHE_FILE, cache)

def get_cached_genres(artist_id):
    cache = load_cache(GENRE_CACHE_FILE)
    if artist_id in cache:
        cached_time = datetime.fromisoformat(cache[artist_id]['timestamp'])
        if datetime.now() - cached_time < timedelta(days=30):
            return cache[artist_id]['genres']
    return None

def cache_genres(artist_id, genres):
    cache = load_cache(GENRE_CACHE_FILE)
    cache[artist_id] = {'timestamp': datetime.now().isoformat(), 'genres': genres}
    save_cache(GENRE_CACHE_FILE, cache)

# ==================== SPOTIFY AUTHENTICATION (MULTI-USER SAFE) ====================
def ensure_spotify_authenticated():
    CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID")
    CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("SPOTIPY_REDIRECT_URI")

    if not CLIENT_ID or not CLIENT_SECRET or not REDIRECT_URI:
        st.error("Spotify credentials are missing!")
        st.stop()

    # Assign a unique session ID if not present
    if "spotify_session_id" not in st.session_state:
        st.session_state.spotify_session_id = str(uuid.uuid4())

    # Remove any old shared caches (optional)
    for f in glob.glob(".spotify_cache_*"):
        if f != f".spotify_cache_{st.session_state.spotify_session_id}":
            os.remove(f)

    # Per-user cache path
    cache_path = f".spotify_cache_{st.session_state.spotify_session_id}"

    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE,
        show_dialog=True,
        cache_path=cache_path
    )

    token_info = st.session_state.get("token_info")
    if token_info:
        try:
            if sp_oauth.is_token_expired(token_info):
                token_info = sp_oauth.refresh_access_token(token_info.get("refresh_token"))
                st.session_state["token_info"] = token_info
        except Exception:
            st.session_state.pop("token_info", None)
            token_info = None

    # OAuth redirect handling
    if not token_info:
        query_params = st.experimental_get_query_params()
        if "code" in query_params:
            code = query_params["code"][0]
            token_info = sp_oauth.get_access_token(code)
            st.session_state["token_info"] = token_info
            st.experimental_set_query_params()
        else:
            auth_url = sp_oauth.get_authorize_url()
            st.markdown("### 🔐 Please log in with Spotify")
            st.markdown(f"[**Log in with Spotify**]({auth_url})")
            st.stop()

    access_token = token_info.get("access_token") if isinstance(token_info, dict) else None
    if not access_token:
        st.error("Unable to obtain Spotify access token.")
        st.stop()

    sp_client = spotipy.Spotify(auth=access_token)
    st.session_state["spotify_client"] = sp_client
    st.session_state["current_user"] = sp_client.current_user()

# ==================== DATA GATHERING & UTILITIES ====================
def validate_user_exists(sp, username):
    try:
        user = sp.user(username)
        return True, user
    except Exception:
        return False, None

def get_user_playlists_data(sp, username, market):
    cached_data = get_cached_playlists(username)
    if cached_data:
        return cached_data
    tracks_data = []
    try:
        playlists, results = [], sp.user_playlists(username)
        while results:
            playlists.extend(results['items'])
            if results.get('next'):
                results = sp.next(results)
            else:
                break
        public_playlists = [p for p in playlists if p.get('public')]
        for playlist in public_playlists:
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
        cache_playlists(username, tracks_data)
        return tracks_data
    except Exception as e:
        st.error(f"Error fetching playlists for {username}: {str(e)}")
        return []

def get_artist_genres(sp, artist_ids):
    genres_map = {}
    artists_to_fetch = []
    for artist_id in artist_ids:
        cached_genres = get_cached_genres(artist_id)
        if cached_genres is not None:
            genres_map[artist_id] = cached_genres
        else:
            artists_to_fetch.append(artist_id)
    for i in range(0, len(artists_to_fetch), 50):
        batch = artists_to_fetch[i:i+50]
        try:
            artists_data = sp.artists(batch)
            for artist in artists_data['artists']:
                if artist:
                    genres = artist.get('genres', [])
                    genres_map[artist['id']] = genres
                    cache_genres(artist['id'], genres)
            time.sleep(0.1)
        except Exception as e:
            st.warning(f"Error fetching artist genres: {str(e)}")
    return genres_map

def get_all_genres_from_tracks(tracks):
    all_genres = set()
    for track in tracks:
        all_genres.update(track.get('genres', []))
    return sorted(list(all_genres))

def parse_release_year(release_date):
    try:
        return int(release_date.split('-')[0])
    except:
        return None

def filter_tracks(tracks, selected_genres, year_range, popularity_range, market, market_filter_enabled, max_per_artist):
    filtered = []
    artist_count = defaultdict(int)
    for track in tracks:
        if selected_genres and not any(g in track.get('genres', []) for g in selected_genres):
            continue
        release_year = parse_release_year(track['album_release_date'])
        if year_range and release_year and not (year_range[0] <= release_year <= year_range[1]):
            continue
        if popularity_range and not (popularity_range[0] <= track['popularity'] <= popularity_range[1]):
            continue
        if market_filter_enabled and market and market not in track.get('available_markets', []):
            continue
        artist_key = tuple(sorted(track['artist_ids']))
        if max_per_artist and artist_count[artist_key] >= max_per_artist:
            continue
        artist_count[artist_key] += 1
        filtered.append(track)
    return filtered

def calculate_track_scores(tracks):
    track_user_counts = defaultdict(set)
    for track in tracks:
        track_user_counts[track['id']].add(track['user_id'])
    for track in tracks:
        score = {
            'cross_user_dup_count': len(track_user_counts[track['id']]),
            'popularity': track['popularity'],
            'release_year': parse_release_year(track['album_release_date']) or 0
        }
        track['score'] = score
    return tracks

# ==================== MAIN UI ====================
def main():
    st.title("🎵 Vibescape - Party Playlist Generator")
    st.info("💡 Guests should make playlists public for best results!")

    ensure_spotify_authenticated()
    sp = st.session_state["spotify_client"]
    current_user = st.session_state["current_user"]

    st.sidebar.success(f"✅ Logged in as: **{current_user.get('display_name', current_user.get('id','Unknown'))}**")

    # --- Guest list ---
    st.subheader("Guest List")
    guest_input = st.text_area("Spotify usernames (one per line)", height=150)
    guests = [g.strip() for g in guest_input.split('\n') if g.strip()]

    if st.button("Validate & Gather Data"):
        if not guests:
            st.error("Add at least one guest!")
            st.stop()
        all_tracks = []
        for guest in guests:
            exists, _ = validate_user_exists(sp, guest)
            if not exists:
                st.error(f"User {guest} does not exist")
                continue
            tracks = get_user_playlists_data(sp, guest, current_user.get('country', 'US'))
            if tracks:
                artist_ids = set()
                for t in tracks:
                    artist_ids.update(t['artist_ids'])
                genres_map = get_artist_genres(sp, list(artist_ids))
                for t in tracks:
                    t['genres'] = list({g for aid in t['artist_ids'] for g in genres_map.get(aid, [])})
                all_tracks.extend(tracks)
        if not all_tracks:
            st.error("No tracks found")
            st.stop()
        st.session_state.all_tracks = all_tracks
        st.session_state.guests = guests
        st.success(f"✅ Gathered {len(all_tracks)} tracks from {len(guests)} guests!")

    # --- Filters & playlist generation ---
    if 'all_tracks' in st.session_state:
        selected_genres = st.multiselect("Select genres", get_all_genres_from_tracks(st.session_state.all_tracks))
        year_filter = st.checkbox("Filter by year")
        year_range = st.slider("Year range", 1960, 2025, (2018, 2025)) if year_filter else None
        popularity_range = (0,100)
        filtered_tracks = filter_tracks(st.session_state.all_tracks, selected_genres, year_range, popularity_range,
                                        current_user.get('country','US'), True, 5)
        scored_tracks = calculate_track_scores(filtered_tracks)
        st.write(f"{len(scored_tracks)} tracks match filters.")

if __name__ == "__main__":
    main()