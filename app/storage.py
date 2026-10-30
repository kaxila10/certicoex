"""Single-owner records and assets. SQLite locally; PostgreSQL for hosted persistence."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import json
import os
import sqlite3
import ssl
from urllib.parse import urlsplit


class StorageUnavailable(Exception):
    pass


class Store:
    def __init__(self, folder, url=''):
        self.folder = Path(folder)
        self.url = url
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, modified TEXT NOT NULL, PRIMARY KEY (kind,id))')
            db.execute('CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, content BYTEA NOT NULL)')

    @contextmanager
    def connection(self):
        if self.url:
            import psycopg
            # TLS with hostname and CA verification for all remote connections.
            options = {'connect_timeout':10}
            if urlsplit(self.url).hostname not in ('localhost', '127.0.0.1', '::1'):
                options.update(sslmode='verify-full', sslrootcert=ssl.get_default_verify_paths().cafile)
            try:
                db = psycopg.connect(self.url, **options)
            except psycopg.Error:
                raise StorageUnavailable('No se ha podido conectar con el almacenamiento permanente.') from None
        else:
            db = sqlite3.connect(self.folder / 'certicoex.sqlite3', timeout=30)
        try:
            with db:
                yield db
        except Exception as exc:
            if self.url and isinstance(exc, psycopg.Error):
                raise StorageUnavailable('No se ha podido completar la operación de almacenamiento.') from None
            raise
        finally:
            db.close()

    def sql(self, statement):
        return statement.replace('?', '%s') if self.url else statement

    def get(self, kind, key, default=None):
        with self.connection() as db:
            row = db.execute(self.sql('SELECT payload FROM records WHERE kind=? AND id=?'), (kind,key)).fetchone()
        return json.loads(row[0]) if row else default

    def all(self, kind):
        with self.connection() as db:
            rows = db.execute(self.sql('SELECT payload FROM records WHERE kind=? ORDER BY modified DESC,id'), (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def put(self, kind, key, value):
        with self.connection() as db:
            db.execute(self.sql('INSERT INTO records(kind,id,payload,modified) VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,modified=excluded.modified'), (kind,key,json.dumps(value,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        return value

    def delete(self, kind, key):
        with self.connection() as db:
            db.execute(self.sql('DELETE FROM records WHERE kind=? AND id=?'), (kind,key))

    def asset(self, key):
        with self.connection() as db:
            row = db.execute(self.sql('SELECT content FROM assets WHERE id=?'), (key,)).fetchone()
        return bytes(row[0]) if row else None

    def put_asset(self, key, content):
        with self.connection() as db:
            db.execute(self.sql('INSERT INTO assets(id,content) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content'), (key,content))

    def bundle(self, kind, key, value, assets):
        """Publish metadata and its files in the same transaction."""
        with self.connection() as db:
            for name, content in assets.items():
                db.execute(self.sql('INSERT INTO assets(id,content) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET content=excluded.content'), (name,content))
            db.execute(self.sql('INSERT INTO records(kind,id,payload,modified) VALUES(?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload,modified=excluded.modified'), (kind,key,json.dumps(value,ensure_ascii=False),datetime.now(timezone.utc).isoformat()))
        return value

    def delete_asset(self, key):
        with self.connection() as db:
            db.execute(self.sql('DELETE FROM assets WHERE id=?'), (key,))


@lru_cache(maxsize=32)
def _open(folder, url):
    store = Store(folder, url)
    # One-time import of the earlier file-based installation. Never remove it.
    if not store.get('settings', 'file-migration'):
        root = Path(folder)
        for path in (root/'templates').glob('*.json'):
            meta = json.loads(path.read_text(encoding='utf8'))
            document = path.with_suffix('.docx')
            if document.exists():
                store.put_asset('templates/'+meta['id'], document.read_bytes())
                store.put('template', meta['id'], meta)
        profile = root/'profile.json'
        if profile.exists():
            store.put('settings','profile',json.loads(profile.read_text(encoding='utf8')))
        if (root/'signature.png').exists():
            store.put_asset('signature',(root/'signature.png').read_bytes())
        store.put('settings','file-migration',{'done':True})
    return store


def open_store(folder):
    return _open(str(Path(folder).resolve()), os.environ.get('CERTICOEX_DATABASE_URL',''))
