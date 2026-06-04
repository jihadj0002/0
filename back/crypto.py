import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _get_fernet():
    key = base64.urlsafe_b64encode(
        hashlib.sha256(settings.ENCRYPT_KEY.encode()).digest()
    )
    return Fernet(key)


def encrypt_value(plaintext: str) -> str:
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    if not token:
        return ""
    f = _get_fernet()
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
