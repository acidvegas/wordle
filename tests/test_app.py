#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import os
import sys
import tempfile
import threading
import unittest

from unittest import mock

from werkzeug.middleware.proxy_fix import ProxyFix

os.environ['WORDLE_DATA_DIR'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'wordle'))

import app as wordle_app
import arcade
import auth
import game


class TestGame(unittest.TestCase):
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

	def test_carry_over(self):
		'''A guest's board moves to the account on sign in, so it can't be used to peek.'''

		ip = {'REMOTE_ADDR': '10.0.0.2'}
		self.client.post('/guess', json={'guess': self.wrong_word()}, environ_base=ip)
		token = self.signup('peeker')
		state = self.client.get('/state', headers={'X-Auth-Token': token}, environ_base=ip).get_json()
		self.assertEqual(len(state['guesses']), 1)

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

	def test_standalone_no_cookie(self):
		'''Standalone mode never sets the shared sign-in cookie and has no /auth/sso.'''

		token = self.signup('loner')
		for reply in (self.client.get('/auth/me', headers={'X-Auth-Token': token}), self.client.post('/auth/logout', headers={'X-Auth-Token': token})):
			self.assertNotIn(arcade.COOKIE, reply.headers.get('Set-Cookie', ''))
		self.assertEqual(self.client.get('/auth/sso').status_code, 404)


class TestArcade(unittest.TestCase):
	'''Arcade mode with the arcade module mocked out, so nothing touches the network.'''

	def setUp(self):
		self.client   = wordle_app.app.test_client()
		self.answer   = game.answer_for(game.today(), wordle_app.SEED)
		self.sessions = {}
		self.pushed   = threading.Event()
		patches = [
			mock.patch.object(arcade, 'enabled', return_value=True),
			mock.patch.object(arcade, 'ARCADE_PUBLIC', 'https://arcade.{domain}'),
			mock.patch.object(arcade, 'session', side_effect=lambda token: self.sessions.get(token)),
			mock.patch.object(arcade, 'logout'),
			mock.patch.object(arcade, 'push_stats', side_effect=lambda *a: self.pushed.set())
		]
		for p in patches:
			p.start()
			self.addCleanup(p.stop)

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
