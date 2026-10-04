/* Short welcome, shown once per tab. Never hold the editor behind an animation. */
(() => {
  const intro = document.querySelector('#intro-screen');
  const key = 'bpmn-agent-intro-seen-v1';
  let seen = false;
  try {
    seen = sessionStorage.getItem(key) === '1';
    sessionStorage.setItem(key, '1');
  } catch { /* Some private contexts disable storage; the welcome still works. */ }
  if (seen || matchMedia('(prefers-reduced-motion: reduce)').matches) {
    intro.remove();
    return;
  }

  document.body.classList.add('intro-active');
  let finished = false;
  let timer;
  const onEscape = event => { if (event.key === 'Escape') finish(); };
  function finish() {
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    document.removeEventListener('keydown', onEscape);
    intro.classList.add('intro-leaving');
    intro.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('intro-active');
    setTimeout(() => intro.remove(), 380);
  }
  document.querySelector('#intro-skip').addEventListener('click', finish);
  document.addEventListener('keydown', onEscape);
  timer = setTimeout(finish, 1400);
})();
