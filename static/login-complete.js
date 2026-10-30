document.getElementById('close').addEventListener('click', () => window.close());
fetch('/api/status').then((response) => response.json()).then((status) => {
  document.getElementById('message').textContent = status.message || status.error;
  if (status.authenticated) window.close();
}).catch(() => {
  document.getElementById('message').textContent = 'Consulte o resultado na tela principal.';
});
