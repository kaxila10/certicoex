"""Desktop bootstrap using only Python's standard library."""
import hashlib
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.version_info < (3, 12):
        print('Necesitas Python 3.12 o posterior. Instálalo desde https://www.python.org/downloads/')
        return 1
    folder = ROOT / '.venv'
    python = folder / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    if not python.is_file():
        print('Preparando Certicoex por primera vez…', flush=True)
        venv.EnvBuilder(with_pip=True).create(folder)
    requirements = ROOT / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = folder / '.certicoex-dependencies'
    if not marker.exists() or marker.read_text() != digest:
        print('Instalando dependencias. La primera vez necesitas conexión a Internet.', flush=True)
        result = subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], cwd=ROOT)
        if result.returncode:
            print('No se pudieron instalar las dependencias. Revisa la conexión y el mensaje anterior.')
            return result.returncode
        marker.write_text(digest)
    return subprocess.call([str(python), str(ROOT / 'scripts/serve_desktop.py')], cwd=ROOT)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
