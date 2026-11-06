"""Local, advisory PDF layout checks. No worker text is retained in results."""
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from docx import Document
from pypdf import PdfReader

from .documents import document_roots, paragraph_text, paragraphs


def normalized(text):
    return ' '.join(text.split()).casefold()


def page_content(pdf, reader):
    binary = shutil.which(os.environ.get('CERTICOEX_PDFTOTEXT', 'pdftotext'))
    if binary:
        try:
            result = subprocess.run([binary, '-bbox', '-cropbox', '-enc', 'UTF-8', str(pdf), '-'],
                                    capture_output=True, timeout=25)
            if result.returncode == 0 and len(result.stdout) < 32_000_000:
                root = ET.fromstring(result.stdout)
                pages = []
                for page in root.iter():
                    if page.tag.rsplit('}', 1)[-1] != 'page':
                        continue
                    words = [w for w in page.iter() if w.tag.rsplit('}', 1)[-1] == 'word']
                    width, height = float(page.get('width')), float(page.get('height'))
                    outside = any(float(w.get('xMin')) < -1 or float(w.get('yMin')) < -1 or
                                  float(w.get('xMax')) > width + 1 or float(w.get('yMax')) > height + 1
                                  for w in words)
                    pages.append((' '.join(''.join(w.itertext()) for w in words), outside))
                if len(pages) == len(reader.pages):
                    return pages, True
        except (OSError, subprocess.TimeoutExpired, ET.ParseError, TypeError, ValueError):
            pass
    return [((page.extract_text() or ''), False) for page in reader.pages], False


def inspect_layout(pdf, word, students, expected_pages=1, common=None):
    """Report possible issues; failure here must never discard a generated PDF."""
    report = {'checked': False, 'geometry_checked': False, 'warnings': []}
    try:
        reader = PdfReader(pdf)
        content, geometry = page_content(pdf, reader)
        report.update(checked=True, geometry_checked=geometry, pages=len(content),
                      expected_pages_per_student=expected_pages)
        if len(content) != students * expected_pages:
            report['warnings'].append({'code': 'page_count', 'page': 1,
                'message': f'El PDF tiene {len(content)} páginas; se esperaban {students * expected_pages} '
                           f'({expected_pages} por alumno). Revisa posibles saltos de página o ajusta las páginas previstas en Plantillas.'})
        doc = Document(word)
        decorations = {normalized(paragraph_text(p)) for root in document_roots(doc)[1:]
                       for p in paragraphs(root) if paragraph_text(p).strip()}
        labels = ['técnico en prl', 'técnica en prl', 'técnica superior en prl', 'la persona interesada']
        common = common or {}
        if common.get('CARGO_TECNICO'):
            labels.append(normalized(common['CARGO_TECNICO']))
        technician = normalized(common.get('TECNICO', ''))
        blank, signatures, outside = [], [], []
        for index, (text, clipped) in enumerate(content, 1):
            body = normalized(text)
            for decoration in sorted(decorations, key=len, reverse=True):
                body = body.replace(decoration, ' ')
            body = normalized(body)
            body = re.sub(r'\b(?:página|page)\s+\d+(?:\s+(?:de|of)\s+\d+)?\b', '', body)
            if not re.search(r'[^\W\d_]', body):
                blank.append(index)
            elif len(body.split()) <= 40 and technician and technician in body:
                if any(body.startswith(label) for label in labels) or body in (
                        technician, 'd. ' + technician, 'dña. ' + technician):
                    signatures.append(index)
            if clipped:
                outside.append(index)
        for code, pages, description in (
                ('empty_page', blank, 'Posibles páginas vacías, con solo cabecera/pie o sin texto del certificado'),
                ('signature_page', signatures, 'Posibles páginas con el bloque de firma separado del certificado'),
                ('clipped_text', outside, 'Texto que parece sobrepasar los bordes de la página')):
            if pages:
                shown = ', '.join(map(str, pages[:20])) + ('…' if len(pages) > 20 else '')
                report['warnings'].append({'code': code, 'pages': pages[:20], 'page': pages[0],
                                          'message': f'{description}: {shown}. Compruébalas en la vista previa.'})
    except Exception:
        report.update(checked=False, warnings=[{'code': 'unavailable',
                      'message': 'No se ha podido revisar la maquetación automáticamente. Comprueba el PDF en la vista previa.'}])
    return report
