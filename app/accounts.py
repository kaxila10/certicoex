"""Department accounts and revocable sessions, using the existing private store."""
from collections import defaultdict, deque
import hashlib
import hmac
import os
import re
import secrets
from threading import RLock
from time import time

from .access import settings


COOKIE = 'certicoex_session'
SESSION_SECONDS = 8 * 60 * 60
_attempts = defaultdict(deque)
_attempt_lock = RLock()


def enabled():
    return settings()[0] and os.environ.get('CERTICOEX_MULTIUSER', 'true').lower() == 'true'


def owner():
    return {'id': 'owner', 'username': settings()[1], 'role': 'admin', 'active': True, 'version': 0}


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,49}', value):
        raise ValueError('El usuario debe tener entre 3 y 50 caracteres: letras sin acentos, números, punto, guion o guion bajo.')
    return value


def password_hash(password):
    if not 12 <= len(password) <= 200:
        raise ValueError('La contraseña debe tener entre 12 y 200 caracteres.')
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def password_matches(password, encoded):
    try:
        method, salt, digest = encoded.split('$')
        if method != 'scrypt' or len(password) > 200:
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, digest)
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = password_hash(secrets.token_urlsafe(24))


def check_rate(client):
    with _attempt_lock:
        cutoff = time() - 15 * 60
        for key in list(_attempts):
            while _attempts[key] and _attempts[key][0] < cutoff:
                _attempts[key].popleft()
            if not _attempts[key]:
                del _attempts[key]
        return len(_attempts[client]) < 20


def failed_login(client):
    with _attempt_lock:
        _attempts[client].append(time())


def authenticate(db, login, password):
    login = login.strip().lower()
    root = owner()
    if login == root['username'].strip().lower():
        # Existing Render credentials remain the owner's initial credentials.
        custom = db.get('account', 'owner')
        if custom:
            return custom if custom['active'] and password_matches(password, custom['password_hash']) else None
        return root if hmac.compare_digest(password.encode(), settings()[2].encode()) else None
    index = db.get('account_login', login)
    account = db.get('account', index['user_id']) if index else None
    matched = password_matches(password, account['password_hash'] if account else _DUMMY_HASH)
    return account if account and account['active'] and matched else None


def create_session(db, account):
    for session in db.all('session'):
        if session['expires'] <= time():
            db.delete('session', session['id'])
    token = secrets.token_urlsafe(32)
    key = hashlib.sha256(token.encode()).hexdigest()
    db.put('session', key, {'id': key, 'user_id': account['id'], 'version': account.get('version', 0), 'expires': time() + SESSION_SECONDS})
    return token


def session_user(db, token):
    if not token or len(token) > 200:
        return None
    session = db.get('session', hashlib.sha256(token.encode()).hexdigest())
    if not session or session['expires'] <= time():
        return None
    account = db.get('account', session['user_id'])
    if account is None and session['user_id'] == 'owner':
        account = owner()
    if not account or not account['active'] or account.get('version', 0) != session['version']:
        return None
    return account


def logout(db, token):
    if token and len(token) <= 200:
        db.delete('session', hashlib.sha256(token.encode()).hexdigest())


def public(account):
    return {key: account[key] for key in ('id', 'username', 'role', 'active')}
