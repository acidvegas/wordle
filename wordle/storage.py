#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# wordle/storage.py

import json
import os
import secrets
import sqlite3
import threading
import time


DATA_DIR      = os.environ.get('WORDLE_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data'))
DB_PATH       = os.path.join(DATA_DIR, 'wordle.db')
DB_LOCK       = threading.RLock()
LEADERBOARD_N = 50
MIN_GAMES     = 5 # games played before showing on the all-time board

os.makedirs(DATA_DIR, exist_ok=True)


def db_connect() -> sqlite3.Connection:
	'''Open a connection to the database.'''

	conn = sqlite3.connect(DB_PATH)
	conn.row_factory = sqlite3.Row
	return conn


def get_seed() -> str:
	'''Return the secret that orders the daily answers, creating it on first run.'''

	with DB_LOCK, db_connect() as c:
		row = c.execute("SELECT value FROM meta WHERE key='seed'").fetchone()
		if row:
			return row['value']
		seed = secrets.token_hex(16)
		c.execute("INSERT INTO meta(key, value) VALUES('seed', ?)", (seed,))
		return seed


def init_db():
	'''Create tables.'''

	with DB_LOCK, db_connect() as c:
		c.execute('''CREATE TABLE IF NOT EXISTS users (
			id            INTEGER PRIMARY KEY AUTOINCREMENT,
			username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
			password_hash TEXT NOT NULL,
			api_key_hash  TEXT UNIQUE,
			created_at    INTEGER NOT NULL
		)''')
		c.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires_at INTEGER NOT NULL)')
		c.execute('''CREATE TABLE IF NOT EXISTS games (
			puzzle     INTEGER NOT NULL,
			player     TEXT NOT NULL,
			user_id    INTEGER,
			guesses    TEXT NOT NULL,
			hard       INTEGER NOT NULL,
			solved     INTEGER NOT NULL,
			over       INTEGER NOT NULL,
			tries      INTEGER NOT NULL,
			started_at INTEGER NOT NULL,
			ended_at   INTEGER,
			PRIMARY KEY (puzzle, player)
		)''')
		c.execute('CREATE INDEX IF NOT EXISTS games_user ON games(user_id, puzzle)')
		c.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')


def leaderboard(board: str, puzzle: int) -> list:
	'''Return a leaderboard: today (fewest guesses, then earliest finish), streak or alltime.'''

	with db_connect() as c:
		if board == 'today':
			rows = c.execute('SELECT u.username, g.tries, g.hard FROM games g JOIN users u ON u.id = g.user_id WHERE g.puzzle=? AND g.solved=1 ORDER BY g.tries, g.ended_at LIMIT ?', (puzzle, LEADERBOARD_N)).fetchall()
			return [dict(r) for r in rows]
		users = {r['id']: r['username'] for r in c.execute('SELECT id, username FROM users').fetchall()}
		games = c.execute('SELECT user_id, puzzle, solved, over, tries FROM games WHERE user_id IS NOT NULL ORDER BY user_id, puzzle').fetchall()
	per_user = {}
	for g in games:
		per_user.setdefault(g['user_id'], []).append(g)
	out = []
	for user_id, rows in per_user.items():
		s = summarize(rows, puzzle)
		if board == 'streak' and s['streak'] > 0:
			out.append({'username': users[user_id], 'streak': s['streak'], 'max_streak': s['max_streak']})
		elif board == 'alltime' and s['played'] >= MIN_GAMES:
			out.append({'username': users[user_id], 'played': s['played'], 'win_pct': s['win_pct'], 'avg': s['avg']})
	if board == 'streak':
		out.sort(key=lambda r: (-r['streak'], -r['max_streak']))
	else:
		out.sort(key=lambda r: (-r['win_pct'], r['avg'] or 99, -r['played']))
	return out[:LEADERBOARD_N]


def load_game(puzzle: int, player: str) -> dict:
	'''Return a player's game for a puzzle, or None.'''

	with db_connect() as c:
		row = c.execute('SELECT * FROM games WHERE puzzle=? AND player=?', (puzzle, player)).fetchone()
	if not row:
		return None
	game            = dict(row)
	game['guesses'] = json.loads(game['guesses'])
	return game


def prune_sessions():
	'''Delete expired sessions.'''

	with DB_LOCK, db_connect() as c:
		c.execute('DELETE FROM sessions WHERE expires_at < ?', (int(time.time()),))


def save_game(game: dict):
	'''Insert or replace a game.'''

	with DB_LOCK, db_connect() as c:
		c.execute('INSERT OR REPLACE INTO games(puzzle, player, user_id, guesses, hard, solved, over, tries, started_at, ended_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (game['puzzle'], game['player'], game['user_id'], json.dumps(game['guesses']), int(game['hard']), int(game['solved']), int(game['over']), len(game['guesses']), game['started_at'], game['ended_at']))


def summarize(rows: list, puzzle: int) -> dict:
	'''Work out stats from one user's games, sorted by puzzle. Unfinished past games count as losses.

	:param rows: game rows with puzzle, solved, over and tries
	:param puzzle: today's puzzle number
	'''

	finished     = [r for r in rows if r['over'] or r['puzzle'] < puzzle]
	wins         = [r for r in finished if r['solved']]
	distribution = [0] * 6
	for r in wins:
		distribution[r['tries'] - 1] += 1
	solved     = {r['puzzle'] for r in wins}
	max_streak = run = 0
	last       = None
	for p in sorted(solved):
		run        = run + 1 if last == p - 1 else 1
		max_streak = max(max_streak, run)
		last       = p
	streak = 0
	day    = puzzle if puzzle in solved else puzzle - 1 # today unfinished doesn't break the streak
	while day in solved:
		streak += 1
		day    -= 1
	return {
		'played'       : len(finished),
		'wins'         : len(wins),
		'win_pct'      : round(100 * len(wins) / len(finished)) if finished else 0,
		'avg'          : round(sum(r['tries'] for r in wins) / len(wins), 2) if wins else None,
		'streak'       : streak,
		'max_streak'   : max_streak,
		'distribution' : distribution
	}


def user_stats(user_id: int, puzzle: int) -> dict:
	'''Return a user's stats.'''

	with db_connect() as c:
		rows = c.execute('SELECT puzzle, solved, over, tries FROM games WHERE user_id=? ORDER BY puzzle', (user_id,)).fetchall()
	return summarize(rows, puzzle)
