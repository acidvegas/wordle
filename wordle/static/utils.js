/* wordle - Developed by acidvegas in Python (https://github.com/acidvegas) */
/* utils.js */

const $         = (id) => document.getElementById(id);
const ROWS      = 6;
const COLS      = 5;
const RANK      = { absent: 1, present: 2, correct: 3 };
const KEY_ROWS  = ['qwertyuiop', ' asdfghjkl ', '>zxcvbnm<'];
const FLIP_MS   = 300;

let authToken = store('wordle_auth') || '';
let me        = null;
let game      = null; // last state from the server
let current   = '';   // letters typed in the active row
let busy      = false;
let countdown = null;
let authMode  = 'login';
let captchaId = null;

function store(key, value) {
	try {
		if (value === undefined) return localStorage.getItem(key);
		if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
	} catch (e) { return null; }
}

async function api(path, body) {
	const opts = { headers: { 'X-Auth-Token': authToken } };
	if (body !== undefined) {
		opts.method  = 'POST';
		opts.headers['Content-Type'] = 'application/json';
		opts.body    = JSON.stringify(body);
	}
	const res  = await fetch(path, opts);
	const data = await res.json().catch(() => ({ error: 'server error' }));
	if (!res.ok && !data.error) data.error = 'server error';
	return data;
}

function toast(text, ms = 1200) {
	const el = document.createElement('div');
	el.className   = 'toast';
	el.textContent = text;
	$('toasts').prepend(el);
	setTimeout(() => { el.style.opacity = 0; setTimeout(() => el.remove(), 300); }, ms);
}

/* Board and keyboard */

function buildBoard() {
	const board = $('board');
	board.innerHTML = '';
	for (let r = 0; r < ROWS; r++) {
		const row = document.createElement('div');
		row.className = 'row';
		for (let c = 0; c < COLS; c++) row.appendChild(Object.assign(document.createElement('div'), { className: 'tile' }));
		board.appendChild(row);
	}
}

function buildKeyboard() {
	const kb = $('keyboard');
	for (const line of KEY_ROWS) {
		const row = document.createElement('div');
		row.className = 'kb-row';
		for (const ch of line) {
			if (ch === ' ') { row.appendChild(Object.assign(document.createElement('div'), { className: 'spacer' })); continue; }
			const key = document.createElement('button');
			key.className = 'key';
			if (ch === '>') { key.classList.add('wide'); key.textContent = 'Enter'; key.dataset.key = 'Enter'; }
			else if (ch === '<') { key.classList.add('wide'); key.innerHTML = '<svg><use href="#i-back"/></svg>'; key.dataset.key = 'Backspace'; key.setAttribute('aria-label', 'Backspace'); }
			else { key.textContent = ch; key.dataset.key = ch; }
			row.appendChild(key);
		}
		kb.appendChild(row);
	}
	kb.addEventListener('click', (e) => { const key = e.target.closest('.key'); if (key) { press(key.dataset.key); key.blur(); } });
}

function rowEl(r) { return $('board').children[r]; }

function paintKeyboard() {
	const best = {};
	for (const g of game.guesses) {
		[...g.word].forEach((ch, i) => {
			const l = ch.toLowerCase();
			if (!best[l] || RANK[g.result[i]] > RANK[best[l]]) best[l] = g.result[i];
		});
	}
	document.querySelectorAll('.key[data-key]').forEach((k) => {
		k.classList.remove('correct', 'present', 'absent');
		if (best[k.dataset.key]) k.classList.add(best[k.dataset.key]);
	});
}

function paintRow(r, guess) {
	[...rowEl(r).children].forEach((tile, i) => {
		tile.textContent = guess.word[i];
		tile.className   = `tile ${guess.result[i]}`;
	});
}

function paintTyping() {
	const r = game.guesses.length;
	if (r >= ROWS) return;
	[...rowEl(r).children].forEach((tile, i) => {
		tile.textContent = current[i] || '';
		tile.className   = current[i] ? 'tile filled' : 'tile';
	});
}

function render() {
	buildBoard();
	game.guesses.forEach((g, r) => paintRow(r, g));
	paintTyping();
	paintKeyboard();
	syncHard();
}

function reveal(r, guess) {
	return new Promise((resolve) => {
		const tiles = [...rowEl(r).children];
		tiles.forEach((tile, i) => {
			setTimeout(() => {
				tile.classList.add('flip');
				setTimeout(() => { tile.textContent = guess.word[i]; tile.className = `tile flip ${guess.result[i]}`; }, 250);
			}, i * FLIP_MS);
		});
		setTimeout(resolve, (COLS - 1) * FLIP_MS + 500);
	});
}

function shake() {
	const row = rowEl(game.guesses.length);
	row.classList.remove('shake');
	void row.offsetWidth;
	row.classList.add('shake');
}

/* Input */

function press(key) {
	if (busy || !game || game.over || !$('overlay').classList.contains('hidden')) return;
	if (key === 'Enter') return submit();
	if (key === 'Backspace') current = current.slice(0, -1);
	else if (/^[a-z]$/i.test(key) && current.length < COLS) current += key.toUpperCase();
	else return;
	paintTyping();
}

async function submit() {
	if (current.length < COLS) { shake(); return toast('Not enough letters'); }
	busy = true;
	const r    = game.guesses.length;
	const data = await api('/guess', { guess: current, hard: store('wordle_hard') === 'on', puzzle: game.puzzle });
	if (data.error === 'a new puzzle is out') {
		busy = false;
		toast('A new puzzle is out', 2000);
		return load();
	}
	if (data.error) {
		busy = false;
		shake();
		return toast(data.error.charAt(0).toUpperCase() + data.error.slice(1));
	}
	game    = data;
	current = '';
	await reveal(r, game.guesses[r]);
	paintKeyboard();
	syncHard();
	busy = false;
	if (game.over) finish(true);
}

function finish(fresh) {
	if (game.solved) {
		const row = rowEl(game.guesses.length - 1);
		if (fresh) {
			row.classList.add('win');
			toast(['Genius', 'Magnificent', 'Impressive', 'Splendid', 'Great', 'Phew'][game.guesses.length - 1], 1800);
		}
	} else if (fresh) {
		toast(game.answer, 3000);
	}
	setTimeout(() => openModal('stats'), fresh ? 1800 : 300);
}

document.addEventListener('keydown', (e) => {
	if (e.ctrlKey || e.metaKey || e.altKey) return;
	if (!$('overlay').classList.contains('hidden')) {
		if (e.key === 'Escape') closeModal();
		return;
	}
	if (e.key === 'Enter' || e.key === 'Backspace' || /^[a-z]$/i.test(e.key)) {
		e.preventDefault();
		press(e.key === 'Enter' || e.key === 'Backspace' ? e.key : e.key.toLowerCase());
	}
});

/* Modals */

function openModal(name) {
	document.querySelectorAll('.modal section').forEach((s) => s.classList.toggle('active', s.dataset.modal === name));
	$('overlay').classList.remove('hidden');
	if (name === 'stats') renderStats();
	if (name === 'leaderboard') loadBoard(document.querySelector('#lb-tabs .active').dataset.board);
	if (name === 'account') renderAccount();
	if (name === 'settings') syncHard();
}

function closeModal() { $('overlay').classList.add('hidden'); }

document.querySelectorAll('[data-open]').forEach((b) => b.addEventListener('click', () => openModal(b.dataset.open)));
document.querySelector('.modal-close').addEventListener('click', closeModal);
$('overlay').addEventListener('click', (e) => { if (e.target === $('overlay')) closeModal(); });

/* Stats and sharing */

async function renderStats() {
	const box = $('stats-box');
	$('result-box').classList.toggle('hidden', !game || !game.over);
	$('share-box').classList.toggle('hidden', !game || !game.over);
	if (game && game.over) {
		$('result-title').textContent = game.solved ? `Solved in ${game.guesses.length}` : 'Out of guesses';
		$('answer-word').textContent  = game.answer;
		$('definition').textContent   = game.definition && game.definition.definition ? `${game.definition.part_of_speech ? game.definition.part_of_speech + ': ' : ''}${game.definition.definition}` : '';
		startCountdown();
	}
	if (!me) {
		box.innerHTML = '<h2>Statistics</h2><p class="dim" style="text-align:center">Sign in to track your stats and streaks and get on the leaderboard.</p><button class="btn" id="stats-signin">Sign in</button>';
		$('stats-signin').onclick = () => openModal('account');
		return;
	}
	const s = await api('/stats');
	if (s.error) { box.textContent = s.error; return; }
	const max  = Math.max(1, ...s.distribution);
	const cur  = game && game.solved ? game.guesses.length : 0;
	box.innerHTML = `<h2>Statistics</h2>
		<div class="stat-row">
			<div><b>${s.played}</b><span>Played</span></div>
			<div><b>${s.win_pct}</b><span>Win %</span></div>
			<div><b>${s.streak}</b><span>Current Streak</span></div>
			<div><b>${s.max_streak}</b><span>Max Streak</span></div>
		</div>
		<h3>Guess Distribution</h3>
		<div class="dist">${s.distribution.map((n, i) => `<div class="dist-row"><span>${i + 1}</span><div class="dist-bar${cur === i + 1 ? ' current' : ''}" style="width:${Math.max(7, (n / max) * 100)}%">${n}</div></div>`).join('')}</div>`;
}

function startCountdown() {
	clearInterval(countdown);
	const end  = Date.now() + game.next_in * 1000;
	const tick = () => {
		const left = Math.max(0, Math.round((end - Date.now()) / 1000));
		$('countdown').textContent = [left / 3600, (left % 3600) / 60, left % 60].map((n) => String(Math.floor(n)).padStart(2, '0')).join(':');
		if (left === 0) { clearInterval(countdown); load(); }
	};
	tick();
	countdown = setInterval(tick, 1000);
}

function shareText() {
	const high  = document.documentElement.dataset.contrast === 'high';
	const light = document.documentElement.dataset.theme === 'light';
	const emoji = { correct: high ? '🟧' : '🟩', present: high ? '🟦' : '🟨', absent: light ? '⬜' : '⬛' };
	const score = game.solved ? game.guesses.length : 'X';
	const grid  = game.guesses.map((g) => g.result.map((r) => emoji[r]).join('')).join('\n');
	return `wordle #${game.puzzle} ${score}/${ROWS}${game.hard ? '*' : ''}\n\n${grid}\n${location.origin}`;
}

async function copyText(text) {
	try {
		await navigator.clipboard.writeText(text);
	} catch (e) { // http pages have no clipboard API
		const ta = Object.assign(document.createElement('textarea'), { value: text });
		ta.style.position = 'fixed';
		ta.style.opacity  = 0;
		document.body.appendChild(ta);
		ta.select();
		document.execCommand('copy');
		ta.remove();
	}
}

$('share-btn').addEventListener('click', async () => {
	const text = shareText();
	if (navigator.share && matchMedia('(pointer: coarse)').matches) {
		try { await navigator.share({ text }); return; } catch (e) { if (e.name === 'AbortError') return; }
	}
	await copyText(text);
	toast('Copied results to clipboard');
});

/* Leaderboard */

async function loadBoard(board) {
	document.querySelectorAll('#lb-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.board === board));
	const body = $('lb-body');
	body.innerHTML = '<p class="dim">Loading...</p>';
	const data = await api(`/leaderboard?board=${board}`);
	if (data.error) { body.textContent = data.error; return; }
	if (!data.players.length) { body.innerHTML = `<p class="dim">${board === 'today' ? 'Nobody has solved today\'s word yet.' : board === 'alltime' ? 'Nobody has played 5 games yet.' : 'No active streaks yet.'}</p>`; return; }
	const cols = {
		today   : [['Guesses', (p) => `${p.tries}${p.hard ? '*' : ''}`]],
		streak  : [['Streak', (p) => p.streak], ['Best', (p) => p.max_streak]],
		alltime : [['Played', (p) => p.played], ['Win %', (p) => p.win_pct], ['Avg', (p) => p.avg ?? '-']]
	}[board];
	const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
	body.innerHTML = `<table><tr><th>#</th><th>Player</th>${cols.map((c) => `<th class="num">${c[0]}</th>`).join('')}</tr>${data.players.map((p, i) => `<tr${me && me.username === p.username ? ' class="me"' : ''}><td>${i + 1}</td><td>${esc(p.username)}</td>${cols.map((c) => `<td class="num">${c[1](p)}</td>`).join('')}</tr>`).join('')}</table>${board === 'today' ? '<p class="dim small">* hard mode</p>' : ''}`;
}

document.querySelectorAll('#lb-tabs button').forEach((b) => b.addEventListener('click', () => loadBoard(b.dataset.board)));

/* Settings */

function syncHard() {
	const locked = !!(game && game.guesses.length);
	$('set-hard').checked  = locked ? game.hard : store('wordle_hard') === 'on';
	$('set-hard').disabled = locked;
	$('set-hard').closest('.setting').classList.toggle('disabled', locked);
	$('hard-note').textContent = locked ? 'Can only be changed before your first guess' : 'Revealed hints must be used in later guesses';
}

function applyTheme() {
	const dark = document.documentElement.dataset.theme !== 'light';
	$('set-dark').checked     = dark;
	$('set-contrast').checked = document.documentElement.dataset.contrast === 'high';
	document.querySelector('meta[name="theme-color"]').content = dark ? '#08080f' : '#ffffff';
}

$('set-hard').addEventListener('change', (e) => store('wordle_hard', e.target.checked ? 'on' : 'off'));
$('set-dark').addEventListener('change', (e) => {
	document.documentElement.dataset.theme = e.target.checked ? 'dark' : 'light';
	store('wordle_theme', document.documentElement.dataset.theme);
	applyTheme();
});
$('set-contrast').addEventListener('change', (e) => {
	if (e.target.checked) document.documentElement.dataset.contrast = 'high'; else delete document.documentElement.dataset.contrast;
	store('wordle_contrast', e.target.checked ? 'on' : 'off');
});

/* Account */

function renderAccount() {
	$('account-out').classList.toggle('hidden', !!me);
	$('account-in').classList.toggle('hidden', !me);
	$('account-btn').classList.toggle('signed-in', !!me);
	if (me) {
		$('account-name').textContent = me.username;
		$('apikey-btn').textContent   = me.has_api_key ? 'Replace API key' : 'Create API key';
		$('apikey-box').classList.add('hidden');
	} else {
		setAuthMode(authMode);
	}
}

async function setAuthMode(mode) {
	authMode = mode;
	document.querySelectorAll('#auth-tabs button').forEach((b) => b.classList.toggle('active', b.dataset.auth === mode));
	$('auth-submit').textContent = mode === 'login' ? 'Log in' : 'Sign up';
	$('auth-pass').autocomplete  = mode === 'login' ? 'current-password' : 'new-password';
	$('captcha-box').classList.toggle('hidden', mode === 'login');
	$('auth-error').textContent  = '';
	if (mode === 'signup') loadCaptcha();
}

async function loadCaptcha() {
	const c = await api('/auth/captcha');
	captchaId = c.captcha_id;
	$('captcha-img').innerHTML = c.svg;
	$('auth-captcha').value    = '';
}

document.querySelectorAll('#auth-tabs button').forEach((b) => b.addEventListener('click', () => setAuthMode(b.dataset.auth)));
$('captcha-img').addEventListener('click', loadCaptcha);

$('auth-form').addEventListener('submit', async (e) => {
	e.preventDefault();
	const body = { username: $('auth-user').value.trim(), password: $('auth-pass').value };
	if (authMode === 'signup') Object.assign(body, { captcha_id: captchaId, captcha: $('auth-captcha').value });
	const data = await api(`/auth/${authMode}`, body);
	if (data.error) {
		$('auth-error').textContent = data.error;
		if (authMode === 'signup') loadCaptcha();
		return;
	}
	authToken = data.token;
	store('wordle_auth', authToken);
	$('auth-pass').value = '';
	await loadMe();
	closeModal();
	toast(`Signed in as ${me.username}`);
	load();
});

$('logout-btn').addEventListener('click', async () => {
	await api('/auth/logout', {});
	authToken = '';
	me        = null;
	store('wordle_auth', null);
	renderAccount();
	closeModal();
	load();
});

$('apikey-btn').addEventListener('click', async () => {
	const data = await api('/auth/apikey', {});
	if (data.error) return toast(data.error);
	me.has_api_key = true;
	$('apikey').textContent     = data.api_key;
	$('apikey-btn').textContent = 'Replace API key';
	$('api-example').textContent = `curl -H 'Authorization: Bearer ${data.api_key}' ${location.origin}/api/state\n\ncurl -H 'Authorization: Bearer ${data.api_key}' \\\n     -H 'Content-Type: application/json' \\\n     -d '{"guess": "crane"}' ${location.origin}/api/guess`;
	$('apikey-box').classList.remove('hidden');
});

$('apikey').addEventListener('click', async () => { await copyText($('apikey').textContent); toast('Copied API key'); });

async function loadMe() {
	if (!authToken) { me = null; renderAccount(); return; }
	const data = await api('/auth/me');
	me = data.user;
	if (!me) { authToken = ''; store('wordle_auth', null); }
	renderAccount();
}

/* Startup */

async function load() {
	const data = await api('/state');
	if (data.error) return toast(data.error, 3000);
	game    = data;
	current = '';
	render();
	if (game.over) finish(false);
	else if (!game.guesses.length && !store('wordle_seen_help')) { store('wordle_seen_help', '1'); openModal('help'); }
}

buildKeyboard();
buildBoard();
applyTheme();
loadMe().then(load);
