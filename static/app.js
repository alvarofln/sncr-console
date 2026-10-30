const $ = (id) => document.getElementById(id);
const form = $('request-form');
let session = { authenticated: false, busy: false };
let submitting = false;
let lastResult = null;
let loginTimer;

for (const uf of 'AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split(' ')) {
  $('uf').add(new Option(uf, uf, false, uf === 'GO'));
}

function showError(message = '') {
  $('error').textContent = message;
  $('error').hidden = !message;
}

function renderSession() {
  const seconds = Math.max(0, Math.floor((session.expiresAt || 0) - Date.now() / 1000));
  const authenticated = session.authenticated && seconds > 0;
  $('session-status').textContent = session.authenticated && !seconds ? 'Credencial expirada. Entre novamente.' : session.message;
  $('expires').textContent = authenticated ? `Credencial válida por ${Math.floor(seconds / 60)}min ${seconds % 60}s.` : '';
  $('login').disabled = !session.csrf || submitting || session.busy;
  $('login').textContent = session.pending ? 'Reabrir login' : authenticated ? 'Entrar novamente' : 'Entrar com gov.br';
  $('logout').hidden = !authenticated;
  $('logout').disabled = submitting || session.busy;
  $('submit').disabled = !authenticated || submitting || session.busy;
  $('submit').textContent = submitting ? 'Aguardando Anvisa…' : 'Solicitar na Anvisa';
}

async function refreshSession() {
  const response = await fetch('/api/status');
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Não foi possível consultar a sessão.');
  session = payload;
  renderSession();
}

async function post(path, body = {}) {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || 'Não foi possível concluir a requisição.');
  return payload;
}

function selectTipo() {
  const special = ['RCE', 'RET'].includes(form.elements.tipo.value);
  $('quantity-field').hidden = special;
  form.elements.quantidade.disabled = special;
  $('cnpj-field').hidden = !special;
  form.elements.cnpj.disabled = !special;
  form.elements.cnpj.required = special;
  $('request-note').textContent = special
    ? 'Esta ação solicita um bloco de 1.000 números em homologação. Informe o CNPJ a ser usado no teste.'
    : 'Esta ação solicita de 10 a 50 números em homologação e depende de saldo disponível para o prescritor.';
}

function generateCnpj() {
  const digits = Array.from(crypto.getRandomValues(new Uint8Array(8)), (value) => value % 10);
  digits.push(0, 0, 0, 1);
  for (let i = 0; i < 2; i++) {
    const sum = digits.reduce((total, digit, index) => total + digit * ((digits.length - index - 1) % 8 + 2), 0);
    const remainder = sum % 11;
    digits.push(remainder < 2 ? 0 : 11 - remainder);
  }
  return digits.join('').replace(/^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$/, '$1.$2.$3/$4-$5');
}

$('generate-cnpj').addEventListener('click', () => {
  form.elements.cnpj.value = generateCnpj();
});
$('tipo').addEventListener('change', selectTipo);
$('login').addEventListener('click', async () => {
  showError();
  const popup = window.open('about:blank', 'sncr-login', 'popup,width=540,height=760');
  if (!popup) return showError('Permita pop-ups para localhost e tente novamente.');
  popup.opener = null;
  $('login').disabled = true;
  clearTimeout(loginTimer);
  try {
    const result = await post('/api/login');
    popup.location.replace(result.url);
    await pollLogin();
  } catch (error) { popup.close(); showError(error.message); renderSession(); }
});

async function pollLogin() {
  try {
    await refreshSession();
    if (session.pending) loginTimer = setTimeout(pollLogin, 1000);
  } catch (error) { showError(error.message); }
}
$('logout').addEventListener('click', async () => {
  showError();
  try { await post('/api/logout'); await refreshSession(); }
  catch (error) { showError(error.message); }
});
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (submitting) return;
  submitting = true;
  showError();
  renderSession();
  $('http-status').textContent = 'Aguardando';
  $('result-note').textContent = 'Solicitação enviada. Aguarde a resposta antes de tentar novamente.';
  $('result').textContent = 'Aguardando Anvisa…';
  $('request').textContent = '—';
  $('download').hidden = true;
  lastResult = null;
  const body = Object.fromEntries(new FormData(form));
  if ('quantidade' in body) body.quantidade = Number(body.quantidade);
  try {
    lastResult = await post('/api/numeracoes', body);
    $('http-status').textContent = `HTTP ${lastResult.httpStatus}`;
    $('result-note').textContent = `Resposta recebida em ${lastResult.elapsedMs} ms.`;
    $('result').textContent = JSON.stringify(lastResult.response, null, 2);
    $('request').textContent = JSON.stringify(lastResult.request, null, 2);
    $('download').hidden = false;
  } catch (error) {
    showError(error.message);
    $('http-status').textContent = 'Sem resultado';
    $('result-note').textContent = '';
    $('result').textContent = error.message;
  } finally {
    submitting = false;
    try { await refreshSession(); } catch (error) { showError(error.message); }
    renderSession();
  }
});
$('download').addEventListener('click', () => {
  if (!lastResult) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(lastResult, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'sncr-homologacao-resultado.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
selectTipo();
refreshSession().then(() => {
  if (session.pending) loginTimer = setTimeout(pollLogin, 1000);
}).catch((error) => showError(error.message));
setInterval(renderSession, 1000);
window.addEventListener('focus', () => refreshSession().catch((error) => showError(error.message)));
