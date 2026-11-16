#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# wordle/app.py

import json
import os
import re
import threading
import time

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import arcade
import game as wordle
from auth import USERNAME_RE, api_key_user, arcade_poll, arcade_presence, attempt, blocked, captcha_create, captcha_verify, create_api_key, create_session, delete_session, mirror_user, request_token, request_user, uncache_session
from storage import DB_LOCK, broken_streaks, db_connect, delete_game, get_seed, init_db, leaderboard, load_game, prune_guests, prune_sessions, save_game, user_stats
from words import WORDS


app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 # no request needs more than a few KB

CACHE_VERSION = int(time.time())
GUESS_RE      = re.compile(r'^[A-Z]{5}$')
STREAK_EVENTS = (7, 14, 30, 50, 100) # streaks that get an activity feed line
PUBLIC_URL    = os.environ.get('WORDLE_PUBLIC_URL', '').rstrip('/') # canonical site for crawlers and share cards, e.g. https://wordle.acid.vegas
PROXIES       = int(os.environ.get('WORDLE_PROXIES', '0')) # reverse proxies in front of the app, so the real client IP is used

if PROXIES:
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXIES, x_proto=PROXIES) # not x_host, so a forged X-Forwarded-Host can't change the canonical and share URLs


def api_error(message: str, status: int = 400) -> tuple:
	'''Return a JSON error response.'''

	return jsonify({'error': message}), status


@app.errorhandler(arcade.ArcadeDown)
def arcade_down(e: arcade.ArcadeDown) -> tuple:
	'''Fail hard when acid-arcade-db can't be reached.'''

	return api_error('account server is down', 503)


@app.errorhandler(arcade.ArcadeError)
def arcade_error(e: arcade.ArcadeError) -> tuple:
	'''Pass an acid-arcade-db refusal on to the player.'''

	return api_error(str(e), e.status)


def arcade_stats(s: dict) -> dict:
	'''Return the profile summary acid-arcade-db shows for a player, from user_stats().'''

	stats = {'Played': s['played'], 'Win %': s['win_pct'], 'Current streak': s['streak'], 'Max streak': s['max_streak']}
	if s['avg'] is not None:
		stats['Average guesses'] = s['avg']
	return stats


@app.route('/api/guess', methods=['POST'])
def api_guess():
	'''Make a guess on today's puzzle with an API key.'''

	user_id, _ = api_key_user()
	if not user_id:
		return api_error('missing or invalid API key', 401)
	return make_guess(f'u:{user_id}', user_id)


@app.route('/api/state')
def api_state():
	'''Return today's game with an API key.'''

	user_id, _ = api_key_user()
	if not user_id:
		return api_error('missing or invalid API key', 401)
	return jsonify(public_state(get_game(f'u:{user_id}', user_id)))


@app.route('/auth/apikey', methods=['POST'])
def auth_apikey():
	'''Create a new API key for the signed in user. The old key stops working.'''

	user_id, _ = request_user()
	if not user_id:
		return api_error('sign in first', 401)
	return jsonify({'api_key': create_api_key(user_id)})


@app.route('/auth/captcha')
def auth_captcha():
	'''Return a new captcha.'''

	if arcade.enabled():
		return jsonify(arcade.captcha())
	if blocked('captcha', request.remote_addr):
		return api_error('too many captchas, wait a minute', 429)
	attempt('captcha', request.remote_addr)
	return jsonify(captcha_create())


@app.route('/auth/login', methods=['POST'])
def auth_login():
	'''Log in with username and password.'''

	data = request.get_json(silent=True) or {}
	if arcade.enabled():
		reply = arcade.login(data.get('username') or '', data.get('password') or '', request.remote_addr)
		mirror_user(reply['user_id'], reply['username'])
		resp = jsonify({'token': reply['token'], 'username': reply['username']})
		arcade.set_cookie(resp, request.host, reply['token'])
		return resp
	name = (data.get('username') or '').lower()
	if blocked('account', name) or blocked('ip', request.remote_addr):
		return api_error('too many failed logins, try again in 15 minutes', 429)
	with db_connect() as c:
		row = c.execute('SELECT id, username, password_hash FROM users WHERE username=?', (data.get('username') or '',)).fetchone()
	if not row or not check_password_hash(row['password_hash'], data.get('password') or ''):
		attempt('account', name)
		attempt('ip', request.remote_addr)
		return api_error('wrong username or password', 401)
	prune_sessions()
	return jsonify({'token': create_session(row['id']), 'username': row['username']})


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
	'''Log out the current session.'''

	resp = jsonify({'ok': True})
	if arcade.enabled():
		uncache_session(request_token())
		arcade.logout(request_token())
		arcade.clear_cookie(resp, request.host)
	else:
		delete_session(request_token())
	return resp


@app.route('/auth/me')
def auth_me():
	'''Return the signed in user, if any.'''

	user_id, username = request_user()
	if not user_id:
		return jsonify({'user': None})
	with db_connect() as c:
		has_key = c.execute('SELECT api_key_hash IS NOT NULL AS k FROM users WHERE id=?', (user_id,)).fetchone()['k']
	user = {'username': username, 'has_api_key': bool(has_key)}
	if arcade.enabled():
		user['user_id'] = user_id
	resp = jsonify({'user': user})
	if arcade.enabled() and not request.cookies.get(arcade.COOKIE): # share a sign-in made before shared sign-in existed
		arcade.set_cookie(resp, request.host, request_token())
	return resp


@app.route('/auth/signup', methods=['POST'])
def auth_signup():
	'''Create an account.'''

	data     = request.get_json(silent=True) or {}
	username = (data.get('username') or '').strip()
	password = data.get('password') or ''
	if arcade.enabled():
		reply = arcade.signup(username, password, data.get('captcha_id') or '', data.get('captcha') or '', request.remote_addr)
		mirror_user(reply['user_id'], reply['username'])
		resp = jsonify({'token': reply['token'], 'username': reply['username']})
		arcade.set_cookie(resp, request.host, reply['token'])
		return resp
	if blocked('signup', request.remote_addr):
		return api_error('too many new accounts from your address, try again later', 429)
	if not captcha_verify(data.get('captcha_id'), data.get('captcha')):
		return api_error('wrong captcha')
	if not USERNAME_RE.match(username):
		return api_error('username must be 3-16 letters, numbers or _')
	if len(password) < 8 or len(password) > 128:
		return api_error('password must be 8-128 characters')
	with DB_LOCK, db_connect() as c:
		if c.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
			return api_error('username taken')
		user_id = c.execute('INSERT INTO users(username, password_hash, created_at) VALUES(?, ?, ?)', (username, generate_password_hash(password), int(time.time()))).lastrowid
	attempt('signup', request.remote_addr)
	return jsonify({'token': create_session(user_id), 'username': username})


@app.route('/auth/sso')
def auth_sso():
	'''Swap the shared sign-in cookie set by any site on this domain for a session here. Arcade mode only.'''

	if not arcade.enabled():
		abort(404)
	reply = arcade.sso(request.cookies.get(arcade.COOKIE))
	if not reply:
		resp, status = api_error('not signed in', 401)
		arcade.clear_cookie(resp, request.host)
		return resp, status
	mirror_user(reply['user_id'], reply['username'])
	return jsonify({'token': reply['token'], 'username': reply['username']})


def background_prune():
	'''Delete expired sessions and old guest games every hour. Runs in a daemon thread.'''

	while True:
		try:
			prune_sessions()
			prune_guests(wordle.today())
		except Exception as e:
			print(f'prune failed: {e}')
		time.sleep(3600)


def get_game(player: str, user_id: int) -> dict:
	'''Return the player's game for today, or a fresh unsaved one.'''

	puzzle = wordle.today()
	return load_game(puzzle, player) or {'puzzle': puzzle, 'player': player, 'user_id': user_id, 'guesses': [], 'hard': False, 'solved': False, 'over': False, 'started_at': int(time.time()), 'ended_at': None}


@app.route('/')
def index():
	'''Serve the single page app.'''

	url    = site_url()
	jsonld = {
		'@context'            : 'https://schema.org',
		'@type'               : ['VideoGame', 'WebApplication'],
		'name'                : 'wordle',
		'url'                 : url,
		'description'         : 'Guess the hidden 5-letter word in 6 tries. A new puzzle every day at midnight UTC. Open source.',
		'image'               : f'{url}static/og.png',
		'applicationCategory' : 'GameApplication',
		'operatingSystem'     : 'Any',
		'genre'               : ['Word game', 'Puzzle'],
		'gamePlatform'        : 'Web browser',
		'playMode'            : ['SinglePlayer'],
		'isAccessibleForFree' : True,
		'offers'              : {'@type': 'Offer', 'price': '0', 'priceCurrency': 'USD'},
		'author'              : {'@type': 'Person', 'name': 'acidvegas', 'url': 'https://github.com/acidvegas'}
	}
	return render_template('index.html', cache_v=CACHE_VERSION, url=url, jsonld=json.dumps(jsonld).replace('</', '<\\/'), arcade_public_url=arcade.public_url(request.host) if arcade.enabled() else '')


@app.route('/favicon.ico')
def favicon():
	'''Serve the PNG icon for clients that only look for /favicon.ico.'''

	return send_from_directory(app.static_folder, 'icon-192.png', mimetype='image/png')


@app.route('/healthz')
def healthz():
	'''Answer ok when the app and its database respond, for the Docker healthcheck and uptime monitors.'''

	with db_connect() as c:
		c.execute('SELECT 1')
	return Response('ok\n', mimetype='text/plain')


@app.route('/robots.txt')
def robots():
	'''Let crawlers in and point them at the sitemap.'''

	return Response(f'User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /auth/\nSitemap: {site_url()}sitemap.xml\n', mimetype='text/plain')


def site_url() -> str:
	'''Return the site's root URL with a trailing slash: WORDLE_PUBLIC_URL when set, otherwise the request host.'''

	return f'{PUBLIC_URL}/' if PUBLIC_URL else request.url_root


@app.route('/sitemap.xml')
def sitemap():
	'''List the home page, the only page worth indexing.'''

	return Response(f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{site_url()}</loc></url></urlset>\n', mimetype='application/xml')


@app.route('/leaderboard')
def leaderboard_route():
	'''Return a leaderboard: today, streak or alltime.'''

	board = request.args.get('board') or 'today'
	if board not in ('today', 'streak', 'alltime'):
		return api_error('unknown board')
	players = leaderboard(board, wordle.today())
	if not arcade.enabled(): # user ids are only needed for avatars
		for p in players:
			del p['user_id']
	return jsonify({'board': board, 'puzzle': wordle.today(), 'players': players})


def make_guess(player: str, user_id: int) -> tuple:
	'''Check and record a guess for a player, then return the new state.'''

	data  = request.get_json(silent=True) or {}
	guess = str(data.get('guess') or '').strip().upper()
	with DB_LOCK:
		game = get_game(player, user_id)
		if data.get('puzzle') not in (None, game['puzzle']):
			return api_error('a new puzzle is out', 409)
		if game['over']:
			return api_error('game over, come back tomorrow', 409)
		if not GUESS_RE.match(guess):
			return api_error('guess must be 5 letters')
		if guess not in WORDS:
			return api_error('not in word list')
		if not game['guesses']:
			game['hard'] = bool(data.get('hard')) # hard mode can only be picked before the first guess
		elif game['hard'] and (error := wordle.hard_mode_error(game['guesses'], guess)):
			return api_error(error)
		answer = wordle.answer_for(game['puzzle'], SEED)
		game['guesses'].append({'word': guess, 'result': wordle.score(guess, answer)})
		game['solved'] = guess == answer
		game['over']   = game['solved'] or len(game['guesses']) >= wordle.MAX_GUESSES
		if game['over']:
			game['ended_at'] = int(time.time())
		save_game(game)
	if game['over']:
		push_stats(user_id, game)
	return jsonify(public_state(game))


def public_state(game: dict) -> dict:
	'''Return what a player may see of a game. The answer is only included once the game is over.'''

	state = {
		'puzzle'      : game['puzzle'],
		'date'        : wordle.puzzle_date(game['puzzle']).isoformat(),
		'max_guesses' : wordle.MAX_GUESSES,
		'hard'        : bool(game['hard']),
		'guesses'     : game['guesses'],
		'solved'      : bool(game['solved']),
		'over'        : bool(game['over']),
		'next_in'     : wordle.seconds_to_next()
	}
	if game['over']:
		answer              = wordle.answer_for(game['puzzle'], SEED)
		pos, definition     = WORDS[answer]
		state['answer']     = answer
		state['definition'] = {'part_of_speech': pos, 'definition': definition}
	return state


def push_broken_streaks(before: int, after: int):
	'''Send acid-arcade-db the stats of every player whose streak broke when the daily puzzle moved on, so their arcade profile shows the same streak as this site.

	:param before: the last puzzle streaks were shown for
	:param after: the new puzzle
	'''

	for user_id in broken_streaks(before, after):
		try:
			s = user_stats(user_id, after)
			arcade.push_stats(user_id, s['streak'], arcade_stats(s))
		except Exception as e:
			print(f'arcade stats push failed for user {user_id}: {e}')


def push_stats(user_id: int, game: dict):
	'''Send a player's summary and feed events for a finished daily game to acid-arcade-db in the background. Does nothing for guests or in standalone mode.'''

	if not user_id or not arcade.enabled():
		return

	def push():
		try:
			s = user_stats(user_id, wordle.today())
			arcade.push_stats(user_id, s['streak'], arcade_stats(s))
		except Exception as e:
			print(f'arcade stats push failed for user {user_id}: {e}')
			return
		if not game['solved']:
			return
		events = [('solve', f'solved Wordle #{game["puzzle"]} in {len(game["guesses"])}/{wordle.MAX_GUESSES}')]
		if s['streak'] in STREAK_EVENTS:
			events.append(('streak', f'is on a {s["streak"]} day Wordle streak'))
		for kind, text in events:
			try:
				arcade.event(user_id, kind, text)
			except Exception as e:
				print(f'arcade event failed for user {user_id}: {e}')

	threading.Thread(target=push, daemon=True).start()


@app.route('/stats')
def stats():
	'''Return the signed in user's stats.'''

	user_id, _ = request_user()
	if not user_id:
		return api_error('sign in to track stats', 401)
	return jsonify(user_stats(user_id, wordle.today()))


def streak_rollover():
	'''Check every minute for a new daily puzzle and push the streaks it broke. Runs in a daemon thread in arcade mode.'''

	puzzle = wordle.today()
	while True:
		time.sleep(60)
		if (now := wordle.today()) != puzzle:
			try:
				push_broken_streaks(puzzle, now)
			except Exception as e:
				print(f'streak rollover failed: {e}')
			puzzle = now


@app.route('/guess', methods=['POST'])
def web_guess():
	'''Make a guess from the website.'''

	return make_guess(*web_player())


def web_player() -> tuple:
	'''Return (player key, user id) for a website request: the account if signed in, otherwise the IP.

	A guest's board for today moves to the account on sign in when it went further than the account's own board, so it can't be used to peek before playing ranked.
	It then stops being a guest board, so only one account can ever claim it.
	'''

	user_id, _ = request_user()
	ip_player  = f'ip:{request.remote_addr}'
	if not user_id:
		return ip_player, None
	player = f'u:{user_id}'
	with DB_LOCK:
		puzzle = wordle.today()
		mine   = load_game(puzzle, player)
		guest  = load_game(puzzle, ip_player)
		if guest and guest['guesses'] and not (mine and mine['over']) and (not mine or guest['over'] or len(guest['guesses']) > len(mine['guesses'])):
			save_game({**guest, 'player': player, 'user_id': user_id})
			delete_game(puzzle, ip_player)
			if guest['over']:
				push_stats(user_id, guest)
	return player, user_id


@app.route('/state')
def web_state():
	'''Return today's game for the website.'''

	return jsonify(public_state(get_game(*web_player())))


init_db()
SEED = get_seed()

threading.Thread(target=background_prune, daemon=True).start()

if arcade.enabled():
	threading.Thread(target=arcade_poll, daemon=True).start()
	threading.Thread(target=arcade_presence, daemon=True).start()
	threading.Thread(target=streak_rollover, daemon=True).start()



if __name__ == '__main__':
	app.run(host='127.0.0.1', port=20004)
