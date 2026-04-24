from cryptography.fernet import Fernet

from app.core import security


def test_mask_secret():
    assert security.mask_secret("abcdef") == "**cdef"


def test_encrypt_decrypt(monkeypatch):
    monkeypatch.setattr(security.settings, "FERNET_KEY", Fernet.generate_key().decode())
    encrypted = security.encrypt_value("secret")
    assert security.decrypt_value(encrypted) == "secret"
