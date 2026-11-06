from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import hashlib
import json
import sqlite3
from time import time
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image

from app import accounts, main, storage
from app.temporary_jobs import temporary_jobs
import test_workflow as fixtures


PASSWORD = 'test-only-password-for-technician'


def signature(color):
    out = BytesIO(); Image.new('RGBA', (120, 40), color).save(out, format='PNG')
    return out.getvalue()


class Accounts(unittest.TestCase):
    def setUp(self):
        self.local = patch.dict('os.environ', {'CERTICOEX_AUTH_REQUIRED': 'false', 'CERTICOEX_PASSWORD': '', 'RENDER': ''})
        self.local.start(); self.addCleanup(self.local.stop)
        fixtures.Workflow.setUp(self)
        self.env = patch.dict('os.environ', {'CERTICOEX_AUTH_REQUIRED': 'true', 'CERTICOEX_MULTIUSER': 'true', 'CERTICOEX_PASSWORD': PASSWORD, 'CERTICOEX_USERNAME': 'tecnico', 'RENDER': ''})
        self.env.start(); self.addCleanup(self.env.stop)
        with accounts._attempt_lock: accounts._attempts.clear()
        self.extra = []
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'tecnico', 'password': PASSWORD}).status_code, 200)

    def tearDown(self):
        for client in self.extra: client.close()
        for key in list(temporary_jobs.jobs): temporary_jobs.delete(key)
        fixtures.Workflow.tearDown(self)

    batch = fixtures.Workflow.batch

    def technician(self, name):
        created = self.client.post('/api/users', json={'username': name, 'password': PASSWORD})
        self.assertEqual(created.status_code, 200, created.text)
        client = TestClient(main.app); self.extra.append(client)
        login = client.post('/api/auth/login', json={'username': name, 'password': PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        return client, created.json()

    def emit(self, client):
        with patch('app.main.convert_pdf', side_effect=RuntimeError('Sin PDF para esta prueba')):
            result = client.post('/api/generate', json=self.batch())
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_login_cookies_logout_and_legacy_basic_cannot_bypass_accounts(self):
        fresh = TestClient(main.app, base_url='https://testserver'); self.extra.append(fresh)
        self.assertEqual(fresh.get('/', follow_redirects=False).headers['location'], '/login')
        self.assertEqual(fresh.get('/login').status_code, 200)
        self.assertEqual(fresh.get('/api/profile', auth=('tecnico', PASSWORD)).status_code, 401)
        result = fresh.post('/api/auth/login', json={'username': 'tecnico', 'password': PASSWORD})
        cookie = result.headers['set-cookie'].lower()
        for flag in ('httponly', 'secure', 'samesite=lax'): self.assertIn(flag, cookie)
        self.assertEqual(fresh.get('/api/auth/me').json()['role'], 'admin')
        token = fresh.cookies.get(accounts.COOKIE)
        stored = main.open_store(self.data).get('session', hashlib.sha256(token.encode()).hexdigest())
        self.assertIsNotNone(stored)
        self.assertNotIn(token, json.dumps(stored))
        self.assertEqual(fresh.post('/api/auth/logout').status_code, 200)
        self.assertEqual(fresh.get('/api/profile').status_code, 401)

    def test_logo_and_bookmark_icon_available_on_login_and_private_app(self):
        fresh = TestClient(main.app); self.extra.append(fresh)
        login = fresh.get('/login')
        self.assertIn('rel="icon"', login.text)
        self.assertIn('class="brand-logo"', login.text)
        icon = fresh.get('/logo.png')
        self.assertEqual(icon.status_code, 200)
        self.assertTrue(icon.content.startswith(b'\x89PNG'))
        self.assertEqual(icon.content, fresh.get('/favicon.ico').content)
        self.assertIn('public', icon.headers['cache-control'])
        self.assertIn('rel="icon"', self.client.get('/').text)

    def test_profiles_signatures_and_generated_certificates_are_isolated(self):
        a, _ = self.technician('tecnico-a'); b, _ = self.technician('tecnico-b')
        self.assertEqual(a.get('/api/profile').json(), {'name': '', 'title': '', 'salutation': '', 'has_signature': False})
        self.assertEqual(a.get('/api/profile/signature').status_code, 404)
        self.assertEqual(self.client.get('/api/profile').json()['name'], 'Técnico de prueba')
        signatures = [signature('red'), signature('blue')]
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda item: item[0].post('/api/profile', data={'name': item[1]}, files={'signature': ('firma.png', item[2], 'image/png')}), [(a, 'Técnico Alfa', signatures[0]), (b, 'Técnico Beta', signatures[1])]))
        self.assertTrue(all(r.status_code == 200 for r in results))
        self.assertNotEqual(a.get('/api/profile/signature').content, b.get('/api/profile/signature').content)
        self.assertEqual(a.get('/api/profile', params={'user_id': 'owner'}).json()['name'], 'Técnico Alfa')
        for client, own_name, other_name, own_signature in ((a, 'Técnico Alfa', 'Técnico Beta', signatures[0]), (b, 'Técnico Beta', 'Técnico Alfa', signatures[1])):
            result = self.emit(client)
            document = client.get(result['word']).content
            text = '\n'.join(p.text for p in Document(BytesIO(document)).paragraphs)
            self.assertIn(own_name, text); self.assertNotIn(other_name, text)
            with ZipFile(BytesIO(document)) as archive:
                self.assertIn(own_signature, [archive.read(path) for path in archive.namelist() if path.startswith('word/media/')])
            other = b if client is a else a
            self.assertEqual(other.get(result['word']).status_code, 404)
            key = result['word'].split('/')[-2]
            self.assertEqual(other.delete('/api/files/' + key).status_code, 200)
            self.assertEqual(client.get(result['word']).status_code, 200)
        self.assertEqual(self.client.get('/api/profile').json()['name'], 'Técnico de prueba')

    def test_shared_template_uses_each_accounts_title_and_salutation(self):
        doc = Document(main.template_path(self.template))
        cell = doc.add_table(rows=1, cols=1).cell(0, 0)
        p = cell.paragraphs[0]
        p.add_run('{CARGO_').bold = True
        p.add_run('TECNICO}')
        cell.add_paragraph('{TRATAMIENTO_TECNICO} {TECNICO}')
        doc.sections[0].footer.paragraphs[0].text = '{CARGO_TECNICO}'
        source = BytesIO(); doc.save(source)
        uploaded = self.client.post('/api/templates', data={'name': 'Plantilla compartida'},
                                    files={'file': ('plantilla.docx', source.getvalue())})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertIn('CARGO_TECNICO', uploaded.json()['fields'])
        payload = self.batch(); payload['template_id'] = uploaded.json()['id']
        a, _ = self.technician('tecnico-a'); b, _ = self.technician('tecnico-b')
        for client, name, title, salutation in ((a, 'Persona Alfa', 'TÉCNICO EN PRL', 'D.'),
                                                (b, 'Persona Beta', 'TÉCNICA SUPERIOR EN PRL', 'Dña.')):
            saved = client.post('/api/profile', data={'name': name, 'title': title, 'salutation': salutation},
                                files={'signature': ('firma.png', signature('blue'), 'image/png')})
            self.assertEqual(saved.status_code, 200, saved.text)
            # Older clients can still save a name without erasing new fields or the signature.
            self.assertEqual(client.post('/api/profile', data={'name': name}).status_code, 200)
            storage._open.cache_clear()
            current = client.get('/api/profile').json()
            self.assertEqual((current['title'], current['salutation']), (title, salutation))
            self.assertTrue(current['has_signature'])
            with patch('app.main.convert_pdf', side_effect=RuntimeError('Sin PDF para esta prueba')):
                generated = client.post('/api/generate', json=payload)
            self.assertEqual(generated.status_code, 200, generated.text)
            result = Document(BytesIO(client.get(generated.json()['word']).content))
            from app.documents import paragraph_text, paragraphs
            text = '\n'.join(paragraph_text(p) for p in paragraphs(result.element))
            self.assertIn(title, text); self.assertIn(f'{salutation} {name}', text)
            self.assertNotIn('{', text)
            self.assertEqual(result.sections[0].footer.paragraphs[0].text, title)
        self.assertEqual(a.get('/api/profile').json()['salutation'], 'D.')
        self.assertEqual(b.get('/api/profile').json()['salutation'], 'Dña.')
        self.assertEqual(self.client.get('/api/profile').json()['title'], '')

    def test_templates_companies_shared_and_management_restricted(self):
        a, actor = self.technician('tecnico-a'); b, _ = self.technician('tecnico-b')
        self.assertIn(self.template, [row['id'] for row in a.get('/api/templates').json()])
        template_url = f'/api/templates/{self.template}/file'
        self.assertEqual(a.get(template_url).content, b.get(template_url).content)
        self.assertEqual(a.get(template_url).status_code, 200)
        fresh = TestClient(main.app); self.extra.append(fresh)
        self.assertEqual(fresh.get(template_url).status_code, 401)
        with ThreadPoolExecutor(2) as pool:
            saved = list(pool.map(lambda client: client.post('/api/companies', json={'name': 'Empresa compartida', 'cif': 'B12345678'}), (a, b)))
        self.assertTrue(all(response.status_code == 200 for response in saved))
        self.assertEqual(saved[0].json()['id'], saved[1].json()['id'])
        self.assertEqual(len(b.get('/api/companies').json()), 1)
        self.assertEqual(b.get('/api/companies').json()[0]['cif'], 'B12345678')
        for method, path, payload in (('get', '/api/users', None), ('post', '/api/users', {'username': 'intruso', 'password': PASSWORD}), ('patch', '/api/users/' + actor['id'], {'active': True})):
            kwargs = {} if payload is None else {'json': payload}
            self.assertEqual(getattr(a, method)(path, **kwargs).status_code, 403)
        users = self.client.get('/api/users').json()
        self.assertEqual(len(users), 3)
        self.assertNotIn('password_hash', json.dumps(users))
        self.assertNotIn(PASSWORD, json.dumps(users))
        duplicate = self.client.post('/api/users', json={'username': 'TECNICO-A', 'password': PASSWORD})
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(a.post('/api/profile', data={'name': 'Perfil de una pestaña antigua'}, headers={'X-Certicoex-User': 'owner'}).status_code, 409)
        self.assertEqual(a.get('/api/profile').json()['name'], '')

    def test_deactivation_reset_and_self_password_change_revoke_sessions(self):
        a, actor = self.technician('tecnico-a')
        self.assertEqual(self.client.patch('/api/users/' + actor['id'], json={'active': False}).status_code, 200)
        self.assertEqual(a.get('/api/profile').status_code, 401)
        self.assertEqual(a.post('/api/auth/login', json={'username': actor['username'], 'password': PASSWORD}).status_code, 401)
        self.client.patch('/api/users/' + actor['id'], json={'active': True, 'password': 'new-test-only-password-123'})
        self.assertEqual(a.post('/api/auth/login', json={'username': actor['username'], 'password': PASSWORD}).status_code, 401)
        self.assertEqual(a.post('/api/auth/login', json={'username': actor['username'], 'password': 'new-test-only-password-123'}).status_code, 200)
        other = TestClient(main.app); self.extra.append(other)
        other.post('/api/auth/login', json={'username': actor['username'], 'password': 'new-test-only-password-123'})
        result = a.post('/api/auth/password', json={'current_password': 'new-test-only-password-123', 'new_password': 'another-test-only-password-456'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(a.get('/api/profile').status_code, 200)
        self.assertEqual(other.get('/api/profile').status_code, 401)
        with patch('app.accounts.time', return_value=time() + accounts.SESSION_SECONDS + 1):
            self.assertEqual(a.get('/api/profile').status_code, 401)

    def test_owner_password_override_preserves_existing_profile_and_invalidates_old_password(self):
        changed = self.client.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': 'new-owner-test-password-123'})
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(self.client.get('/api/profile').json()['name'], 'Técnico de prueba')
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'tecnico', 'password': PASSWORD}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'tecnico', 'password': 'new-owner-test-password-123'}).status_code, 200)

    def test_worker_data_remain_temporary_even_with_archive_setting(self):
        with patch.dict('os.environ', {'CERTICOEX_RETAIN_STUDENTS': 'true'}):
            result = self.emit(self.client)
            self.assertEqual(result['temporary_minutes'], 30)
            self.assertEqual(self.client.post('/api/drafts', json={'company': 'Prueba'}).status_code, 409)
        with sqlite3.connect(self.data / 'certicoex.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records WHERE kind IN ('history','draft','preview')").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM assets WHERE id LIKE 'jobs/%'").fetchone()[0], 0)

    def test_csrf_and_login_rate_limit(self):
        self.assertEqual(self.client.post('/api/auth/login', json={'username': 'tecnico', 'password': PASSWORD}, headers={'Origin': 'https://another.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/users', json={'username': 'outside', 'password': PASSWORD}, headers={'Origin': 'https://another.example'}).status_code, 403)
        anonymous = TestClient(main.app); self.extra.append(anonymous)
        for _ in range(20):
            self.assertEqual(anonymous.post('/api/auth/login', json={'username': 'tecnico', 'password': 'incorrecta'}).status_code, 401)
        self.assertEqual(anonymous.post('/api/auth/login', json={'username': 'tecnico', 'password': PASSWORD}).status_code, 429)
