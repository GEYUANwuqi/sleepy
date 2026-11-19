"""Credential primitives shared by initialization, CLI recovery and the API."""

import hashlib
import secrets
import string

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
SYMBOLS = "!@#$%^&*()-_=+[]{}:,.?"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def token() -> str:
    return secrets.token_urlsafe(32)


def validate_password(value: str) -> str:
    if not 8 <= len(value) <= 256:
        raise ValueError("密码长度必须为 8–256 位")
    groups = (string.ascii_uppercase, string.ascii_lowercase, string.digits, string.punctuation)
    if not all(any(c in group for c in value) for group in groups):
        raise ValueError("密码必须包含英文大写、小写、数字和符号")
    return value


def random_password() -> str:
    alphabet = string.ascii_letters + string.digits + SYMBOLS
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(12))
        try:
            return validate_password(value)
        except ValueError:
            continue


def verify_password(encoded: str, password: str) -> bool:
    try:
        return HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False
