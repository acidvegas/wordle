#!/usr/bin/env python3
# wordle - Developed by acidvegas in Python (https://github.com/acidvegas)
# wordle/words.py

import os


WORDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'words')


def load_answers() -> list:
	'''Return the sorted list of possible daily answers.'''

	with open(os.path.join(WORDS_DIR, 'answers.txt')) as f:
		return [line.strip() for line in f if line.strip()]


def load_words() -> dict:
	'''Return every valid 5-letter guess mapped to its (part of speech, definition).'''

	words = {}
	with open(os.path.join(WORDS_DIR, 'words.txt')) as f:
		for line in f:
			word, pos, definition = line.rstrip('\n').split('\t')
			words[word] = (pos, definition)
	return words


ANSWERS = load_answers()
WORDS   = load_words()
