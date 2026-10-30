import base64
import http.cookies
import json
import os
from pathlib import Path
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ANVISA_URL = 'https://api-gateway.hmg.apps.anvisa.gov.br/api-sncr/api/v1'
STATIC = Path(__file__).parent / 'static'
NOTIFICACOES = {'NRA', 'NRB', 'NRB2', 'NRR'}
UF = set('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_anvisa(path, *, token=None, origin=None, body=None):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if origin:
        headers['Origin'] = origin
    if body is not None:
        headers['Content-Type'] = 'application/json'
    request = urllib.request.Request(
        ANVISA_URL + path, headers=headers,
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        response = urllib.request.build_opener(NoRedirect).open(request, timeout=25)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(1_000_000).decode('utf-8', errors='replace')
        if token:
            raw = raw.replace(token, '[token omitido]')
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = {'mensagem': 'A Anvisa retornou conteúdo fora do formato JSON.'}
        return response.status, payload


def build_request(data):
    if not isinstance(data, dict):
        raise ValueError('Requisição inválida.')
    tipo = data.get('tipo')
    if not isinstance(tipo, str) or tipo not in NOTIFICACOES | {'RCE', 'RET'}:
        raise ValueError('Selecione um tipo de receita válido.')
    body = {key: str(data.get(key, '')).strip() for key in ('conselho', 'uf', 'documento')}
    if body['conselho'] not in {'CRM', 'CRO', 'CRMV'} or body['uf'] not in UF:
        raise ValueError('Conselho ou UF inválidos.')
    if not body['documento'].isascii() or not body['documento'].isdigit() or len(body['documento']) > 20:
        raise ValueError('Informe o número do conselho, somente com dígitos.')
    if tipo in NOTIFICACOES:
        quantity = data.get('quantidade')
        if type(quantity) is not int or not 10 <= quantity <= 50:
            raise ValueError('A quantidade deve estar entre 10 e 50.')
        return '/numeracoes/notificacao-receita', dict(body, receita=tipo, quantidade=quantity)
    cnpj = str(data.get('cnpj', '')).translate(str.maketrans('', '', './- '))
    if len(cnpj) != 14 or not cnpj.isascii() or not cnpj.isdigit():
        raise ValueError('Informe um CNPJ com 14 dígitos.')
    return '/numeracoes/receita-especial-retencao', dict(body, tipo=tipo, cnpj=cnpj)


class ConsoleServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, origin, upstream=call_anvisa):
        super().__init__(address, Handler)
        self.origin = origin
        self.upstream = upstream
        self.sessions = {}
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, status, body='', content_type='application/json; charset=utf-8', **headers):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def get_session(self):
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
        except http.cookies.CookieError:
            return None
        item = cookie.get('sncr_session')
        with self.server.lock:
            expired = [key for key, value in self.server.sessions.items() if value['created'] < time.time() - 3600]
            for key in expired:
                del self.server.sessions[key]
            session = self.server.sessions.get(item.value) if item else None
            if session and session['expires'] <= time.time():
                session['token'] = None
            if session and session['attempt'] and session['attempt']['expires'] <= time.time():
                session.update(attempt=None, message='Tempo de login esgotado. Tente novamente.')
            return session

    def valid_host(self):
        if self.headers.get('Host') != urllib.parse.urlsplit(self.server.origin).netloc:
            self.respond(403, {'error': 'Abra a aplicação pelo endereço localhost configurado.'})
            return False
        return True

    def do_GET(self):
        if not self.valid_host():
            return
        url = urllib.parse.urlsplit(self.path)
        if url.path == '/health':
            return self.respond(200, {'status': 'ok'})
        session = self.get_session()
        if url.path == '/':
            headers = {}
            if session is None:
                with self.server.lock:
                    if len(self.server.sessions) >= 128:
                        return self.respond(503, {'error': 'Limite de sessões locais atingido.'})
                    key = secrets.token_urlsafe(32)
                    self.server.sessions[key] = dict(created=time.time(), csrf=secrets.token_urlsafe(32), token=None,
                                                     expires=0, attempt=None, busy=False, message='Entre com sua conta gov.br.')
                headers['Set-Cookie'] = f'sncr_session={key}; HttpOnly; SameSite=Lax; Path=/; Max-Age=3600'
            return self.respond(200, (STATIC / 'index.html').read_bytes(), 'text/html; charset=utf-8', **headers)
        if url.path in {'/app.js', '/style.css', '/login-complete.js'}:
            kind = 'text/javascript' if url.path.endswith('.js') else 'text/css'
            return self.respond(200, (STATIC / url.path[1:]).read_bytes(), kind + '; charset=utf-8')
        if session is None:
            return self.respond(401, {'error': 'Abra a página inicial para iniciar uma sessão.'})
        if url.path == '/login-complete':
            return self.respond(200, (STATIC / 'login-complete.html').read_bytes(), 'text/html; charset=utf-8')
        if url.path == '/api/status':
            with self.server.lock:
                return self.respond(200, dict(authenticated=bool(session['token']), expiresAt=session['expires'],
                                              csrf=session['csrf'], busy=session['busy'], pending=bool(session['attempt']) or session['busy'],
                                              message=session['message']))
        if url.path.startswith('/callback/'):
            return self.exchange_token(url, session)
        self.respond(404, {'error': 'Página não encontrada.'})

    def exchange_token(self, url, session):
        with self.server.lock:
            attempt = session['attempt']
            if not attempt or url.path != '/callback/' + attempt['id'] or attempt['expires'] < time.time():
                return self.respond(400, {'error': 'Retorno de login inválido ou expirado. Inicie outro login.'})
            session['attempt'] = None
            session['busy'] = True
        try:
            session_id = urllib.parse.parse_qs(url.query).get('session_id', [''])[0]
            if not session_id or len(session_id) > 4096:
                raise ValueError()
            status, payload = self.server.upstream('/auth/token?' + urllib.parse.urlencode({'session_id': session_id}), origin=self.server.origin)
            if status != 200:
                raise ValueError()
            token = payload['access_token']
            part = token.split('.')[1]
            claims = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
            expires = claims['exp']
            if type(expires) not in (int, float) or not time.time() < expires < time.time() + 86400:
                raise ValueError()
            with self.server.lock:
                session.update(token=token, expires=expires, message='Autenticação concluída na Anvisa.')
        except Exception:
            with self.server.lock:
                session.update(token=None, expires=0, message='Não foi possível concluir o login. Tente novamente.')
        finally:
            with self.server.lock:
                session['busy'] = False
        self.respond(303, **{'Location': '/login-complete'})

    def do_POST(self):
        if not self.valid_host():
            return
        session = self.get_session()
        if session is None:
            return self.respond(401, {'error': 'Sessão local expirada. Recarregue a página.'})
        if self.headers.get('Origin') != self.server.origin or not secrets.compare_digest(
            self.headers.get('X-CSRF-Token', ''), session['csrf']
        ):
            return self.respond(403, {'error': 'Origem ou sessão inválida.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192:
                raise ValueError()
            data = json.loads(self.rfile.read(size))
        except (ValueError, OverflowError):
            return self.respond(400, {'error': 'Requisição inválida.'})
        with self.server.lock:
            if session['busy']:
                return self.respond(409, {'error': 'Aguarde a requisição em andamento.'})
            if self.path == '/api/login':
                attempt = dict(id=secrets.token_urlsafe(32), expires=time.time() + 300)
                session.update(attempt=attempt, token=None, expires=0, message='Conclua o login no pop-up da Anvisa.')
                callback = self.server.origin + '/callback/' + attempt['id']
                return self.respond(200, {'url': ANVISA_URL + '/auth/login?' + urllib.parse.urlencode({'client_url': callback})})
            if self.path == '/api/logout':
                session.update(token=None, expires=0, attempt=None, message='Credencial local removida. A sessão gov.br do navegador permanece ativa.')
                return self.respond(200, {'ok': True})
            if self.path != '/api/numeracoes':
                return self.respond(404, {'error': 'Rota não encontrada.'})
            if not session['token']:
                return self.respond(401, {'error': 'Entre novamente na Anvisa para solicitar numerações.'})
            session['busy'] = True
            token = session['token']
        try:
            path, body = build_request(data)
            started = time.monotonic()
            status, response = self.server.upstream(path, token=token, body=body)
            if status == 401:
                with self.server.lock:
                    session.update(token=None, expires=0, message='A Anvisa recusou a credencial. Entre novamente.')
            self.respond(200, dict(httpStatus=status, elapsedMs=round((time.monotonic() - started) * 1000),
                                   request={'method': 'POST', 'url': ANVISA_URL + path, 'body': body}, response=response))
        except ValueError as error:
            self.respond(400, {'error': str(error)})
        except Exception:
            self.respond(502, {'error': 'Sem resposta da Anvisa. A operação pode ter sido processada; não repetimos a solicitação automaticamente.'})
        finally:
            with self.server.lock:
                session['busy'] = False


if __name__ == '__main__':
    origin = os.environ.get('SNCR_ORIGIN', 'http://localhost:18767')
    parsed = urllib.parse.urlsplit(origin)
    if parsed.scheme != 'http' or parsed.hostname != 'localhost' or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise SystemExit('SNCR_ORIGIN deve ser http://localhost:PORTA.')
    server = ConsoleServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), origin)
    print('Console SNCR de homologação: ' + origin, flush=True)
    server.serve_forever()
