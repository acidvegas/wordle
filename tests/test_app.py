#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# tests/test_app.py

import os
import sys
import tempfile
import unittest

os.environ['WORDLE_DATA_DIR'] = tempfile.mkdtemp()
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'wordle'))

import app as wordle_app
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



if __name__ == '__main__':
	unittest.main()
