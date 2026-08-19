"""Criptografia para credenciais do módulo de mídia."""

import base64
import hashlib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _get_fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:
        raise ImproperlyConfigured(
            'Instale o pacote cryptography para usar o cofre de credenciais de mídia.'
        ) from exc

    vault_key = getattr(settings, 'MEDIA_VAULT_KEY', None)
    if vault_key:
        key = vault_key.encode() if isinstance(vault_key, str) else vault_key
    else:
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        key = base64.urlsafe_b64encode(digest)

    return Fernet(key)


def encrypt_secret(value: str) -> str:
    if not value:
        return ''
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ''
    return _get_fernet().decrypt(value.encode()).decode()
