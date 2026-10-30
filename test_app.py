import base64
import http.client
import json
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from app import ConsoleServer


def fake_token(expires=None):
    payload = json.dumps({'exp': expires or time.time() + 300}).encode()
    return 'test.' + base64.urlsafe_b64encode(payload).decode().rstrip('=') + '.test'


class ConsoleTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.token = fake_token()
        self.response_status = 201
        self.response_body = {'inicio': '0000000000000001', 'fim': '0000000000001000', 'quantidade': 1000}
        self.server = ConsoleServer(('127.0.0.1', 0), '', self.upstream)
        self.port = self.server.server_port
        self.server.origin = f'http://localhost:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = None
        status, headers, _ = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.cookie = headers['Set-Cookie'].split(';')[0]
        _, _, session = self.request('GET', '/api/status')
        self.csrf = session['csrf']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def upstream(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.startswith('/auth/token?'):
            return 200, {'access_token': self.token, 'token_type': 'Bearer'}
        return self.response_status, self.response_body

    def request(self, method, path, body=None, extra_headers=None, cookie=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Host': f'localhost:{self.port}'}
        if cookie and self.cookie:
            headers['Cookie'] = self.cookie
        if method == 'POST':
            headers.update({'Origin': self.server.origin, 'X-CSRF-Token': self.csrf, 'Content-Type': 'application/json'})
        headers.update(extra_headers or {})
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        response = connection.getresponse()
        payload = response.read().decode()
        result = response.status, dict(response.getheaders()), json.loads(payload) if payload and response.getheader('Content-Type').startswith('application/json') else payload
        connection.close()
        return result

    def start_login(self):
        status, _, payload = self.request('POST', '/api/login', {})
        self.assertEqual(status, 200)
        callback = parse_qs(urlsplit(payload['url']).query)['client_url'][0]
        self.assertTrue(callback.startswith(self.server.origin + '/callback/'))
        return urlsplit(callback).path + '?session_id=fake-voucher'

    def authenticate(self):
        callback = self.start_login()
        status, headers, _ = self.request('GET', callback)
        self.assertEqual(status, 303)
        self.assertEqual(headers['Location'], '/login-complete')
        return callback

    def body(self, tipo='RCE'):
        return dict(tipo=tipo, conselho='CRM', uf='GO', documento='12345', cnpj='11.222.333/0001-81', quantidade=10)

    def test_login_exchanges_voucher_once_and_hides_token(self):
        callback = self.authenticate()
        status, _, payload = self.request('GET', '/api/status')
        self.assertEqual(status, 200)
        self.assertTrue(payload['authenticated'])
        self.assertNotIn(self.token, json.dumps(payload))
        self.assertEqual(self.calls, [('/auth/token?session_id=fake-voucher', {'origin': self.server.origin})])
        self.assertEqual(self.request('GET', callback)[0], 400)
        self.assertEqual(len(self.calls), 1)

    def test_callback_is_bound_to_browser_session_and_latest_attempt(self):
        old_callback = self.start_login()
        new_callback = self.start_login()
        self.assertEqual(self.request('GET', old_callback)[0], 400)
        self.assertEqual(self.request('GET', new_callback, cookie=False)[0], 401)
        self.assertEqual(self.request('GET', new_callback)[0], 303)
        self.assertEqual(len(self.calls), 1)

    def test_host_origin_and_csrf_are_required(self):
        self.assertEqual(self.request('GET', '/', extra_headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/login', {}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/login', {}, {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.calls, [])

    def test_request_routes_and_payloads_match_contract(self):
        self.authenticate()
        for tipo in ('NRA', 'NRB', 'NRB2', 'NRR', 'RCE', 'RET'):
            with self.subTest(tipo=tipo):
                status, _, result = self.request('POST', '/api/numeracoes', self.body(tipo))
                self.assertEqual(status, 200)
                self.assertEqual(result['httpStatus'], 201)
                path, args = self.calls[-1]
                self.assertEqual(args['token'], self.token)
                self.assertNotIn(self.token, json.dumps(result))
                body = args['body']
                if tipo in ('RCE', 'RET'):
                    self.assertEqual(path, '/numeracoes/receita-especial-retencao')
                    self.assertEqual(body, dict(tipo=tipo, conselho='CRM', uf='GO', documento='12345', cnpj='11222333000181'))
                else:
                    self.assertEqual(path, '/numeracoes/notificacao-receita')
                    self.assertEqual(body, dict(receita=tipo, conselho='CRM', uf='GO', documento='12345', quantidade=10))

    def test_expiry_and_logout_prevent_number_requests(self):
        self.assertEqual(self.request('POST', '/api/numeracoes', self.body())[0], 401)
        self.authenticate()
        next(iter(self.server.sessions.values()))['expires'] = time.time() - 1
        self.assertEqual(self.request('POST', '/api/numeracoes', self.body())[0], 401)
        self.authenticate()
        self.assertEqual(self.request('POST', '/api/logout', {})[0], 200)
        self.assertEqual(self.request('POST', '/api/numeracoes', self.body())[0], 401)
        self.assertTrue(all(path.startswith('/auth/token?') for path, _ in self.calls))

    def test_invalid_body_never_reaches_anvisa(self):
        self.authenticate()
        invalid = [dict(self.body('NRA'), quantidade=n) for n in (9, 51, True, '10')]
        invalid += [dict(self.body(), cnpj='123'), dict(self.body(), tipo='NRT'), [], dict(self.body(), tipo=[])]
        for body in invalid:
            self.assertEqual(self.request('POST', '/api/numeracoes', body)[0], 400)
        self.assertEqual(len(self.calls), 1)

    def test_upstream_unauthorized_clears_token(self):
        self.authenticate()
        self.response_status = 401
        status, _, result = self.request('POST', '/api/numeracoes', self.body())
        self.assertEqual(status, 200)
        self.assertEqual(result['httpStatus'], 401)
        self.assertFalse(self.request('GET', '/api/status')[2]['authenticated'])

    def test_concurrent_submission_is_rejected_and_timeout_is_not_retried(self):
        self.authenticate()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def failing_upstream(*args, **kwargs):
            calls.append(args)
            entered.set()
            release.wait(2)
            raise TimeoutError()

        self.server.upstream = failing_upstream
        results = []
        request = threading.Thread(target=lambda: results.append(self.request('POST', '/api/numeracoes', self.body())))
        request.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.request('POST', '/api/numeracoes', self.body())[0], 409)
        finally:
            release.set()
            request.join()
        self.assertEqual(results[0][0], 502)
        self.assertIn('pode ter sido processada', results[0][2]['error'])
        self.assertEqual(len(calls), 1)

    def test_failed_or_expired_token_does_not_authenticate(self):
        self.token = fake_token(time.time() - 1)
        self.authenticate()
        self.assertFalse(self.request('GET', '/api/status')[2]['authenticated'])


if __name__ == '__main__':
    unittest.main()
