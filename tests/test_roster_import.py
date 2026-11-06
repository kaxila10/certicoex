import os
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import main
from app.rosters import parse_text, tabular
from app.roster_files import extract_list


def text_pdf(lines):
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    content = DecodedStreamObject()
    escaped = [line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') for line in lines]
    content.set_data(('BT /F1 12 Tf 40 790 Td 24 TL ' + ' '.join(f'({line}) Tj T*' for line in escaped) + ' ET').encode('latin1'))
    page[NameObject('/Contents')] = writer._add_object(content)
    buffer = BytesIO(); writer.write(buffer)
    return buffer.getvalue()


def printed_photo():
    image = Image.new('RGB', (2000, 450), 'white')
    font = ImageFont.truetype('DejaVuSans.ttf', 44)
    draw = ImageDraw.Draw(image)
    draw.text((55, 60), '12345678Z GARCIA LOPEZ, ANA', fill='black', font=font)
    draw.text((55, 145), 'X1234567L PEREZ MARTIN, LUIS', fill='black', font=font)
    out = BytesIO(); image.save(out, format='PNG')
    return out.getvalue()


class TextLists(unittest.TestCase):
    def test_email_document_first_surname_comma_dates_and_missing_last_date(self):
        text = 'DNI/NIF TRABAJADOR FORMACIÓN PRL\n'
        text += '\n'.join(f'{i:08d}Z APELLIDO COMPUESTO, PERSONA {"02/12/2021" if i < 14 else ""}' for i in range(1, 15))
        result = parse_text(text)
        self.assertEqual(len(result['rows']), 14)
        self.assertEqual(result['rows'][0], ['PERSONA APELLIDO COMPUESTO', '00000001Z'])
        self.assertEqual(result['rows'][-1][0], 'PERSONA APELLIDO COMPUESTO')
        self.assertEqual(result['unparsed'], [])
        self.assertIn('02/12/2021', result['warnings'][0])

    def test_document_at_either_end_and_formatted_nie(self):
        result = parse_text('• Ana García López 12.345.678-z\n1. x 1234567-l PÉREZ MARTÍN, LUIS 2021-12-09')
        self.assertEqual(result['rows'], [['Ana García López', '12345678Z'], ['LUIS PÉREZ MARTÍN', 'X1234567L']])

    def test_whatsapp_export_and_name_document_on_separate_lines(self):
        result = parse_text('[09:34, 06/11/2026] Empresa: Ana García López\nDNI: 12345678Z\n06/11/2026, 09:35 - Empresa: X1234567L\nLuis Pérez Martín')
        self.assertEqual(result['rows'], [['Ana García López', '12345678Z'], ['Luis Pérez Martín', 'X1234567L']])
        self.assertEqual(result['unparsed'], [])

    def test_unknown_lines_and_ambiguous_records_are_never_silently_discarded(self):
        result = parse_text('Buenos días\n12345678Z GARCÍA, ANA\n8765432IX PÉREZ, LUIS\n12345678Z Ana 87654321X Luis\nGracias')
        self.assertEqual(result['rows'], [['ANA GARCÍA', '12345678Z']])
        self.assertEqual([line['line'] for line in result['unparsed']], [1, 3, 4, 5])
        self.assertIn('8765432IX', result['unparsed'][1]['text'])

    def test_tabular_formats_and_dni_nif_header(self):
        for separator in ('\t', ';', ','):
            result = parse_text(separator.join(['DNI/NIF', 'Trabajador', 'Fecha']) + '\n' + separator.join(['12345678Z', 'Ana García', '02/12/2021']))
            self.assertEqual(result['mapping']['dni'], 0)
            self.assertEqual(result['mapping']['name'], 1)
            self.assertEqual(len(result['rows']), 1)
        result = parse_text('12345678Z;Ana García\nX1234567L;Luis Pérez')
        self.assertEqual(result['mapping']['dni'], 0)
        self.assertEqual(result['mapping']['name'], 1)
        self.assertEqual(parse_text('Ana;P123456\nLuis;P987654')['rows'][0], ['Ana', 'P123456'])
        result = parse_text('DNI/NIF;Trabajador;Fecha\n12345678Z;GARCÍA LÓPEZ, ANA;02/12/2021')
        self.assertEqual(result['rows'][0][1], 'ANA GARCÍA LÓPEZ')
        self.assertIn('02/12/2021', result['warnings'][0])

    def test_limits_do_not_truncate_students(self):
        for text in ('\n'.join(f'{i:08d}Z Ana García' for i in range(501)), 'a' * 150001):
            with self.assertRaises(ValueError): parse_text(text)
        with self.assertRaises(ValueError): tabular([['Ana', '12345678Z']] * 501)


class FileLists(unittest.TestCase):
    def test_selectable_pdf_extracts_without_ocr(self):
        with patch('app.roster_files._ocr', side_effect=AssertionError('Should use existing PDF text')):
            result = extract_list(text_pdf(['DNI/NIF TRABAJADOR FORMACION PRL', '12345678Z GARCIA LOPEZ, ANA 02/12/2021', 'X1234567L PEREZ MARTIN, LUIS']), 'listado.pdf')
        self.assertEqual(result['source'], 'pdf')
        self.assertEqual(result['rows'], [['ANA GARCIA LOPEZ', '12345678Z'], ['LUIS PEREZ MARTIN', 'X1234567L']])
        self.assertIn('02/12/2021', result['extracted_text'])
        with patch('app.roster_files._ocr', side_effect=AssertionError('Keep readable text for manual correction')):
            result = extract_list(text_pdf(['P1234567 GARCIA, ANA']), 'pasaportes.pdf')
        self.assertEqual(result['source'], 'pdf')
        self.assertIn('P1234567', result['unparsed'][0]['text'])

    def test_missing_ocr_and_malformed_files_have_actionable_errors(self):
        with patch('app.roster_files._binary', return_value=None):
            with self.assertRaisesRegex(ValueError, 'Tesseract'): extract_list(printed_photo(), 'list.png')
        for raw, name in ((b'bad', 'list.pdf'), (b'bad', 'list.png'), (b'bad', 'list.exe'), (b'x' * 10000001, 'list.pdf')):
            with self.subTest(name=name):
                with self.assertRaises(ValueError): extract_list(raw, name)

    def test_pdf_page_limit_and_password(self):
        writer = PdfWriter()
        for _ in range(11): writer.add_blank_page(width=100, height=100)
        out = BytesIO(); writer.write(out)
        with self.assertRaisesRegex(ValueError, '10 páginas'): extract_list(out.getvalue(), 'list.pdf')
        writer.encrypt('test-only'); out = BytesIO(); writer.write(out)
        with self.assertRaisesRegex(ValueError, 'contraseña'): extract_list(out.getvalue(), 'list.pdf')

    @unittest.skipUnless(os.environ.get('CERTICOEX_TEST_OCR') == '1', 'Activar con CERTICOEX_TEST_OCR=1')
    def test_real_printed_photo_and_scanned_pdf(self):
        photo = printed_photo()
        result = extract_list(photo, 'foto.png')
        self.assertEqual(result['source'], 'ocr')
        self.assertEqual(result['rows'], [['ANA GARCIA LOPEZ', '12345678Z'], ['LUIS PEREZ MARTIN', 'X1234567L']])
        image = Image.open(BytesIO(photo)); out = BytesIO(); image.save(out, format='PDF', resolution=120)
        result = extract_list(out.getvalue(), 'escaneado.pdf')
        self.assertEqual(result['source'], 'ocr')
        self.assertEqual(result['rows'], [['ANA GARCIA LOPEZ', '12345678Z'], ['LUIS PEREZ MARTIN', 'X1234567L']])


class ImportAPI(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patch = patch.object(main, 'DATA', self.data)
        self.patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close(); self.patch.stop(); self.temp.cleanup()

    def test_file_import_only_returns_preview_without_saving_students_or_history(self):
        response = self.client.post('/api/import/document', files={'file': ('list.pdf', text_pdf(['12345678Z GARCIA, ANA']), 'application/pdf')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['rows'], [['ANA GARCIA', '12345678Z']])
        self.assertEqual(self.client.get('/api/history').json()['total'], 0)
        self.assertEqual(self.client.get('/api/drafts').json(), [])
        self.assertEqual(list(self.data.glob('*.pdf')), [])

    def test_file_import_obeys_access_origin_and_validation(self):
        files = {'file': ('list.png', b'bad', 'image/png')}
        self.assertEqual(self.client.post('/api/import/document', files=files).status_code, 422)
        self.assertEqual(self.client.post('/api/import/document', files=files, headers={'Origin': 'https://another.example'}).status_code, 403)
        with patch.dict(os.environ, {'CERTICOEX_AUTH_REQUIRED': 'true', 'CERTICOEX_PASSWORD': 'test-password-not-deployed'}):
            self.assertEqual(self.client.post('/api/import/document', files=files).status_code, 401)


if __name__ == '__main__':
    unittest.main()
