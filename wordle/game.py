#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# wordle/game.py

import datetime
import random

from words import ANSWERS


EPOCH       = datetime.date(2026, 11, 3) # puzzle #1
MAX_GUESSES = 6
WORD_LEN    = 5
ORDINALS    = ['1st', '2nd', '3rd', '4th', '5th']


def answer_for(puzzle: int, seed: str) -> str:
	'''Return the answer for a puzzle number. The seed keeps the order secret even though the list is public.'''

	order = sorted(ANSWERS)
	random.Random(seed).shuffle(order)
	return order[(puzzle - 1) % len(order)]


def hard_mode_error(previous: list, guess: str) -> str:
	'''Return why a guess breaks hard mode, or None. Every revealed hint must be used.

	:param previous: earlier guesses as dicts with 'word' and 'result'
	:param guess: the new guess
	'''

	for prev in previous:
		for i, (letter, result) in enumerate(zip(prev['word'], prev['result'])):
			if result == 'correct' and guess[i] != letter:
				return f'{ORDINALS[i]} letter must be {letter}'
		for letter in set(prev['word']):
			needed = sum(1 for l, r in zip(prev['word'], prev['result']) if l == letter and r != 'absent')
			if guess.count(letter) < needed:
				return f'guess must contain {letter}'
	return None


def puzzle_date(puzzle: int) -> datetime.date:
	'''Return the UTC date of a puzzle number.'''

	return EPOCH + datetime.timedelta(days=puzzle - 1)


def score(guess: str, answer: str) -> list:
	'''Color a guess against the answer, handling repeated letters the way Wordle does.'''

	result    = ['absent'] * WORD_LEN
	remaining = []
	for i in range(WORD_LEN):
		if guess[i] == answer[i]:
			result[i] = 'correct'
		else:
			remaining.append(answer[i])
	for i in range(WORD_LEN):
		if result[i] != 'correct' and guess[i] in remaining:
			result[i] = 'present'
			remaining.remove(guess[i])
	return result


def seconds_to_next() -> int:
	'''Return seconds until the next puzzle (midnight UTC).'''

	now = datetime.datetime.now(datetime.timezone.utc)
	return int((datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(), datetime.timezone.utc) - now).total_seconds())


def today() -> int:
	'''Return today's puzzle number (UTC).'''

	return (datetime.datetime.now(datetime.timezone.utc).date() - EPOCH).days + 1
