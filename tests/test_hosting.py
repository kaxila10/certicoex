import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient

from app.main import app, ROOT
from app.defaults import seed_templates


class Hosting(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict('os.environ', {
            'CERTICOEX_AUTH_REQUIRED':'true',
            'CERTICOEX_USERNAME':'tecnico',
            'CERTICOEX_PASSWORD':'test-only-password-not-deployed',
            'CERTICOEX_EPHEMERAL':'true',
        })
        self.env.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()

    def test_all_private_routes_require_credentials(self):
        for route in ('/', '/app.js', '/api/templates', '/api/profile', '/api/profile/signature', '/api/files/'+'a'*32+'/pdf', '/openapi.json'):
            response=self.client.get(route)
            self.assertEqual(response.status_code,401,route)
            self.assertIn('Basic',response.headers['www-authenticate'])
        response=self.client.post('/api/import/paste',json={'text':'Ana;12345678Z'})
        self.assertEqual(response.status_code,401)

    def test_correct_and_incorrect_login(self):
        self.assertEqual(self.client.get('/api/templates',auth=('tecnico','incorrecta')).status_code,401)
        self.assertEqual(self.client.get('/api/templates',auth=('otro','test-only-password-not-deployed')).status_code,401)
        for header in ('Basic !!!','Bearer aaa','Basic '+base64.b64encode(b'without-separator').decode()):
            self.assertEqual(self.client.get('/api/profile',headers={'Authorization':header}).status_code,401)
        response=self.client.get('/',auth=('tecnico','test-only-password-not-deployed'))
        self.assertEqual(response.status_code,200)
        self.assertIn('script-src',response.headers['content-security-policy'])

    def test_health_is_public_but_missing_password_blocks_startup(self):
        result=self.client.get('/api/health')
        self.assertEqual(result.status_code,200)
        self.assertTrue(result.json()['storage_ephemeral'])
        for value in ('','short'):
            with patch.dict('os.environ',{'CERTICOEX_PASSWORD':value}):
                self.assertEqual(self.client.get('/api/health').status_code,503)
                self.assertEqual(self.client.get('/').status_code,503)
        with patch.dict('os.environ',{'RENDER':'true','CERTICOEX_PASSWORD':'','CERTICOEX_AUTH_REQUIRED':'false'}):
            self.assertEqual(self.client.get('/').status_code,503)

    def test_seed_restores_template_without_overwriting_user_data(self):
        with tempfile.TemporaryDirectory() as folder:
            data=Path(folder);(data/'templates').mkdir()
            seed_templates(data,ROOT/'defaults')
            metadata=list((data/'templates').glob('*.json'))
            self.assertEqual(len(metadata),1)
            item=json.loads(metadata[0].read_text())
            doc=Document(data/'templates'/(item['id']+'.docx'))
            self.assertEqual(doc.core_properties.author,'')
            self.assertEqual(doc.core_properties.last_modified_by,'')
            self.assertIn('{TECNICO}', '\n'.join(p.text for p in doc.paragraphs))
            metadata[0].write_text('{"name":"Usuario"}')
            seed_templates(data,ROOT/'defaults')
            self.assertEqual(json.loads(metadata[0].read_text())['name'],'Usuario')


if __name__ == '__main__':
    unittest.main()
