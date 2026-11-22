from chalkboard.passwords import (
    check_unknown_user,
    hash_password,
    needs_rehash,
    normalise_email,
    verify_password,
)


def test_hash_is_argon2id():
    assert hash_password("x" * 8).startswith("$argon2id$")


def test_verify_true_and_false():
    hashed = hash_password("correct horse")
    assert verify_password(hashed, "correct horse") is True
    assert verify_password(hashed, "wrong") is False


def test_verify_garbage_hash_is_false():
    assert verify_password("not-a-hash", "x") is False


def test_normalise_email():
    assert normalise_email("  Aamina@Example.COM ") == "aamina@example.com"


def test_needs_rehash_for_weaker_parameters():
    from argon2 import PasswordHasher

    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("x" * 8)
    assert needs_rehash(weak) is True
    assert needs_rehash(hash_password("x" * 8)) is False


def test_check_unknown_user_returns_none():
    assert check_unknown_user("anything") is None
