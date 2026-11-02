// Aplica o tema (claro/escuro) antes de desenhar a página, para não piscar.
(function () {
  var t = null;
  try { t = localStorage.getItem('tema'); } catch (e) {}
  if (t !== 'claro' && t !== 'escuro') {
    t = (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches) ? 'escuro' : 'claro';
  }
  document.documentElement.setAttribute('data-tema', t);
})();
