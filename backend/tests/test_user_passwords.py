import hashlib

from app.crud.user import get_password_hash, verify_password


def test_generated_password_hash_verifies_plain_password() -> None:
    password_hash = get_password_hash("password123")

    assert verify_password("password123", password_hash) is True
    assert verify_password("wrong-password", password_hash) is False


def test_raw_sha256_password_hash_is_not_accepted() -> None:
    password_hash = hashlib.sha256("password123".encode()).hexdigest()

    assert verify_password("password123", password_hash) is False


def test_bcrypt_password_hash_is_not_accepted() -> None:
    password_hash = "$2b$12$5kaumg2iZsSFXLDlUEA9Xukev6HlaKzb../uMgyGB30/qoc3vc.t."

    assert verify_password("password123", password_hash) is False


def test_unknown_password_hash_returns_false() -> None:
    assert verify_password("password123", "not-a-supported-password-hash") is False
