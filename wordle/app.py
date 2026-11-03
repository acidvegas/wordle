#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# wordle/app.py

import os
import re
import time

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import game as wordle
from auth import USERNAME_RE, api_key_user, captcha_create, captcha_verify, create_api_key, create_session, delete_session, request_token, request_user
from storage import DB_LOCK, db_connect, get_seed, init_db, leaderboard, load_game, prune_sessions, save_game, user_stats
from words import WORDS


app = Flask(__name__)

CACHE_VERSION = int(time.time())
GUESS_RE      = re.compile(r'^[A-Z]{5}$')
PROXIES       = int(os.environ.get('WORDLE_PROXIES', '0')) # reverse proxies in front of the app, so the real client IP is used

if PROXIES:
	app.wsgi_app = ProxyFix(app.wsgi_app, x_for=PROXIES, x_proto=PROXIES, x_host=PROXIES)


def api_error(message: str, status: int = 400) -> tuple:
	'''Return a JSON error response.'''

	return jsonify({'error': message}), status


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

	return jsonify(captcha_create())


@app.route('/auth/login', methods=['POST'])
def auth_login():
	'''Log in with username and password.'''

	data = request.get_json(silent=True) or {}
	with db_connect() as c:
		row = c.execute('SELECT id, username, password_hash FROM users WHERE username=?', (data.get('username') or '',)).fetchone()
	if not row or not check_password_hash(row['password_hash'], data.get('password') or ''):
		return api_error('wrong username or password', 401)
	prune_sessions()
	return jsonify({'token': create_session(row['id']), 'username': row['username']})


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
	'''Log out the current session.'''

	delete_session(request_token())
	return jsonify({'ok': True})


@app.route('/auth/me')
def auth_me():
	'''Return the signed in user, if any.'''

	user_id, username = request_user()
	if not user_id:
		return jsonify({'user': None})
	with db_connect() as c:
		has_key = c.execute('SELECT api_key_hash IS NOT NULL AS k FROM users WHERE id=?', (user_id,)).fetchone()['k']
	return jsonify({'user': {'username': username, 'has_api_key': bool(has_key)}})


@app.route('/auth/signup', methods=['POST'])
def auth_signup():
	'''Create an account.'''

	data     = request.get_json(silent=True) or {}
	username = (data.get('username') or '').strip()
	password = data.get('password') or ''
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
	return jsonify({'token': create_session(user_id), 'username': username})


def get_game(player: str, user_id: int) -> dict:
	'''Return the player's game for today, or a fresh unsaved one.'''

	puzzle = wordle.today()
	return load_game(puzzle, player) or {'puzzle': puzzle, 'player': player, 'user_id': user_id, 'guesses': [], 'hard': False, 'solved': False, 'over': False, 'started_at': int(time.time()), 'ended_at': None}


@app.route('/')
def index():
	'''Serve the single page app.'''

	return render_template('index.html', cache_v=CACHE_VERSION, url=request.url_root)


@app.route('/leaderboard')
def leaderboard_route():
	'''Return a leaderboard: today, streak or alltime.'''

	board = request.args.get('board') or 'today'
	if board not in ('today', 'streak', 'alltime'):
		return api_error('unknown board')
	return jsonify({'board': board, 'puzzle': wordle.today(), 'players': leaderboard(board, wordle.today())})


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


@app.route('/stats')
def stats():
	'''Return the signed in user's stats.'''

	user_id, _ = request_user()
	if not user_id:
		return api_error('sign in to track stats', 401)
	return jsonify(user_stats(user_id, wordle.today()))


@app.route('/guess', methods=['POST'])
def web_guess():
	'''Make a guess from the website.'''

	return make_guess(*web_player())


def web_player() -> tuple:
	'''Return (player key, user id) for a website request: the account if signed in, otherwise the IP.

	A guest's board for today carries over when they sign in, so it can't be used to peek before playing ranked.
	'''

	user_id, _ = request_user()
	ip_player  = f'ip:{request.remote_addr}'
	if not user_id:
		return ip_player, None
	player = f'u:{user_id}'
	with DB_LOCK:
		puzzle = wordle.today()
		if not load_game(puzzle, player) and (guest := load_game(puzzle, ip_player)) and guest['guesses']:
			save_game({**guest, 'player': player, 'user_id': user_id})
	return player, user_id


@app.route('/state')
def web_state():
	'''Return today's game for the website.'''

	return jsonify(public_state(get_game(*web_player())))


init_db()
SEED = get_seed()



if __name__ == '__main__':
	app.run(host='127.0.0.1', port=20004)
