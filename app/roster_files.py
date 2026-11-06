"""Extract lists locally from PDFs and printed photos, without external services."""
from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from time import monotonic

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .rosters import DOCUMENT, parse_text


def _binary(setting, name):
    return shutil.which(os.environ.get(setting, name))


def _run(command, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise ValueError('La lectura ha tardado demasiado. Divide el documento o sube una foto más nítida.')
    try:
        return subprocess.run(command, capture_output=True, check=True, timeout=min(remaining, 30),
                              env={**os.environ, 'OMP_THREAD_LIMIT': '1'}).stdout.decode('utf8', errors='replace')
    except subprocess.TimeoutExpired as exc:
        raise ValueError('La lectura ha tardado demasiado. Divide el documento o sube una foto más nítida.') from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError('No se ha podido leer el documento. Prueba con una foto más nítida o pega el texto.') from exc


@lru_cache(maxsize=4)
def _language(binary):
    languages = _run([binary, '--list-langs'], monotonic() + 10).splitlines()
    if 'spa' in languages:
        return 'spa+eng' if 'eng' in languages else 'spa'
    if 'eng' in languages:
        return 'eng'
    raise ValueError('Instala los idiomas español o inglés de Tesseract para leer fotos.')


def _ocr(path, deadline):
    binary = _binary('CERTICOEX_TESSERACT', 'tesseract')
    if not binary:
        raise ValueError('Para leer fotos y PDF escaneados, instala Tesseract OCR en el servidor. Mientras tanto, pega el texto del listado.')
    return _run([binary, str(path), 'stdout', '-l', _language(binary), '--psm', '6'], deadline)


def _image(raw, destination):
    try:
        with Image.open(BytesIO(raw)) as source:
            if source.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError('Sube una imagen PNG, JPG o WebP.')
            if source.width * source.height > 20_000_000:
                raise ValueError('La foto supera 20 megapíxeles. Recórtala al listado o reduce su tamaño.')
            source.load()
            image = ImageOps.exif_transpose(source).convert('RGBA')
            background = Image.new('RGBA', image.size, 'white')
            background.alpha_composite(image)
            image = ImageOps.autocontrast(background.convert('L'))
            if image.width < 1500:
                scale = min(2, 1500 / image.width)
                image = image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)
            image.save(destination)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError('No se puede abrir la foto. Comprueba que es PNG, JPG o WebP.') from exc


def extract_list(raw, filename):
    if len(raw) > 10_000_000:
        raise ValueError('El archivo supera 10 MB.')
    extension = Path(filename).suffix.lower()
    if extension not in ('.pdf', '.png', '.jpg', '.jpeg', '.webp'):
        raise ValueError('Sube un PDF, PNG, JPG o WebP. Para un mensaje de WhatsApp, también puedes copiar y pegar su texto.')
    deadline = monotonic() + 90
    used_ocr = False
    with TemporaryDirectory(prefix='certicoex-list-') as folder:
        root = Path(folder)
        if extension == '.pdf':
            try:
                reader = PdfReader(BytesIO(raw))
                if reader.is_encrypted:
                    raise ValueError('El PDF está protegido. Sube una copia sin contraseña.')
                if not 1 <= len(reader.pages) <= 10:
                    raise ValueError('Sube un PDF de entre 1 y 10 páginas. Divide los documentos más largos.')
                page_count = len(reader.pages)
                source = root / 'list.pdf'
                source.write_bytes(raw)
                pages = []
                for number, page in enumerate(reader.pages, 1):
                    text = page.extract_text(extraction_mode='layout') or ''
                    # A header alone is not enough when the roster itself is an image.
                    if not text.strip() or not DOCUMENT.search(text) and bool(page.images):
                        renderer = _binary('CERTICOEX_PDFTOPPM', 'pdftoppm')
                        if not renderer:
                            raise ValueError('Este PDF necesita reconocimiento de texto. Instala Poppler y Tesseract en el servidor, o sube sus páginas como fotos.')
                        target = root / f'page-{number}'
                        _run([renderer, '-f', str(number), '-l', str(number), '-singlefile', '-scale-to', '2600', '-png', str(source), str(target)], deadline)
                        text = _ocr(target.with_suffix('.png'), deadline)
                        used_ocr = True
                    pages.append(text)
                    if sum(map(len, pages)) > 150_000:
                        raise ValueError('El texto supera 150.000 caracteres. Divide el listado.')
                text = '\n'.join(pages)
            except (PdfReadError, OSError, KeyError, TypeError) as exc:
                raise ValueError('No se puede leer el PDF. Comprueba que es válido y no está dañado.') from exc
        else:
            target = root / 'photo.png'
            _image(raw, target)
            text = _ocr(target, deadline)
            used_ocr, page_count = True, 1
    if not text.strip():
        raise ValueError('No se ha encontrado texto legible. Sube una foto más nítida, bien orientada y recortada al listado, o pega el texto.')
    result = parse_text(text)
    result['extracted_text'] = text
    result['source'] = 'ocr' if used_ocr else 'pdf'
    result['page_count'] = page_count
    result.setdefault('warnings', []).insert(0, 'Revisa todos los nombres y documentos frente al original antes de añadirlos.' if not used_ocr else
                                             'Lectura automática de texto impreso: puede confundir letras y cifras. Revisa cada alumno frente al original; la escritura a mano puede no reconocerse.')
    return result
