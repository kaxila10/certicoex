"""One-time, local adaptation of the user's supplied certificate."""
import argparse
from pathlib import Path
import tempfile

from app.documents import prepare_example
from app.main import store_template

parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
parser.add_argument('--name', default='Manipulado, envasado y control de maquinaria')
args = parser.parse_args()
with tempfile.TemporaryDirectory() as folder:
    output = Path(folder) / 'plantilla.docx'
    prepare_example(args.source, output)
    meta = store_template(output.read_bytes(), args.name)
    print('Plantilla adaptada:', meta['id'])
