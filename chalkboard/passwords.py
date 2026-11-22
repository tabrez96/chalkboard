from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2-cffi's default type is Argon2id
MIN_LENGTH = 8

_unknown_user_hash: str | None = None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError, VerificationError, InvalidHashError:
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def check_unknown_user(password: str) -> None:
    """Spend the time of a real check so a missing email can't be told from a wrong password."""
    global _unknown_user_hash
    if _unknown_user_hash is None:
        _unknown_user_hash = hash_password("unknown-user")
    verify_password(_unknown_user_hash, password)


def normalise_email(email: str) -> str:
    return email.strip().lower()
