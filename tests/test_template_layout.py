from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import os
import shutil
import tempfile
import sqlite3
import unittest
from zipfile import ZipFile
from unittest.mock import patch

from docx import Document
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import main, storage
from app.documents import render_batch
from app.layout import inspect_layout
import test_accounts as accounts_fixtures
import test_workflow as fixtures


def changed_word(original):
    doc = Document(BytesIO(original)); doc.add_paragraph('VERSION NUEVA DE PRUEBA')
    output = BytesIO(); doc.save(output)
    return output.getvalue()


class TemplateReplacement(unittest.TestCase):
    setUp = accounts_fixtures.Accounts.setUp
    tearDown = accounts_fixtures.Accounts.tearDown
    technician = accounts_fixtures.Accounts.technician
    batch = fixtures.Workflow.batch

    def replace(self, content, version=0, client=None):
        return (client or self.client).put(f'/api/templates/{self.template}/file',
                data={'version': str(version)}, files={'file': ('revision.docx', content)})

    def test_replacement_preserves_course_and_can_restore_previous_word(self):
        original = main.template_path(self.template).read_bytes()
        updated_word = changed_word(original)
        self.client.put(f'/api/templates/{self.template}/settings',
                        json={'name': 'Curso compartido', 'hours': 8, 'expected_pages': 2})
        result = self.replace(updated_word)
        self.assertEqual(result.status_code, 200, result.text)
        meta = result.json()
        self.assertEqual((meta['id'], meta['name'], meta['hours'], meta['expected_pages']),
                         (self.template, 'Curso compartido', 8, 2))
        self.assertEqual(meta['version'], 1); self.assertTrue(meta['has_previous'])
        self.assertEqual(meta['filename'], 'revision.docx')
        storage._open.cache_clear()
        db = main.open_store(self.data)
        self.assertEqual(db.asset('templates/' + self.template + '/previous'), original)
        self.assertEqual(len(self.client.get('/api/templates').json()), 1)
        tech, _ = self.technician('tecnico-a')
        self.assertEqual(tech.get(f'/api/templates/{self.template}/file').content, updated_word)
        restored = self.client.post(f'/api/templates/{self.template}/restore', json={'version': 1})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['filename'], 'plantilla.docx')
        self.assertEqual(restored.json()['expected_pages'], 2)
        self.assertEqual(self.client.get(f'/api/templates/{self.template}/file').content, original)
        self.assertEqual(db.asset('templates/' + self.template + '/previous'), updated_word)
        self.client.delete(f'/api/templates/{self.template}')
        self.assertIsNone(db.asset('templates/' + self.template + '/previous'))

    def test_invalid_unauthorized_and_stale_replacements_preserve_current_word(self):
        original = main.template_path(self.template).read_bytes()
        invalid = BytesIO(); Document().save(invalid)
        bad_xml = BytesIO()
        with ZipFile(BytesIO(original)) as source, ZipFile(bad_xml, 'w') as target:
            for item in source.infolist():
                target.writestr(item, b'<broken' if item.filename == 'word/document.xml' else source.read(item.filename))
        for content in (b'not a Word', invalid.getvalue(), bad_xml.getvalue()):
            self.assertEqual(self.replace(content).status_code, 422)
            self.assertEqual(self.client.get(f'/api/templates/{self.template}/file').content, original)
        tech, _ = self.technician('tecnico-a')
        self.assertEqual(self.replace(original, client=tech).status_code, 403)
        self.assertEqual(tech.post(f'/api/templates/{self.template}/restore', json={'version': 0}).status_code, 403)
        fresh = TestClient(main.app); self.extra.append(fresh)
        self.assertEqual(self.replace(original, client=fresh).status_code, 401)
        self.assertEqual(self.client.post(f'/api/templates/{self.template}/restore', json={'version': 0}).status_code, 404)
        new = changed_word(original)
        self.assertEqual(self.replace(new).status_code, 200)
        self.assertEqual(self.replace(original).status_code, 409)
        self.assertEqual(self.client.post(f'/api/templates/{self.template}/restore', json={'version': 0}).status_code, 409)
        self.assertEqual(self.client.get(f'/api/templates/{self.template}/file').content, new)

    def test_concurrent_replacements_have_one_winner_and_keep_original_backup(self):
        original = main.template_path(self.template).read_bytes()
        new = changed_word(original)
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(self.replace, (new, new)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 409])
        db = main.open_store(self.data)
        self.assertEqual(db.asset('templates/' + self.template + '/previous'), original)
        old_settings = {'name': 'Curso renombrado', 'hours': 6}
        updated = db.update_template(self.template, old_settings)
        self.assertEqual(updated['version'], 1)
        self.assertTrue(updated['has_previous'])
        self.assertEqual(db.asset('templates/' + self.template), new)

    def test_generation_uses_a_consistent_snapshot_during_replacement(self):
        original = main.template_path(self.template).read_bytes()
        new = changed_word(original)
        def replace_during_render(template, *args):
            self.assertEqual(self.replace(new).status_code, 200)
            return render_batch(template, *args)
        with patch('app.main.render_batch', side_effect=replace_during_render), patch('app.main.convert_pdf', side_effect=RuntimeError('Sin PDF en esta prueba')):
            result = self.client.post('/api/generate', json=self.batch())
        self.assertEqual(result.status_code, 200, result.text)
        word = Document(BytesIO(self.client.get(result.json()['word']).content))
        self.assertNotIn('VERSION NUEVA DE PRUEBA', '\n'.join(p.text for p in word.paragraphs))
        self.assertEqual(self.client.get(f'/api/templates/{self.template}/file').content, new)

    def test_failed_write_rolls_back_current_and_backup_together(self):
        original = main.template_path(self.template).read_bytes()
        db = main.open_store(self.data)
        with db.connection() as connection:
            connection.execute("CREATE TRIGGER prevent_replace BEFORE UPDATE ON assets BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            db.replace_template(self.template, changed_word(original), ['NOMBRE'], 'new.docx', 0)
        self.assertEqual(db.asset('templates/' + self.template), original)
        self.assertIsNone(db.asset('templates/' + self.template + '/previous'))
        self.assertEqual(db.get('template', self.template).get('version', 0), 0)


def make_pdf(path, pages):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject('/Type'): NameObject('/Font'),
               NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')}))
    for text, x, y in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        escaped = text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream.set_data(f'BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET'.encode('latin1'))
        page[NameObject('/Contents')] = writer._add_object(stream)
    with path.open('wb') as target:
        writer.write(target)


class LayoutChecks(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.word = self.root / 'test.docx'
        doc = Document(); doc.sections[0].header.paragraphs[0].text = 'CABECERA'
        doc.save(self.word)
        self.pdf = self.root / 'test.pdf'

    def test_blank_header_only_and_signature_pages_are_flagged_without_worker_text(self):
        make_pdf(self.pdf, [('CERTIFICADO ALUMNO 00000000T', 50, 700), ('CABECERA', 50, 800),
                            ('', 50, 700), ('TECNICO EN PRL PERSONA TECNICA', 50, 700)])
        report = inspect_layout(self.pdf, self.word, 1, common={'TECNICO': 'PERSONA TECNICA', 'CARGO_TECNICO': 'TECNICO EN PRL'})
        self.assertTrue(report['checked'])
        issues = {issue['code']: issue for issue in report['warnings']}
        self.assertIn('page_count', issues)
        self.assertEqual(issues['empty_page']['pages'], [2, 3])
        self.assertEqual(issues['signature_page']['pages'], [4])
        self.assertNotIn('00000000T', str(report)); self.assertNotIn('PERSONA TECNICA', str(report))

    def test_planned_two_page_certificates_do_not_raise_page_count_warning(self):
        make_pdf(self.pdf, [('CERTIFICADO ALUMNO 00000000T', 50, 700), ('TEMARIO Y CONTENIDO', 50, 700)])
        report = inspect_layout(self.pdf, self.word, 1, expected_pages=2)
        self.assertTrue(report['checked']); self.assertEqual(report['warnings'], [])

    @unittest.skipUnless(shutil.which('pdftotext'), 'Requiere Poppler')
    def test_text_outside_page_is_reported(self):
        make_pdf(self.pdf, [('FUERA DEL BORDE', -4, 700)])
        report = inspect_layout(self.pdf, self.word, 1)
        self.assertTrue(report['geometry_checked'])
        self.assertIn('clipped_text', [issue['code'] for issue in report['warnings']])

    def test_missing_geometry_tool_and_failed_analysis_are_advisory(self):
        make_pdf(self.pdf, [('CERTIFICADO ALUMNO 00000000T', 50, 700)])
        with patch('app.layout.shutil.which', return_value=None):
            report = inspect_layout(self.pdf, self.word, 1)
        self.assertTrue(report['checked']); self.assertFalse(report['geometry_checked'])
        self.assertEqual(report['warnings'], [])
        self.pdf.write_bytes(b'not a PDF')
        report = inspect_layout(self.pdf, self.word, 1)
        self.assertFalse(report['checked']); self.assertEqual(report['warnings'][0]['code'], 'unavailable')


class LayoutIntegration(unittest.TestCase):
    setUp = fixtures.Workflow.setUp
    tearDown = fixtures.Workflow.tearDown
    batch = fixtures.Workflow.batch

    @unittest.skipUnless(os.environ.get('CERTICOEX_TEST_PDF') == '1', 'Requiere LibreOffice')
    def test_real_blank_pages_are_reported_and_pdf_remains_downloadable(self):
        doc = Document(main.template_path(self.template)); doc.add_page_break(); doc.add_paragraph('')
        source = BytesIO(); doc.save(source)
        template = self.client.post('/api/templates', data={'name': 'Plantilla con salto'}, files={'file': ('salto.docx', source.getvalue())}).json()
        for preview in (False, True):
            payload = self.batch(); payload.update(template_id=template['id'], preview=preview)
            result = self.client.post('/api/generate', json=payload)
            self.assertEqual(result.status_code, 200, result.text)
            data = result.json()
            self.assertTrue(data['layout']['checked'])
            codes = [issue['code'] for issue in data['layout']['warnings']]
            self.assertIn('page_count', codes); self.assertIn('empty_page', codes)
            self.assertEqual(self.client.get(data['pdf']).status_code, 200)
            self.assertEqual(self.client.get(data['word']).status_code, 200)
