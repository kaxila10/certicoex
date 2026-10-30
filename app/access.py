"""Single-owner HTTP Basic access for the hosted trial (HTTPS at Render)."""
import base64
import binascii
import hmac
import os


def settings():
    password = os.environ.get('CERTICOEX_PASSWORD', '')
    required = bool(password) or os.environ.get('CERTICOEX_AUTH_REQUIRED', '').lower() == 'true' or os.environ.get('RENDER', '').lower() == 'true'
    return required, os.environ.get('CERTICOEX_USERNAME', 'tecnico'), password


def configured():
    required, username, password = settings()
    return not required or bool(username) and len(password) >= 16


def authorized(header):
    required, username, password = settings()
    if not required:
        return True
    if not configured() or not header or len(header) > 4096:
        return False
    try:
        scheme, encoded = header.split(' ', 1)
        if scheme.lower() != 'basic':
            return False
        decoded = base64.b64decode(encoded, validate=True).decode('utf8')
        entered_user, separator, entered_password = decoded.partition(':')
        if not separator:
            return False
        return hmac.compare_digest(entered_user.encode('utf8'), username.encode('utf8')) & hmac.compare_digest(entered_password.encode('utf8'), password.encode('utf8'))
    except (ValueError, UnicodeError, binascii.Error):
        return False
