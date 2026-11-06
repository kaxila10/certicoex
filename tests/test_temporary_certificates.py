import sqlite3
from threading import Event
from unittest.mock import patch
import unittest

from app import main, storage
from app.temporary_jobs import TemporaryJobs, temporary_jobs
import test_workflow as fixtures


class TemporaryCertificates(unittest.TestCase):
    def setUp(self):
        self.retention = patch.dict('os.environ', {'CERTICOEX_RETAIN_STUDENTS': 'false'})
        self.retention.start(); self.addCleanup(self.retention.stop)
        fixtures.Workflow.setUp(self)

    def tearDown(self):
        for key in list(temporary_jobs.jobs): temporary_jobs.delete(key)
        fixtures.Workflow.tearDown(self)

    batch = fixtures.Workflow.batch

    def test_generation_and_preview_never_persist_students_or_documents(self):
        for preview in (True, False):
            payload = self.batch(); payload['preview'] = preview
            with patch('app.main.convert_pdf', side_effect=RuntimeError('PDF no solicitado en esta prueba')):
                response = self.client.post('/api/generate', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(result['temporary_minutes'], 30)
            self.assertNotIn('history_id', result)
            self.assertEqual(self.client.get(result['word']).status_code, 200)
            self.assertEqual(self.client.get('/api/history').json(), {'total': 0, 'items': []})
            self.assertEqual(self.client.get('/api/drafts').json(), [])
            self.assertEqual(list((self.data / 'jobs').iterdir()), [])
        with sqlite3.connect(self.data / 'certicoex.sqlite3') as db:
            self.assertEqual(db.execute("SELECT count(*) FROM records WHERE kind IN ('history','preview','draft')").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM assets WHERE id LIKE 'jobs/%'").fetchone()[0], 0)
            payloads = '\n'.join(row[0] for row in db.execute('SELECT payload FROM records'))
            self.assertNotIn('Ana García López', payloads)
            self.assertNotIn('12345678Z', payloads)
        self.assertEqual(self.client.get('/api/companies').json()[0]['cif'], 'B12345678')
        storage._open.cache_clear()
        self.assertTrue(any(t['id'] == self.template for t in self.client.get('/api/templates').json()))
        self.assertTrue(self.client.get('/api/profile').json()['has_signature'])

    def test_explicit_deletion_removes_downloads_and_preserves_configuration(self):
        with patch('app.main.convert_pdf', side_effect=RuntimeError('Sin PDF')):
            result = self.client.post('/api/generate', json=self.batch()).json()
        key = result['word'].split('/')[-2]
        self.assertEqual(self.client.delete('/api/files/' + key).status_code, 200)
        self.assertEqual(self.client.get(result['word']).status_code, 404)
        self.assertEqual(self.client.delete('/api/files/' + key).status_code, 200)
        self.assertTrue(self.client.get('/api/profile').json()['has_signature'])
        self.assertEqual(len(self.client.get('/api/companies').json()), 1)

    def test_draft_api_cannot_bypass_privacy_mode_and_old_history_is_not_exposed(self):
        draft = {'students': [{'name': 'Persona ficticia', 'dni': '12345678Z'}]}
        self.assertEqual(self.client.post('/api/drafts', json=draft).status_code, 409)
        self.assertEqual(self.client.put('/api/drafts/' + 'a' * 32, json=draft).status_code, 409)
        main.open_store(self.data).put('history', 'old', {'data': draft})
        self.assertEqual(self.client.get('/api/history').json()['total'], 0)
        self.assertFalse(self.client.get('/api/health').json()['retains_students'])


class TemporaryJobLifetime(unittest.TestCase):
    def test_deadline_removes_bytes_without_needing_another_request(self):
        jobs = TemporaryJobs(lifetime=.03)
        removed = Event()
        original = jobs.delete
        def delete(key):
            original(key); removed.set()
        jobs.delete = delete
        jobs.put('job', {'pdf': b'PDF de prueba'}, 'Curso')
        self.assertTrue(removed.wait(2))
        self.assertIsNone(jobs.get('job'))
        self.assertEqual(len(jobs.jobs), 0)

    def test_memory_limit_evicts_old_jobs_and_rejects_oversized_batches(self):
        jobs = TemporaryJobs(max_bytes=6, max_jobs=2)
        try:
            jobs.put('first', {'pdf': b'1234'}, 'Curso')
            jobs.put('second', {'pdf': b'1234'}, 'Curso')
            self.assertIsNone(jobs.get('first'))
            self.assertIsNotNone(jobs.get('second'))
            with self.assertRaises(ValueError): jobs.put('large', {'pdf': b'1234567'}, 'Curso')
        finally:
            for key in list(jobs.jobs): jobs.delete(key)


if __name__ == '__main__':
    unittest.main()
