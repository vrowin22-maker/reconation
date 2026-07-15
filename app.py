from flask import Flask, render_template, request ,redirect
import requests
import time
from dotenv import load_dotenv
import os
from pymongo import MongoClient
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import bcrypt
from collections import Counter

load_dotenv()

TMDB_API_KEY = os.getenv('TMDB_API_KEY')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'reconation_secret_123')

class APIHandler:
    @staticmethod
    def get_itunes_preview(track, artist):
        """Get iTunes preview URL and artwork"""
        try:
            url = f"https://itunes.apple.com/search?term={artist}+{track}&media=music&limit=1"
            response = requests.get(url, timeout=5)
            data = response.json()
            if data.get('resultCount', 0) > 0:
                result = data['results'][0]
                return {
                    'previewUrl': result.get('previewUrl'),
                    'artworkUrl100': result.get('artworkUrl100'),
                    'trackId': result.get('trackId'),
                }
            return None
        except:
            return None

client = MongoClient(os.getenv('MONGO_URI'))
db = client['reconation']
liked_collection = db['liked']
users_collection = db['users']

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']

@login_manager.user_loader
def load_user(user_id):
    from bson.objectid import ObjectId
    user_data = users_collection.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None

@app.route('/')
def home():
    movies = []
    anime = []
    songs = []
    try:
        movie_url = f"https://api.themoviedb.org/3/trending/movie/week?api_key={TMDB_API_KEY}"
        movie_res = requests.get(movie_url).json()
        movies = movie_res.get('results', [])[:15]

        anime_query = """
        query {
            Page(perPage: 15) {
                media(sort: TRENDING_DESC, type: ANIME) {
                    title { romaji }
                    coverImage { large }
                }
            }
        }
        """
        anime_res = requests.post('https://graphql.anilist.co', json={'query': anime_query}).json()
        anime = anime_res['data']['Page']['media']

        song_url = f"https://ws.audioscrobbler.com/2.0/?method=chart.gettoptracks&api_key={LASTFM_API_KEY}&format=json&limit=15"
        song_res = requests.get(song_url).json()
        songs = song_res.get('tracks', {}).get('track', [])

    except Exception as e:
        print(f"Home trending error: {e}")
    
    return render_template('index.html', movies=movies, anime=anime, songs=songs)

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    category = request.form.get('category') or request.args.get('category')
    query = request.form.get('query') or request.args.get('query')
    popularity = int(request.form.get('popularity') or request.args.get('popularity') or 70)
    results = []

    try:
        if category == 'movie':
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
            search_res = requests.get(search_url).json()
            
            if search_res.get('results'):
                movie_id = search_res['results'][0]['id']
                rec_url = f"https://api.themoviedb.org/3/movie/{movie_id}/recommendations?api_key={TMDB_API_KEY}"
                rec_res = requests.get(rec_url).json()
                all_results = rec_res.get('results', [])
                results = apply_popularity_filter_movies(all_results, popularity)

        elif category == 'anime':
            search_query = """
            query ($search: String) {
                Media(search: $search, type: ANIME) {
                    id
                    title { romaji }
                }
            }
            """
            search_res = requests.post('https://graphql.anilist.co', json={
                'query': search_query,
                'variables': {'search': query}
            }).json()

            anime_id = search_res['data']['Media']['id']

            rec_query = """
            query ($id: Int) {
                Media(id: $id, type: ANIME) {
                    recommendations {
                        nodes {
                            mediaRecommendation {
                                id
                                title { romaji }
                                coverImage { large }
                                averageScore
                                episodes
                                genres
                                description
                                popularity
                                favourites
                            }
                        }
                    }
                }
            }
            """
            rec_res = requests.post('https://graphql.anilist.co', json={
                'query': rec_query,
                'variables': {'id': anime_id}
            }).json()

            nodes = rec_res['data']['Media']['recommendations']['nodes']
            all_results = [n['mediaRecommendation'] for n in nodes if n['mediaRecommendation']]
            results = apply_popularity_filter_anime(all_results, popularity)

        elif category == 'song':
            if ' - ' in query:
                parts = query.split(' - ')
                track_name = parts[0].strip()
                artist_name = parts[1].strip()
            else:
                track_name = query
                artist_name = None

            search_url = f"https://ws.audioscrobbler.com/2.0/?method=track.search&track={track_name}&api_key={LASTFM_API_KEY}&format=json&limit=1"
            if artist_name:
                search_url += f"&artist={artist_name}"
            
            search_res = requests.get(search_url).json()
            tracks = search_res.get('results', {}).get('trackmatches', {}).get('track', [])
            
            if tracks:
                artist = tracks[0]['artist']
                track = tracks[0]['name']
                similar_url = f"https://ws.audioscrobbler.com/2.0/?method=track.getSimilar&artist={artist}&track={track}&api_key={LASTFM_API_KEY}&format=json&limit=30"
                similar_res = requests.get(similar_url).json()
                all_results = similar_res.get('similartracks', {}).get('track', [])
                
                results = apply_popularity_filter_songs(all_results, popularity)
                
                for song in results:
                    itunes_url = f"https://itunes.apple.com/search?term={song['artist']['name']}+{song['name']}&media=music&limit=1"
                    itunes_res = requests.get(itunes_url).json()
                    if itunes_res['resultCount'] > 0:
                        song['cover'] = itunes_res['results'][0]['artworkUrl100']
                        song['preview'] = itunes_res['results'][0].get('previewUrl', None)
                    else:
                        song['cover'] = None
                        song['preview'] = None

    except Exception as e:
        print(f"Error: {e}")
        results = []

    return render_template('results.html', results=results, category=category, query=query, popularity=popularity)

@app.route('/vibe_match', methods=['POST'])
def vibe_match():
    query = request.form.get('query')
    source_type = request.form.get('source_type')
    
    if not query:
        return redirect('/')
    
    results = {'songs': []}
    vibe_keywords = []
    source_info = {}
    
    try:
        if source_type == 'movie':
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
            search_res = requests.get(search_url).json()
            
            if not search_res.get('results'):
                return render_template('vibe_results.html', query=query, source_type=source_type, results=results, error="Movie not found!")
            
            source_data = search_res['results'][0]
            source_info = {
                'title': source_data.get('title'),
                'id': source_data['id'],
                'type': 'movie',
                'poster': f"https://image.tmdb.org/t/p/w200{source_data.get('poster_path', '')}"
            }
            
            details_url = f"https://api.themoviedb.org/3/movie/{source_data['id']}?api_key={TMDB_API_KEY}"
            details_res = requests.get(details_url).json()
            vibe_keywords = [g['name'] for g in details_res.get('genres', [])]
            
        elif source_type == 'anime':
            search_query = """
            query ($search: String) {
                Media(search: $search, type: ANIME) {
                    id
                    title { romaji english }
                    genres
                    description
                    coverImage { large }
                    averageScore
                    episodes
                    status
                    seasonYear
                }
            }
            """
            search_res = requests.post('https://graphql.anilist.co', json={
                'query': search_query,
                'variables': {'search': query}
            }).json()
            
            media = search_res.get('data', {}).get('Media')
            if not media:
                return render_template('vibe_results.html', query=query, source_type=source_type, results=results, error="Anime not found!")
            
            source_info = {
                'title': media['title'].get('english') or media['title']['romaji'],
                'id': media['id'],
                'type': 'anime',
                'poster': media.get('coverImage', {}).get('large', ''),
                'genres': media.get('genres', [])
            }
            
            vibe_keywords = media.get('genres', [])
        
        print(f"Vibe keywords from {source_type}: {vibe_keywords}")
        
        results['songs'] = find_songs_by_vibe(vibe_keywords, query)
        
    except Exception as e:
        print(f"Vibe match error: {e}")
        return render_template('vibe_results.html', query=query, source_type=source_type, results=results, error="Something went wrong!")
    
    return render_template('vibe_results.html', query=query, source_type=source_type, results=results, vibe_keywords=vibe_keywords, source_info=source_info)

# ============ HELPER FUNCTIONS FOR VIBE MATCH ============

def find_songs_by_vibe(vibe_keywords, title=None):
    """Find songs that match the vibe - with random variety"""
    songs = []
    try:
        import random
        
        genre_to_tag = {
            'Action': ['rock', 'metal', 'alternative', 'energetic'],
            'Adventure': ['epic', 'orchestral', 'cinematic'],
            'Drama': ['emotional', 'piano', 'orchestral', 'melancholic'],
            'Comedy': ['upbeat', 'pop', 'fun', 'happy'],
            'Fantasy': ['fantasy', 'orchestral', 'epic', 'cinematic', 'magical'],
            'Horror': ['dark', 'ambient', 'suspense', 'tense'],
            'Romance': ['romance', 'acoustic', 'pop', 'love', 'emotional'],
            'Science Fiction': ['electronic', 'synthwave', 'cyberpunk', 'future'],
            'Sci-Fi': ['electronic', 'synthwave', 'cyberpunk', 'future'],
            'Thriller': ['suspense', 'dark', 'intense', 'tense'],
            'Mystery': ['mystery', 'suspense', 'ambient', 'dark'],
            'Crime': ['crime', 'dark', 'jazz', 'suspense', 'noir'],
            'War': ['war', 'epic', 'orchestral', 'dramatic'],
            'Western': ['western', 'acoustic', 'guitar', 'cinematic'],
            'Animation': ['animation', 'orchestral', 'soundtrack', 'cinematic'],
            'Family': ['family', 'soundtrack', 'orchestral', 'upbeat'],
            'Music': ['music', 'soundtrack', 'orchestral', 'jazz'],
            'History': ['history', 'orchestral', 'epic', 'cinematic'],
            'Documentary': ['documentary', 'ambient', 'orchestral', 'cinematic'],
            'Supernatural': ['supernatural', 'dark', 'mysterious', 'electronic'],
            'Psychological': ['psychological', 'dark', 'ambient', 'mind-bending'],
            'Cyberpunk': ['cyberpunk', 'electronic', 'synthwave', 'dark'],
            'Dystopian': ['dystopian', 'electronic', 'dark', 'ambient'],
            'Slice of Life': ['acoustic', 'indie', 'chill', 'relaxing'],
            'Sports': ['rock', 'energetic', 'pop', 'upbeat'],
            'Mecha': ['mecha', 'electronic', 'orchestral', 'epic'],
            'Shounen': ['rock', 'energetic', 'j-rock', 'anime'],
            'Seinen': ['alternative', 'rock', 'dark', 'mature'],
            'Josei': ['acoustic', 'pop', 'indie', 'emotional'],
            'Isekai': ['fantasy', 'orchestral', 'epic', 'adventure'],
            'Shoujo': ['pop', 'romance', 'acoustic', 'emotional'],
            'Magic': ['magic', 'fantasy', 'orchestral', 'magical'],
        }
        
        # Title-based specific tags
        title_based_tags = []
        if title:
            title_lower = title.lower()
            if any(word in title_lower for word in ['blade', 'runner']):
                title_based_tags = ['synthwave', 'cyberpunk', 'dark']
            elif any(word in title_lower for word in ['inception', 'dream', 'mind']):
                title_based_tags = ['dreamy', 'ambient', 'electronic']
            elif any(word in title_lower for word in ['interstellar', 'space', 'cosmic']):
                title_based_tags = ['space', 'ambient', 'orchestral']
            elif any(word in title_lower for word in ['matrix', 'cyber', 'digital']):
                title_based_tags = ['cyberpunk', 'electronic', 'synthwave']
            elif any(word in title_lower for word in ['dark', 'knight']):
                title_based_tags = ['dark', 'orchestral', 'epic']
            elif any(word in title_lower for word in ['attack', 'titan']):
                title_based_tags = ['epic', 'orchestral', 'dark', 'j-rock']
            elif any(word in title_lower for word in ['shutter', 'island']):
                title_based_tags = ['dark', 'suspense', 'ambient', 'mysterious']
            elif any(word in title_lower for word in ['fight', 'club']):
                title_based_tags = ['alternative', 'rock', 'dark']
            elif any(word in title_lower for word in ['gladiator']):
                title_based_tags = ['epic', 'orchestral', 'cinematic']
            elif any(word in title_lower for word in ['cowboy', 'bebop']):
                title_based_tags = ['jazz', 'blues', 'space']
            elif any(word in title_lower for word in ['pulp', 'fiction']):
                title_based_tags = ['rock', 'surf', 'retro']
            elif any(word in title_lower for word in ['lion', 'king']):
                title_based_tags = ['orchestral', 'soundtrack', 'epic']
            elif any(word in title_lower for word in ['your lie', 'april']):
                title_based_tags = ['classical', 'piano', 'emotional', 'orchestral']
            elif any(word in title_lower for word in ['demon', 'slayer']):
                title_based_tags = ['j-rock', 'epic', 'orchestral']
            elif any(word in title_lower for word in ['spirited', 'away']):
                title_based_tags = ['whimsical', 'orchestral', 'cinematic', 'magical']
        
        all_tags = []
        for keyword in vibe_keywords:
            if keyword in genre_to_tag:
                all_tags.extend(genre_to_tag[keyword])
            elif keyword.lower() in genre_to_tag:
                all_tags.extend(genre_to_tag[keyword.lower()])
        
        all_tags.extend(title_based_tags)
        
        if any(k in ['Science Fiction', 'Sci-Fi'] for k in vibe_keywords):
            all_tags = ['electronic', 'synthwave', 'cyberpunk'] + all_tags
        
        tag_counts = Counter(all_tags)
        sorted_tags = [tag for tag, count in tag_counts.most_common()]
        
        seen = set()
        unique_tags = []
        for tag in sorted_tags:
            if tag not in seen:
                seen.add(tag)
                unique_tags.append(tag)
        sorted_tags = unique_tags
        
        if not sorted_tags:
            sorted_tags = ['soundtrack', 'cinematic', 'instrumental', 'epic']
        
        print(f"Genres: {vibe_keywords}")
        print(f"Title-specific tags: {title_based_tags}")
        print(f"Final tags: {sorted_tags[:6]}")
        
        # ===== SEARCH WITH RANDOM PAGES FOR VARIETY =====
        random.shuffle(sorted_tags)
        tags_to_search = sorted_tags[:5]
        
        for tag in tags_to_search:
            if len(tag) > 2:
                page = random.randint(1, 3)
                url = f"https://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={tag}&api_key={LASTFM_API_KEY}&format=json&limit=5&page={page}"
                try:
                    res = requests.get(url, timeout=10)
                    data = res.json()
                    tracks = data.get('tracks', {}).get('track', [])
                    print(f"Found {len(tracks)} songs for tag '{tag}' (page {page})")
                    
                    for track in tracks:
                        artist_name = track.get('artist', {}).get('name', 'Unknown')
                        track_name = track.get('name', '')
                        key = f"{artist_name}-{track_name}"
                        
                        if key not in [f"{s.get('artist', {}).get('name', '')}-{s.get('name', '')}" for s in songs]:
                            preview = APIHandler.get_itunes_preview(track_name, artist_name)
                            if preview:
                                track['cover'] = preview.get('artworkUrl100')
                                track['preview'] = preview.get('previewUrl')
                            else:
                                track['cover'] = None
                                track['preview'] = None
                            track['vibe_keyword'] = tag
                            songs.append(track)
                except Exception as e:
                    print(f"Error with tag '{tag}': {e}")
                    continue
        
        print(f"Total songs found: {len(songs)}")
        
        # ===== FALLBACK =====
        if len(songs) < 6:
            print("Trying fallback with random pages...")
            fallback_tags = ['cinematic', 'orchestral', 'soundtrack', 'ambient']
            random.shuffle(fallback_tags)
            for tag in fallback_tags[:2]:
                page = random.randint(1, 2)
                url = f"https://ws.audioscrobbler.com/2.0/?method=tag.gettoptracks&tag={tag}&api_key={LASTFM_API_KEY}&format=json&limit=4&page={page}"
                try:
                    res = requests.get(url, timeout=10)
                    data = res.json()
                    tracks = data.get('tracks', {}).get('track', [])
                    for track in tracks:
                        artist_name = track.get('artist', {}).get('name', 'Unknown')
                        track_name = track.get('name', '')
                        key = f"{artist_name}-{track_name}"
                        if key not in [f"{s.get('artist', {}).get('name', '')}-{s.get('name', '')}" for s in songs]:
                            preview = APIHandler.get_itunes_preview(track_name, artist_name)
                            if preview:
                                track_data = {
                                    'name': track_name,
                                    'artist': {'name': artist_name},
                                    'cover': preview.get('artworkUrl100'),
                                    'preview': preview.get('previewUrl'),
                                    'vibe_keyword': tag
                                }
                                songs.append(track_data)
                            if len(songs) >= 10:
                                break
                except Exception as e:
                    continue
                if len(songs) >= 10:
                    break
        
        random.shuffle(songs)
        return songs[:12]
    except Exception as e:
        print(f"Find songs error: {e}")
        return []

@app.route('/suggest')
def suggest():
    query = request.args.get('query', '')
    category = request.args.get('category', 'movie')
    suggestions = []

    if len(query) < 2:
        return {'suggestions': []}

    try:
        if category == 'movie':
            url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
            res = requests.get(url).json()
            suggestions = [r['title'] for r in res.get('results', [])[:5]]

        elif category == 'anime':
            res = requests.post('https://graphql.anilist.co', json={
                'query': '''query ($search: String) {
                    Page(perPage: 5) {
                        media(search: $search, type: ANIME) {
                            title { romaji }
                        }
                    }
                }''',
                'variables': {'search': query}
            }).json()
            suggestions = [m['title']['romaji'] for m in res['data']['Page']['media']]

        elif category == 'song':
            url = f"https://ws.audioscrobbler.com/2.0/?method=track.search&track={query}&api_key={LASTFM_API_KEY}&format=json&limit=5"
            res = requests.get(url).json()
            tracks = res.get('results', {}).get('trackmatches', {}).get('track', [])
            suggestions = [f"{t['name']} - {t['artist']}" for t in tracks]

    except Exception as e:
        print(f"Suggest error: {e}")
        suggestions = []

    return {'suggestions': suggestions}

@app.route('/like', methods=['POST'])
@login_required
def like():
    item = request.json
    item['user_id'] = current_user.id
    liked_collection.insert_one(item)
    return {'status': 'ok'}

@app.route('/liked')
@login_required
def liked():
    items = list(liked_collection.find({'user_id': current_user.id}, {'_id': 0}))
    return render_template('liked.html', items=items)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if users_collection.find_one({'username': username}):
            return render_template('register.html', error='Username already exists')
        
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        users_collection.insert_one({'username': username, 'password': hashed})
        return redirect('/login')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user_data = users_collection.find_one({'username': username})
        if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data['password']):
            login_user(User(user_data))
            return redirect('/')
        
        return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect('/')

@app.route('/unlike', methods=['POST'])
@login_required
def unlike():
    title = request.json.get('title')
    liked_collection.delete_one({'user_id': current_user.id, 'title': title})
    return {'status': 'ok'}

# ==================== POPULARITY FILTER FUNCTIONS ====================

def apply_popularity_filter_movies(results, popularity):
    if not results:
        return []
    
    sorted_results = sorted(results, key=lambda x: x.get('vote_count', 0), reverse=True)
    total = len(sorted_results)
    
    if total == 0:
        return []
    
    if popularity <= 30:
        start_index = max(0, total - 15)
        filtered = sorted_results[start_index:]
        print(f"DEBUG - Deep Cuts: taking last {len(filtered)} (least popular)")
    elif popularity <= 60:
        filtered = sorted_results
        print(f"DEBUG - Balanced: giving ALL {len(filtered)} results")
    else:
        filtered = sorted_results[:15]
        print(f"DEBUG - Popular Hits: taking first {len(filtered)} (most popular)")
    
    return filtered


def apply_popularity_filter_anime(results, popularity):
    if not results:
        return []
    
    sorted_results = sorted(results, key=lambda x: x.get('popularity', 0) or x.get('favourites', 0), reverse=True)
    total = len(sorted_results)
    
    if total == 0:
        return []
    
    if popularity <= 30:
        start_index = max(0, total - 15)
        filtered = sorted_results[start_index:]
        print(f"DEBUG - Deep Cuts: taking last {len(filtered)} (least popular)")
    elif popularity <= 60:
        filtered = sorted_results
        print(f"DEBUG - Balanced: giving ALL {len(filtered)} results")
    else:
        filtered = sorted_results[:15]
        print(f"DEBUG - Popular Hits: taking first {len(filtered)} (most popular)")
    
    return filtered


def apply_popularity_filter_songs(results, popularity):
    if not results:
        return []
    
    sorted_results = sorted(results, key=lambda x: int(x.get('listeners', 0) or 0), reverse=True)
    total = len(sorted_results)
    
    if total == 0:
        return []
    
    if popularity <= 30:
        start_index = max(0, total - 15)
        filtered = sorted_results[start_index:]
        print(f"DEBUG - Deep Cuts: taking last {len(filtered)} (least popular)")
    elif popularity <= 60:
        filtered = sorted_results
        print(f"DEBUG - Balanced: giving ALL {len(filtered)} results")
    else:
        filtered = sorted_results[:15]
        print(f"DEBUG - Popular Hits: taking first {len(filtered)} (most popular)")
    
    return filtered

# ==================== RUN THE APP ====================
if __name__ == '__main__':
    app.run(debug=True)
# #app.py is the backend,which is the logic of what happen when We do an action
# #html is the structure,like the button,texts,inputs
# #css is what makes it pretty such as colours,layouts

# from flask import Flask, render_template, request #render_template lets us load html files
#                                                   #request let us read data the user send

# TMDB_API_KEY = "db173039059c81787b2e9599100983ec"

# app = Flask(__name__)

# @app.route('/')  #when someone visits the homepage,runs the function below
# def home():
#     return render_template('index.html')  #index.html is the structure of the page running
# #render_template search the templates folder for the index.html file,return 
# if __name__ == '__main__':
#     app.run(debug=True)

# #Browser asks Flask "what do I show?"
# #Flask processes it and returns index.html
# #Browser receives it and displays it

# #app.py is the instructions for flask.
# #It tells Flask:
# #what to deliver (index.html)
# #when to deliver it (when someone visits /)
# #where to deliver it next (when someone visits /recommend)


# #From the index.html page,when someone click the button search
# #User clicks Search → browser sends the data to /recommend
# #app.py receives it and runs the recommend function
# #app.py tells Flask what to deliver back
# #Flask delivers the results page to the browser
# #Browser displays it
# #app.py is the backend,which is the logic of what happen when We do an action
# #html is the structure,like the button,texts,inputs
# #css is what makes it pretty such as colours,layouts

# from flask import Flask, render_template, request #render_template lets us load html files
#                                                   #request let us read data the user send

# TMDB_API_KEY = "db173039059c81787b2e9599100983ec"

# app = Flask(__name__)

# @app.route('/')  #when someone visits the homepage,runs the function below
# def home():
#     return render_template('index.html')  #index.html is the structure of the page running
# #render_template search the templates folder for the index.html file,return 
# if __name__ == '__main__':
#     app.run(debug=True)

# #Browser asks Flask "what do I show?"
# #Flask processes it and returns index.html
# #Browser receives it and displays it

# #app.py is the instructions for flask.
# #It tells Flask:
# #what to deliver (index.html)
# #when to deliver it (when someone visits /)
# #where to deliver it next (when someone visits /recommend)


# #From the index.html page,when someone click the button search
# #User clicks Search → browser sends the data to /recommend
# #app.py receives it and runs the recommend function
# #app.py tells Flask what to deliver back
# #Flask delivers the results page to the browser
# #Browser displays it
