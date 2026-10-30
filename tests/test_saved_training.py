import hashlib
import shutil
import sqlite3
import unittest
from urllib.parse import unquote
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import main, storage
from app.validation import identity_status
from app.naming import document_name
import test_workflow as fixtures


class SavedTraining(unittest.TestCase):
    setUp = fixtures.Workflow.setUp
    tearDown = fixtures.Workflow.tearDown
    batch = fixtures.Workflow.batch

    def emit(self, payload=None):
        with patch('app.main.convert_pdf',side_effect=RuntimeError('PDF de prueba no solicitado')):
            response=self.client.post('/api/generate',json=payload or self.batch())
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_partial_draft_can_be_updated_recovered_and_deleted(self):
        data={'title':'Formación pendiente','company':'Empresa de prueba','students':[{'name':'','dni':''}]}
        response=self.client.post('/api/drafts',json=data)
        self.assertEqual(response.status_code,200,response.text)
        key=response.json()['id'];data['students'][0]['name']='Ana'
        self.assertEqual(self.client.put('/api/drafts/'+key,json=data).status_code,200)
        storage._open.cache_clear()
        records=self.client.get('/api/drafts').json()
        self.assertEqual(len(records),1)
        self.assertEqual(records[0]['data']['students'][0]['name'],'Ana')
        self.assertEqual(self.client.delete('/api/drafts/'+key).status_code,200)
        self.assertEqual(self.client.get('/api/drafts').json(),[])

    def test_companies_are_shared_and_browser_import_keeps_newer_server_data(self):
        response=self.client.post('/api/companies',json={'name':'Empresa actual','cif':'B12345678'})
        self.assertEqual(response.status_code,200,response.text)
        key=response.json()['id']
        other=TestClient(main.app)
        try:
            self.assertEqual(other.get('/api/companies').json()[0]['name'],'Empresa actual')
        finally:other.close()
        response=self.client.post('/api/companies/import',json=[{'id':'anterior','name':'Nombre antiguo','cif':'b12345678'},{'name':'Otra empresa','cif':'B87654321'}])
        self.assertEqual(len(response.json()),2)
        self.assertIn('Empresa actual',[c['name'] for c in response.json()])
        self.client.post('/api/companies',json={'id':key,'name':'Empresa actual','cif':'B12345679'})
        companies=self.client.get('/api/companies').json()
        self.assertEqual(len(companies),2)
        self.assertEqual(next(c for c in companies if c['id']==key)['cif'],'B12345679')
        self.assertEqual(self.client.post('/api/companies',json={'name':' ','cif':'B1'}).status_code,422)

    def test_course_duration_and_name_survive_reopening_database(self):
        response=self.client.put('/api/templates/'+self.template+'/settings',json={'name':'Carretillas · Prueba','hours':8})
        self.assertEqual(response.status_code,200,response.text)
        storage._open.cache_clear()
        course=next(c for c in self.client.get('/api/templates').json() if c['id']==self.template)
        self.assertEqual(course['name'],'Carretillas · Prueba');self.assertEqual(course['hours'],8)
        self.assertEqual(self.client.put('/api/templates/'+self.template+'/settings',json={'name':'Prueba','hours':0}).status_code,422)

    def test_history_search_preserves_original_download_when_corrected(self):
        first=self.emit()
        old=self.client.get(first['word']).content
        data=self.batch();data['students'][0]['name']='Ana García Corregida'
        second=self.emit(data)
        self.assertNotEqual(first['history_id'],second['history_id'])
        records=self.client.get('/api/history',params={'q':'garcia','course_date':'2026-10-30'}).json()
        self.assertEqual(records['total'],2)
        self.assertEqual(self.client.get('/api/history',params={'q':'87654321X'}).json()['total'],2)
        self.assertEqual(self.client.get('/api/history',params={'course_date':'2025-01-01'}).json()['total'],0)
        shutil.rmtree(self.data/'jobs')
        storage._open.cache_clear()
        self.assertEqual(self.client.get(first['word']).content,old)
        self.assertNotEqual(hashlib.sha256(self.client.get(second['word']).content).digest(),hashlib.sha256(old).digest())

    def test_profile_signature_and_template_are_restored_from_database(self):
        signature=self.client.get('/api/profile/signature').content
        (self.data/'signature.png').unlink();(self.data/'profile.json').unlink()
        shutil.rmtree(self.data/'templates')
        storage._open.cache_clear()
        self.assertEqual(self.client.get('/api/profile').json(),{'name':'Técnico de prueba','has_signature':True})
        self.assertEqual(self.client.get('/api/profile/signature').content,signature)
        self.assertTrue(main.template_path(self.template).is_file())

    def test_preview_is_not_history_and_expired_preview_is_rejected(self):
        data=self.batch();data['preview']=True
        result=self.emit(data)
        self.assertEqual(self.client.get('/api/history').json()['total'],0)
        key=result['word'].split('/')[-2]
        main.open_store(self.data).put('preview',key,{'id':key,'created':0})
        self.assertEqual(self.client.get(result['word']).status_code,404)

    def test_download_names_use_course_company_prefix_and_course_date(self):
        self.client.put('/api/templates/'+self.template+'/settings',json={'name':'Prevención agrícola','hours':8})
        data=self.batch();data['company']='Hortícola del Sur, S. L., Grupo de prueba'
        for preview in (True,False):
            data['preview']=preview
            result=self.emit(data)
            self.assertEqual(result['filename'],'Prevención agrícola - Hortícola del Sur - 30-10-2026')
            header=self.client.get(result['word']).headers['content-disposition']
            self.assertEqual(unquote(header.split("filename*=UTF-8''")[1]),'Prevención agrícola - Hortícola del Sur - 30-10-2026.docx')
            self.assertEqual(result['count'],2)
        self.client.put('/api/templates/'+self.template+'/settings',json={'name':'Nombre cambiado','hours':4})
        self.assertIn('Prevenci%C3%B3n',self.client.get(result['word']).headers['content-disposition'])
        record=main.open_store(self.data).get('history',result['history_id'])
        del record['files']['filename']
        main.open_store(self.data).put('history',record['id'],record)
        self.assertIn('Prevenci%C3%B3n',self.client.get(result['word']).headers['content-disposition'])

    def test_filename_removes_unsafe_characters_and_limits_byte_length(self):
        name=document_name('Curso / PRL: "básico"\r\n','Empresa\\Nombre, S.L.','2026-10-30')
        self.assertEqual(name,'Curso PRL básico - Empresa Nombre - 30-10-2026')
        self.assertLessEqual(len((document_name('á'*200,'ñ'*200,'2026-10-30')+'.docx').encode('utf8')),255)

    def test_identity_review_detects_letters_duplicates_and_missing_values(self):
        self.assertEqual(identity_status('12345678Z'),'valid')
        self.assertEqual(identity_status('00000000T'),'valid')
        self.assertEqual(identity_status('X0000000T'),'valid')
        self.assertEqual(identity_status('12345678A'),'letter')
        result=self.client.post('/api/review',json={'students':[{'name':'Ana','dni':'12345678Z'},{'name':'Luis','dni':'12345678A'},{'name':'Eva','dni':'12.345.678-z'},{'name':'','dni':''}]}).json()
        self.assertEqual([i['row'] for i in result['issues']],[2,3,4])
        self.assertIn('Letra',result['issues'][0]['messages'][0])

    def test_files_and_metadata_are_committed_together(self):
        db=main.open_store(self.data)
        with self.assertRaises(sqlite3.IntegrityError):
            db.bundle('history','transaction-test',{'id':'transaction-test'},{'first-test':b'valid','second-test':None})
        self.assertIsNone(db.asset('first-test'))
        self.assertIsNone(db.get('history','transaction-test'))


class PostgresConfiguration(unittest.TestCase):
    def test_remote_connections_require_verified_tls(self):
        with patch('psycopg.connect') as connect:
            db=storage.Store.__new__(storage.Store)
            db.url='postgresql://test-user:fake-fixture@example.invalid/test?sslmode=require'
            with db.connection():pass
            self.assertEqual(connect.call_args.kwargs['sslmode'],'verify-full')
            self.assertTrue(connect.call_args.kwargs['sslrootcert'])
