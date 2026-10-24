// Runs before first paint so the saved theme does not flash.
try {
  var t = localStorage.getItem('quill.theme')
  if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t)
} catch (e) { /* storage blocked: follow the system theme */ }
