"""Restore the prepared course template in a new, empty installation."""
import hashlib
import json
from pathlib import Path

from .documents import inspect_template


def seed_templates(data: Path, source: Path):
    target = data / 'templates'
    if any(target.glob('*.json')) or not source.is_dir():
        return
    for path in sorted(source.glob('*.docx')):
        content = path.read_bytes()
        fields = inspect_template(content)
        key = hashlib.sha256(content).hexdigest()[:32]
        (target / (key + '.docx')).write_bytes(content)
        name = path.stem.replace('_', ' ')
        (target / (key + '.json')).write_text(json.dumps({'id':key, 'name':name, 'fields':fields}, ensure_ascii=False), encoding='utf8')
