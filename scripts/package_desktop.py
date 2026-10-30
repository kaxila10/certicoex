"""Build an allowlisted ZIP, excluding signatures, profiles and generated jobs."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import json

from docx import Document

ROOT = Path(__file__).resolve().parents[1]


def build(destination):
    sources = []
    for name in ('app', 'static'):
        sources.extend(p for p in (ROOT / name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    sources.extend(ROOT / n for n in ('requirements.txt', 'requirements-dev.txt', 'README.md', 'LEEME-PRIMERO.txt', 'INICIAR.bat', 'INICIAR.command'))
    sources.extend(ROOT / 'scripts' / n for n in ('launch.py', 'serve_desktop.py', 'setup_office.sh', 'import_example.py', 'browser_smoke.py'))
    sources.extend(p for p in (ROOT / 'tests').glob('test_*.py'))
    template_dir = ROOT / '.local/data/templates'
    templates = []
    for metadata in template_dir.glob('*.json'):
        meta = json.loads(metadata.read_text(encoding='utf8'))
        template = template_dir / (meta['id'] + '.docx')
        doc = Document(template)
        markers = [n.get('descr') for n in doc.element.iter() if n.tag.endswith('}docPr')]
        # Only the locally sanitized example is bundled. Arbitrary uploaded
        # templates may contain personal data and must not be auto-packaged.
        if 'certicoex_signature' in markers:
            templates.extend([metadata, template])
    if len(templates) != 2:
        raise ValueError('Se esperaba una única plantilla de ejemplo adaptada.')
    sources.extend(templates)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
        for path in sorted(sources):
            relative = Path('Certicoex') / path.relative_to(ROOT)
            info = ZipInfo(str(relative).replace('\\', '/'))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o100755 if path.suffix in ('.command', '.sh') else 0o100644) << 16
            archive.writestr(info, path.read_bytes())
    return destination


if __name__ == '__main__':
    print(build(ROOT / '.local/releases/Certicoex.zip'))
