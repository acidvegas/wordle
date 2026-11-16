#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

from unittest import mock

from werkzeug.middleware.proxy_fix import ProxyFix

os.environ['WORDLE_DATA_DIR'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'wordle'))

REAL_THREAD = threading.Thread

import app as wordle_app
import arcade
import auth
import game
import storage


def saved_game(puzzle: int, user_id: int, solved: bool, over: bool = True):
	'''Save a finished (or unfinished) two guess game for an account straight to the database.'''

	wordle_app.save_game({'puzzle': puzzle, 'player': f'u:{user_id}', 'user_id': user_id, 'guesses': [{'word': 'CRANE', 'result': []}] * 2, 'hard': False, 'solved': solved, 'over': over, 'started_at': 0, 'ended_at': 0})


class TestGame(unittest.TestCase):
	def test_answer_order(self):
		'''The cached answer order gives the same answers the uncached one did, and is only worked out once per seed.'''

		self.assertEqual([game.answer_for(p, 'test-seed') for p in (1, 2, 3, 100, 2146, 2147, 5000)], ['VEGAN', 'SCOOP', 'AMIGO', 'FOLIC', 'FRANC', 'VEGAN', 'SHALL'])
		self.assertIs(game.answer_order('test-seed'), game.answer_order('test-seed'))

	def test_hard_mode(self):
		'''Greens must stay put and yellows must be reused.'''

		prev = [{'word': 'CRANE', 'result': game.score('CRANE', 'CRAMP')}]
		self.assertEqual(game.hard_mode_error(prev, 'BLIMP'), '1st letter must be C')
		self.assertIsNone(game.hard_mode_error(prev, 'CRAMP'))
		prev = [{'word': 'SLATE', 'result': game.score('SLATE', 'HOTEL')}]
		self.assertEqual(game.hard_mode_error(prev, 'TEMPO'), 'guess must contain L')

	def test_score_repeats(self):
		'''Repeated letters only light up as many times as the answer has them.'''

		self.assertEqual(game.score('SPEED', 'ABIDE'), ['absent', 'absent', 'present', 'absent', 'present'])
		self.assertEqual(game.score('EERIE', 'THREE'), ['present', 'absent', 'correct', 'absent', 'correct'])
		self.assertEqual(game.score('LLAMA', 'HELLO'), ['present', 'present', 'absent', 'absent', 'absent'])
		self.assertEqual(game.score('CRANE', 'CRANE'), ['correct'] * 5)


class TestApp(unittest.TestCase):
	def setUp(self):
		self.client = wordle_app.app.test_client()
		self.answer = game.answer_for(game.today(), wordle_app.SEED)
		auth.ATTEMPTS.clear()

	def signup(self, name: str) -> str:
		'''Create an account and return its session token.'''

		cid  = auth.captcha_create()['captcha_id']
		text = auth.CAPTCHAS[cid][0]
		return self.client.post('/auth/signup', json={'username': name, 'password': 'password123', 'captcha_id': cid, 'captcha': text}).get_json()['token']

	def wrong_word(self) -> str:
		'''Return a valid guess that isn't the answer.'''

		return 'CRANE' if self.answer != 'CRANE' else 'SLATE'

	def test_api_key(self):
		'''The API needs a key and plays the same board as the account.'''

		self.assertEqual(self.client.get('/api/state').status_code, 401)
		token = self.signup('bot_owner')
		key   = self.client.post('/auth/apikey', headers={'X-Auth-Token': token}).get_json()['api_key']
		bot   = {'Authorization': f'Bearer {key}'}
		self.client.post('/api/guess', json={'guess': self.wrong_word()}, headers=bot)
		self.assertEqual(len(self.client.get('/state', headers={'X-Auth-Token': token}).get_json()['guesses']), 1)
		new_key = self.client.post('/auth/apikey', headers={'X-Auth-Token': token}).get_json()['api_key']
		self.assertEqual(self.client.get('/api/state', headers=bot).status_code, 401)
		self.assertEqual(self.client.get('/api/state', headers={'Authorization': f'Bearer {new_key}'}).status_code, 200)

	def test_captcha_limits(self):
		'''The captcha is drawn as paths, the store is capped and one IP gets 30 a minute.'''

		self.assertNotIn('<text', auth.captcha_create()['svg'])
		with mock.patch.object(auth, 'CAPTCHA_MAX', 3):
			for _ in range(5):
				auth.captcha_create()
			self.assertEqual(len(auth.CAPTCHAS), 3)
		ip = {'REMOTE_ADDR': '10.0.1.1'}
		for _ in range(30):
			self.assertEqual(self.client.get('/auth/captcha', environ_base=ip).status_code, 200)
		self.assertEqual(self.client.get('/auth/captcha', environ_base=ip).status_code, 429)
		self.assertEqual(self.client.get('/auth/captcha', environ_base={'REMOTE_ADDR': '10.0.1.2'}).status_code, 200)

	def test_carry_over(self):
		'''A guest's board moves to the account on sign in, so it can't be used to peek.'''

		ip = {'REMOTE_ADDR': '10.0.0.2'}
		self.client.post('/guess', json={'guess': self.wrong_word()}, environ_base=ip)
		token = self.signup('peeker')
		state = self.client.get('/state', headers={'X-Auth-Token': token}, environ_base=ip).get_json()
		self.assertEqual(len(state['guesses']), 1)

	def test_login_limits(self):
		'''Ten failed logins lock an account even with the right password, and other accounts still work.'''

		self.signup('locked')
		self.signup('unlocked')
		for _ in range(10):
			self.assertEqual(self.client.post('/auth/login', json={'username': 'locked', 'password': 'wrongpass1'}).status_code, 401)
		self.assertEqual(self.client.post('/auth/login', json={'username': 'locked', 'password': 'password123'}).status_code, 429)
		self.assertEqual(self.client.post('/auth/login', json={'username': 'unlocked', 'password': 'password123'}).status_code, 200)

	def test_forwarded_host_and_jsonld(self):
		'''Behind a proxy a forged X-Forwarded-Host doesn't change the canonical URL, and the JSON-LD can't close its script tag.'''

		spec    = importlib.util.spec_from_file_location('app_proxied', wordle_app.__file__)
		proxied = importlib.util.module_from_spec(spec)
		with mock.patch.dict(os.environ, {'WORDLE_PROXIES': '1'}), mock.patch.dict(sys.modules, {'app_proxied': proxied}):
			spec.loader.exec_module(proxied) # a second copy of the app, set up as WORDLE_PROXIES=1 would
		page = proxied.app.test_client().get('/', headers={'Host': 'wordle.example', 'X-Forwarded-Host': 'evil.example'}).get_data(as_text=True)
		self.assertIn('<link rel="canonical" href="http://wordle.example/">', page)
		self.assertNotIn('evil.example', page)
		with mock.patch.object(wordle_app, 'PUBLIC_URL', 'https://wordle.example/</script><script>alert(1)</script>'):
			page = self.client.get('/').get_data(as_text=True)
		self.assertNotIn('</script><script>alert(1)', page)
		jsonld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
		self.assertEqual(jsonld['url'], 'https://wordle.example/</script><script>alert(1)</script>/')

	def test_leaderboard_cache(self):
		'''The streak and alltime boards are cached for a minute.'''

		storage.BOARDS.clear()
		token = self.signup('cached_board')
		with wordle_app.db_connect() as c:
			user_id = c.execute("SELECT id FROM users WHERE username='cached_board'").fetchone()['id']
		before = {board: self.client.get(f'/leaderboard?board={board}').get_json()['players'] for board in ('streak', 'alltime')}
		for puzzle in range(game.today() - 5, game.today()):
			saved_game(puzzle, user_id, True)
		for board in ('streak', 'alltime'):
			self.assertEqual(self.client.get(f'/leaderboard?board={board}').get_json()['players'], before[board])
		with mock.patch.object(storage.time, 'time', return_value=time.time() + 61):
			for board in ('streak', 'alltime'):
				self.assertIn('cached_board', [p['username'] for p in self.client.get(f'/leaderboard?board={board}').get_json()['players']])
		self.assertIsNotNone(token)

	def test_peek_and_claim(self):
		'''A longer guest board replaces the account's own, so finishing as a guest can't be used to peek, and only one account can claim it.'''

		ip     = {'REMOTE_ADDR': '10.0.0.3'}
		token  = self.signup('peeker2')
		signed = {'X-Auth-Token': token}
		self.client.post('/guess', json={'guess': self.wrong_word()}, headers=signed, environ_base=ip)
		for _ in range(6):
			self.client.post('/guess', json={'guess': self.wrong_word()}, environ_base=ip)
		state = self.client.get('/state', headers=signed, environ_base=ip).get_json()
		self.assertTrue(state['over'] and not state['solved'])
		self.assertEqual(self.client.post('/guess', json={'guess': self.answer}, headers=signed, environ_base=ip).status_code, 409)
		alt = self.client.get('/state', headers={'X-Auth-Token': self.signup('peeker3')}, environ_base=ip).get_json()
		self.assertEqual(alt['guesses'], [])


	def test_guest_game(self):
		'''Guests play by IP, bad guesses don't count and the answer stays hidden until the end.'''

		ip = {'REMOTE_ADDR': '10.0.0.1'}
		self.assertEqual(self.client.post('/guess', json={'guess': 'ZZZZZ'}, environ_base=ip).get_json()['error'], 'not in word list')
		self.assertEqual(self.client.post('/guess', json={'guess': self.wrong_word(), 'puzzle': game.today() - 1}, environ_base=ip).status_code, 409)
		for _ in range(5):
			state = self.client.post('/guess', json={'guess': self.wrong_word()}, environ_base=ip).get_json()
			self.assertNotIn('answer', state)
		self.assertEqual(len(state['guesses']), 5)
		state = self.client.post('/guess', json={'guess': self.answer}, environ_base=ip).get_json()
		self.assertTrue(state['solved'] and state['over'])
		self.assertEqual(state['answer'], self.answer)
		self.assertEqual(self.client.post('/guess', json={'guess': self.answer}, environ_base=ip).status_code, 409)
		self.assertTrue(self.client.get('/state', environ_base={'REMOTE_ADDR': '10.0.0.9'}).get_json()['guesses'] == [])

	def test_stats_and_leaderboard(self):
		'''A signed in solve shows up in stats and today's leaderboard.'''

		token = self.signup('solver')
		self.client.post('/guess', json={'guess': self.answer, 'hard': True}, headers={'X-Auth-Token': token})
		stats = self.client.get('/stats', headers={'X-Auth-Token': token}).get_json()
		self.assertEqual((stats['played'], stats['wins'], stats['streak'], stats['distribution'][0]), (1, 1, 1, 1))
		board = self.client.get('/leaderboard?board=today').get_json()['players']
		self.assertIn({'username': 'solver', 'tries': 1, 'hard': 1}, board)
		self.assertEqual(self.client.get('/stats').status_code, 401)

	def test_prune(self):
		'''The hourly prune drops expired sessions and guest games more than 2 days old, and keeps everything else.'''

		today = game.today()
		for puzzle, player, user_id in ((today - 3, 'ip:10.0.2.1', None), (today - 2, 'ip:10.0.2.2', None), (today - 30, 'u:1', 1)):
			wordle_app.save_game({'puzzle': puzzle, 'player': player, 'user_id': user_id, 'guesses': [], 'hard': False, 'solved': False, 'over': False, 'started_at': 0, 'ended_at': None})
		with wordle_app.db_connect() as c:
			c.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES('old_session', 1, 1), ('new_session', 1, ?)", (int(time.time()) + 60,))
		with mock.patch.object(wordle_app.time, 'sleep', side_effect=StopIteration):
			with self.assertRaises(StopIteration):
				wordle_app.background_prune()
		with wordle_app.db_connect() as c:
			self.assertEqual([r['player'] for r in c.execute("SELECT player FROM games WHERE player IN ('ip:10.0.2.1', 'ip:10.0.2.2') OR (player='u:1' AND puzzle=?) ORDER BY player", (today - 30,))], ['ip:10.0.2.2', 'u:1'])
			self.assertEqual([r['token'] for r in c.execute("SELECT token FROM sessions WHERE token IN ('old_session', 'new_session')")], ['new_session'])

	def test_seo_urls(self):
		'''Canonical, og:url, JSON-LD, robots.txt and the sitemap use WORDLE_PUBLIC_URL when set and the request host when not.'''

		for public, root in (('', 'http://wordle.example/'), ('https://wordle.acid.vegas', 'https://wordle.acid.vegas/')):
			with mock.patch.object(wordle_app, 'PUBLIC_URL', public):
				page = self.client.get('/', headers={'Host': 'wordle.example'}).get_data(as_text=True)
				self.assertIn(f'<link rel="canonical" href="{root}">', page)
				self.assertIn(f'<meta property="og:url" content="{root}">', page)
				jsonld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
				self.assertEqual((jsonld['url'], jsonld['image']), (root, f'{root}static/og.png'))
				self.assertIn(f'Sitemap: {root}sitemap.xml', self.client.get('/robots.txt', headers={'Host': 'wordle.example'}).get_data(as_text=True))
				self.assertIn(f'<loc>{root}</loc>', self.client.get('/sitemap.xml', headers={'Host': 'wordle.example'}).get_data(as_text=True))

	def test_signup_limit(self):
		'''One IP can make 5 accounts an hour.'''

		for i in range(5):
			self.signup(f'many{i}')
		cid = auth.captcha_create()['captcha_id']
		self.assertEqual(self.client.post('/auth/signup', json={'username': 'many5', 'password': 'password123', 'captcha_id': cid, 'captcha': auth.CAPTCHAS[cid][0]}).status_code, 429)

	def test_standalone_no_cookie(self):
		'''Standalone mode never sets the shared sign-in cookie and has no /auth/sso.'''

		token = self.signup('loner')
		for reply in (self.client.get('/auth/me', headers={'X-Auth-Token': token}), self.client.post('/auth/logout', headers={'X-Auth-Token': token})):
			self.assertNotIn(arcade.COOKIE, reply.headers.get('Set-Cookie', ''))
		self.assertEqual(self.client.get('/auth/sso').status_code, 404)

	def test_standalone_sends_nothing(self):
		'''Standalone mode never talks to acid-arcade-db, even when a game is solved.'''

		auth.SEEN.clear()
		token = self.signup('quiet')
		with mock.patch.object(arcade, 'call') as call:
			self.client.post('/guess', json={'guess': self.answer}, headers={'X-Auth-Token': token})
			self.client.get('/state', headers={'X-Auth-Token': token})
		call.assert_not_called()
		self.assertEqual(auth.SEEN, {})


class TestArcade(unittest.TestCase):
	'''Arcade mode with the arcade module mocked out, so nothing touches the network.'''

	def setUp(self):
		self.client   = wordle_app.app.test_client()
		self.answer   = game.answer_for(game.today(), wordle_app.SEED)
		self.sessions = {}
		self.pushed   = threading.Event()
		auth.SESSIONS.clear()
		patches = [
			mock.patch.object(arcade, 'enabled', return_value=True),
			mock.patch.object(arcade, 'ARCADE_PUBLIC', 'https://arcade.{domain}'),
			mock.patch.object(arcade, 'session', side_effect=lambda token: self.sessions.get(token)),
			mock.patch.object(arcade, 'logout'),
			mock.patch.object(arcade, 'push_stats', side_effect=lambda *a: self.pushed.set()),
			mock.patch.object(arcade, 'event'),
			mock.patch.object(wordle_app.threading, 'Thread', side_effect=self.thread)
		]
		self.threads = []
		for p in patches:
			p.start()
			self.addCleanup(p.stop)
		self.addCleanup(lambda: [t.join(5) for t in self.threads]) # runs first, so background pushes finish while arcade is still mocked

	def login(self, user_id: int, username: str) -> str:
		'''Log in through the mocked account server and return the session token.'''

		token = f'tok{user_id}'
		with mock.patch.object(arcade, 'login', return_value={'token': token, 'user_id': user_id, 'username': username}):
			resp = self.client.post('/auth/login', json={'username': username, 'password': 'password123'})
		self.assertEqual(resp.get_json(), {'token': token, 'username': username})
		self.last_cookie = resp.headers.get('Set-Cookie', '')
		self.sessions[token] = {'user_id': user_id, 'username': username, 'admin': False}
		return token

	def poll(self, changes: dict):
		'''Run one pass of the changes poller.'''

		with mock.patch.object(arcade, 'changes', return_value=changes), mock.patch.object(auth.time, 'sleep', side_effect=StopIteration):
			with self.assertRaises(StopIteration):
				auth.arcade_poll()

	def settle(self):
		'''Wait for the background pushes started so far.'''

		for t in self.threads:
			t.join(5)

	def thread(self, *args, **kwargs) -> threading.Thread:
		'''Make a real thread and remember it, so tests can wait for it.'''

		t = REAL_THREAD(*args, **kwargs)
		self.threads.append(t)
		return t

	def wrong_word(self) -> str:
		'''Return a valid guess that isn't the answer.'''

		return 'CRANE' if self.answer != 'CRANE' else 'SLATE'

	def test_api_key_and_push(self):
		'''API keys work for mirror accounts and a bot finishing the daily game pushes stats.'''

		token = self.login(1001, 'arcade_bot')
		self.assertEqual(self.client.get('/auth/me', headers={'X-Auth-Token': token}).get_json()['user'], {'username': 'arcade_bot', 'has_api_key': False, 'user_id': 1001})
		key = self.client.post('/auth/apikey', headers={'X-Auth-Token': token}).get_json()['api_key']
		self.login(1001, 'arcade_bot') # signing in again must keep the key
		self.client.post('/api/guess', json={'guess': self.answer}, headers={'Authorization': f'Bearer {key}'})
		self.assertTrue(self.pushed.wait(5))
		arcade.push_stats.assert_called_once_with(1001, 1, {'Played': 1, 'Win %': 100, 'Current streak': 1, 'Max streak': 1, 'Average guesses': 1.0})

	def test_carry_over(self):
		'''A guest's finished board moves to the arcade account on sign in and pushes stats.'''

		ip = {'REMOTE_ADDR': '10.0.1.1'}
		for _ in range(6):
			self.client.post('/guess', json={'guess': self.wrong_word()}, environ_base=ip)
		token = self.login(1002, 'arcade_guest')
		state = self.client.get('/state', headers={'X-Auth-Token': token}, environ_base=ip).get_json()
		self.assertEqual(len(state['guesses']), 6)
		self.assertTrue(self.pushed.wait(5))
		self.assertEqual(arcade.push_stats.call_args.args[:2], (1002, 0))

	def test_down_and_errors(self):
		'''The account server being down is a 503 and its refusals pass through.'''

		with mock.patch.object(arcade, 'session', side_effect=arcade.ArcadeDown('refused')):
			reply = self.client.get('/state', headers={'X-Auth-Token': 'x'})
		self.assertEqual((reply.status_code, reply.get_json()), (503, {'error': 'account server is down'}))
		with mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('wrong username or password', 401)):
			reply = self.client.post('/auth/login', json={'username': 'a', 'password': 'b'})
		self.assertEqual((reply.status_code, reply.get_json()), (401, {'error': 'wrong username or password'}))
		with mock.patch.object(arcade, 'signup', return_value={'token': 'tok1003', 'user_id': 1003, 'username': 'arcade_new'}) as signup:
			reply = self.client.post('/auth/signup', json={'username': 'arcade_new', 'password': 'password123', 'captcha_id': 'c', 'captcha': 'ABCDE'}).get_json()
		signup.assert_called_once_with('arcade_new', 'password123', 'c', 'ABCDE', '127.0.0.1')
		self.assertEqual(reply, {'token': 'tok1003', 'username': 'arcade_new'})
		self.assertEqual(self.client.get('/state', headers={'X-Auth-Token': 'unknown'}).get_json()['guesses'], [])

	def test_session_cache(self):
		'''Session lookups are cached for 30 seconds, the mirror row is only written on a rename and logout drops the cached token.'''

		token  = self.login(1011, 'arcade_cached')
		signed = {'X-Auth-Token': token}
		with mock.patch.object(auth, 'mirror_user', wraps=auth.mirror_user) as mirror:
			self.client.get('/state', headers=signed)
			self.client.get('/state', headers=signed)
			self.assertEqual(arcade.session.call_count, 1)
			mirror.assert_not_called()
			with mock.patch.object(auth.time, 'time', return_value=time.time() + 31):
				self.client.get('/state', headers=signed)
			self.assertEqual(arcade.session.call_count, 2)
			self.client.post('/auth/logout', headers=signed)
			self.assertNotIn(token, auth.SESSIONS)
			self.sessions[token]['username'] = 'arcade_cached2'
			self.assertEqual(self.client.get('/auth/me', headers=signed).get_json()['user']['username'], 'arcade_cached2')
			self.assertEqual(arcade.session.call_count, 3)
			mirror.assert_called_once_with(1011, 'arcade_cached2')

	def test_shared_sign_in(self):
		'''Login and /auth/me share the sign-in cookie, /auth/sso swaps it for a session and logout clears it.'''

		token = self.login(1005, 'arcade_sso')
		self.assertIn(f'{arcade.COOKIE}={token}', self.last_cookie)
		self.client.delete_cookie(arcade.COOKIE)
		self.assertIn(f'{arcade.COOKIE}={token}', self.client.get('/auth/me', headers={'X-Auth-Token': token}).headers.get('Set-Cookie', ''))
		self.assertNotIn('Set-Cookie', self.client.get('/auth/me', headers={'X-Auth-Token': token}).headers)
		with mock.patch.object(arcade, 'sso', return_value={'token': 'tok_shared', 'user_id': 1005, 'username': 'arcade_sso'}) as sso:
			reply = self.client.get('/auth/sso')
		sso.assert_called_once_with(token)
		self.assertEqual(reply.get_json(), {'token': 'tok_shared', 'username': 'arcade_sso'})
		reply = self.client.post('/auth/logout', headers={'X-Auth-Token': token})
		arcade.logout.assert_called_once_with(token)
		self.assertIn(f'{arcade.COOKIE}=;', reply.headers.get('Set-Cookie', ''))
		with mock.patch.object(arcade, 'sso', return_value=None) as sso:
			reply = self.client.get('/auth/sso')
		sso.assert_called_once_with(None)
		self.assertEqual(reply.status_code, 401)

	def test_events(self):
		'''A solve posts one feed line, a 7 day streak posts a streak line and a loss posts nothing.'''

		token = self.login(1006, 'arcade_solver')
		self.client.post('/guess', json={'guess': self.answer}, headers={'X-Auth-Token': token})
		self.settle()
		arcade.event.assert_called_once_with(1006, 'solve', f'solved Wordle #{game.today()} in 1/6')
		arcade.event.reset_mock()
		for puzzle in range(game.today() - 6, game.today()):
			wordle_app.save_game({'puzzle': puzzle, 'player': 'u:1007', 'user_id': 1007, 'guesses': [{'word': 'CRANE', 'result': []}] * 2, 'hard': False, 'solved': True, 'over': True, 'started_at': 0, 'ended_at': 0})
		token = self.login(1007, 'arcade_streak')
		self.client.post('/guess', json={'guess': self.wrong_word()}, headers={'X-Auth-Token': token})
		self.client.post('/guess', json={'guess': self.answer}, headers={'X-Auth-Token': token})
		self.settle()
		self.assertEqual(arcade.event.call_args_list, [mock.call(1007, 'solve', f'solved Wordle #{game.today()} in 2/6'), mock.call(1007, 'streak', 'is on a 7 day Wordle streak')])
		arcade.event.reset_mock()
		token = self.login(1008, 'arcade_loser')
		for _ in range(6):
			self.client.post('/guess', json={'guess': self.wrong_word()}, headers={'X-Auth-Token': token})
		self.settle()
		self.assertEqual(arcade.push_stats.call_args.args[:2], (1008, 0))
		arcade.event.assert_not_called()

	def test_presence(self):
		'''Players with a signed in request in the last minute are reported, older ones drop off.'''

		auth.SEEN.clear()
		token = self.login(1009, 'arcade_online')
		self.client.get('/state', headers={'X-Auth-Token': token})
		auth.SEEN[1010] = 0 # last seen long ago
		with mock.patch.object(arcade, 'presence') as presence, mock.patch.object(auth.time, 'sleep', side_effect=StopIteration):
			with self.assertRaises(StopIteration):
				auth.arcade_presence()
		presence.assert_called_once_with([1009])

	def test_ip_and_public_url(self):
		'''Behind a trusted proxy the player's IP reaches acid-arcade-db, a 429 passes through and the arcade link follows the host.'''

		proxied = {'X-Forwarded-For': '203.0.113.7'}
		with mock.patch.object(wordle_app.app, 'wsgi_app', ProxyFix(wordle_app.app.wsgi_app, x_for=1)): # WORDLE_PROXIES=1
			with mock.patch.object(arcade, 'login', side_effect=arcade.ArcadeError('too many failed logins, try again later', 429)) as login:
				reply = self.client.post('/auth/login', json={'username': 'a', 'password': 'b'}, headers=proxied)
			login.assert_called_once_with('a', 'b', '203.0.113.7')
			self.assertEqual((reply.status_code, reply.get_json()), (429, {'error': 'too many failed logins, try again later'}))
			with mock.patch.object(arcade, 'signup', side_effect=arcade.ArcadeError('too many accounts from your address', 429)) as signup:
				reply = self.client.post('/auth/signup', json={'username': 'a', 'password': 'b', 'captcha_id': 'c', 'captcha': 'd'}, headers=proxied)
			signup.assert_called_once_with('a', 'b', 'c', 'd', '203.0.113.7')
			self.assertEqual(reply.status_code, 429)
		for host, url in (('wordle.supernets.org', 'https://arcade.supernets.org'), ('wordle.acid.vegas', 'https://arcade.acid.vegas')):
			self.assertIn(f'data-arcade="{url}"', self.client.get('/', headers={'Host': host}).get_data(as_text=True))

	def test_streak_rollover(self):
		'''When the puzzle moves on, players whose streak just broke get their stats pushed and nobody else does.'''

		base = 10_000 # far past the games other tests save, so only these players have a streak
		for puzzle, user_id, solved, over in ((base - 2, 2001, True, True), (base - 1, 2001, True, True), (base - 1, 2002, True, True), (base, 2002, True, True), (base - 1, 2003, True, True), (base, 2003, False, False), (base - 1, 2004, False, True)):
			saved_game(puzzle, user_id, solved, over)
		wordle_app.push_broken_streaks(base, base + 1)
		self.assertEqual(arcade.push_stats.call_args_list, [
			mock.call(2001, 0, {'Played': 2, 'Win %': 100, 'Current streak': 0, 'Max streak': 2, 'Average guesses': 2.0}),
			mock.call(2003, 0, {'Played': 2, 'Win %': 50, 'Current streak': 0, 'Max streak': 1, 'Average guesses': 2.0})
		])
		with mock.patch.object(wordle_app.wordle, 'today', side_effect=[base, base, base + 1]), mock.patch.object(wordle_app, 'push_broken_streaks') as push, mock.patch.object(wordle_app.time, 'sleep', side_effect=[None, None, StopIteration]):
			with self.assertRaises(StopIteration):
				wordle_app.streak_rollover()
		push.assert_called_once_with(base, base + 1)

	def test_rename_and_delete(self):
		'''Renames show on the leaderboard and deleted accounts lose everything.'''

		token = self.login(1004, 'arcade_old')
		self.client.post('/guess', json={'guess': self.answer}, headers={'X-Auth-Token': token})
		self.assertTrue(self.pushed.wait(5))
		self.poll({'now': 1, 'changed': [{'user_id': 1004, 'username': 'arcade_renamed'}], 'deleted': []})
		board = self.client.get('/leaderboard?board=today').get_json()['players']
		self.assertIn({'user_id': 1004, 'username': 'arcade_renamed', 'tries': 1, 'hard': 0}, board)
		self.poll({'now': 2, 'changed': [], 'deleted': [1004]})
		with wordle_app.db_connect() as c:
			self.assertIsNone(c.execute('SELECT 1 FROM users WHERE id=1004').fetchone())
			self.assertIsNone(c.execute('SELECT 1 FROM games WHERE user_id=1004').fetchone())



if __name__ == '__main__':
	unittest.main()
