/* Replace the simulator's generic SVG token with the team mascot. */
(() => {
  const surface = $('#canvas');
  const SVG = 'http://www.w3.org/2000/svg';
  function decorate() {
    surface.querySelectorAll('.bts-token:not([data-mascot])').forEach(token => {
      token.dataset.mascot = 'true';
      const image = document.createElementNS(SVG, 'image');
      image.setAttribute('href', '/static/assets/mascot-cat.png');
      image.setAttribute('x', '-8'); image.setAttribute('y', '-19');
      image.setAttribute('width', '36'); image.setAttribute('height', '49');
      image.setAttribute('preserveAspectRatio', 'xMidYMid meet');
      image.classList.add('bts-mascot');
      token.append(image);
    });
  }
  const observer = new MutationObserver(decorate);
  modeler.get('eventBus').on('tokenSimulation.toggleMode', event => {
    if (event.active) { observer.observe(surface, { childList: true, subtree: true }); decorate(); }
    else observer.disconnect();
  });
})();
