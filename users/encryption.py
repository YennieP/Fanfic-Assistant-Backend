import os
from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ.get('ENCRYPTION_KEY', '')
    if not key:
        raise ValueError('ENCRYPTION_KEY 环境变量未设置')
    return Fernet(key.encode())


def encrypt_key(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_key(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()