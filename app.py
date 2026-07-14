from flask import Flask, render_template, request ,redirect
import requests
import time
from dotenv import load_dotenv
import os
from pymongo import MongoClient
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
import bcrypt

load_dotenv()

TMDB_API_KEY = os.getenv('TMDB_API_KEY')
LASTFM_API_KEY = os.getenv('LASTFM_API_KEY')

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'reconation_secret_123')

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
    except Exception as e:
        print(f"Home trending error: {e}")
    
    return render_template('index.html', movies=movies, anime=anime)

@app.route('/recommend', methods=['GET', 'POST'])
def recommend():
    category = request.form.get('category') or request.args.get('category')
    query = request.form.get('query') or request.args.get('query')
    results = []

    try:
        if category == 'movie':
            search_url = f"https://api.themoviedb.org/3/search/movie?api_key={TMDB_API_KEY}&query={query}"
            search_res = requests.get(search_url).json()
            
            if search_res.get('results'):
                movie_id = search_res['results'][0]['id']
                rec_url = f"https://api.themoviedb.org/3/movie/{movie_id}/recommendations?api_key={TMDB_API_KEY}"
                rec_res = requests.get(rec_url).json()
                results = [r for r in rec_res['results'] if r['vote_average'] >= 6.0][:40]

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
            results = [n['mediaRecommendation'] for n in nodes if n['mediaRecommendation']][:15]

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
                similar_url = f"https://ws.audioscrobbler.com/2.0/?method=track.getSimilar&artist={artist}&track={track}&api_key={LASTFM_API_KEY}&format=json&limit=20"
                similar_res = requests.get(similar_url).json()
                all_results = similar_res.get('similartracks', {}).get('track', [])
                filtered = all_results[:15]
                
                for song in filtered:
                    itunes_url = f"https://itunes.apple.com/search?term={song['artist']['name']}+{song['name']}&media=music&limit=1"
                    itunes_res = requests.get(itunes_url).json()
                    if itunes_res['resultCount'] > 0:
                        song['cover'] = itunes_res['results'][0]['artworkUrl100']
                        song['preview'] = itunes_res['results'][0].get('previewUrl', None)
                    else:
                        song['cover'] = None
                        song['preview'] = None
                
                results = filtered

    except Exception as e:
        print(f"Error: {e}")
        results = []

    return render_template('results.html', results=results, category=category, query=query)

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

@app.route('/trending')
def trending():
    movies = []
    anime = []
    
    try:
        # Trending movies
        movie_url = f"https://api.themoviedb.org/3/trending/movie/week?api_key={TMDB_API_KEY}"
        movie_res = requests.get(movie_url).json()
        movies = movie_res.get('results', [])[:12]
        
        # Trending anime
        anime_query = """
        query {
            Page(perPage: 12) {
                media(sort: TRENDING_DESC, type: ANIME) {
                    title { romaji }
                    coverImage { large }
                    averageScore
                    episodes
                    genres
                    description
                }
            }
        }
        """
        anime_res = requests.post('https://graphql.anilist.co', json={'query': anime_query}).json()
        anime = anime_res['data']['Page']['media']
    except Exception as e:
        print(f"Trending error: {e}")
    
    return render_template('trending.html', movies=movies, anime=anime)

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

