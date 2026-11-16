/* wordle - Developed by acidvegas in Python (https://github.com/acidvegas) */
/* theme.js */

try { const t = localStorage.getItem('wordle_theme'); if (t) document.documentElement.dataset.theme = t; if (localStorage.getItem('wordle_contrast') === 'on') document.documentElement.dataset.contrast = 'high'; } catch (e) {}
