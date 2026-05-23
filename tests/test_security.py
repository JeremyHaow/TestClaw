import json

from cryptography.fernet import Fernet

from app.core.redaction import (
    REDACTED_VALUE,
    is_sensitive_header,
    redact_json_text,
    redact_sensitive_text,
)
from app.core import security


def test_mask_secret():
    assert security.mask_secret("abcdef") == "**cdef"


def test_encrypt_decrypt(monkeypatch):
    monkeypatch.setattr(security.settings, "FERNET_KEY", Fernet.generate_key().decode())
    encrypted = security.encrypt_value("secret")
    assert security.decrypt_value(encrypted) == "secret"


def test_redact_json_text_redacts_non_json_legacy_logs():
    redacted = redact_json_text("legacy worker failed with password=plain-secret")

    assert redacted == "legacy worker failed with password=[REDACTED]"


def test_redact_json_text_redacts_captcha_mfa_otp_and_playwright_fills():
    payload = {
        "captcha": "captcha-secret",
        "mfa_code": "mfa-secret",
        "otp": "otp-secret",
        "login_playwright_commands": [
            'fill "Captcha" "captcha-fill-secret"',
            'type "#mfa" "mfa-fill-secret"',
            'fill "#otp" "otp-fill-secret"',
        ],
        "safe": "visible",
    }

    redacted = redact_json_text(json.dumps(payload))

    assert redacted is not None
    assert "captcha-secret" not in redacted
    assert "mfa-secret" not in redacted
    assert "otp-secret" not in redacted
    assert "captcha-fill-secret" not in redacted
    assert "mfa-fill-secret" not in redacted
    assert "otp-fill-secret" not in redacted
    assert "visible" in redacted
    assert REDACTED_VALUE in redacted


def test_redaction_treats_session_jwt_csrf_xsrf_headers_as_sensitive():
    payload = {
        "headers": {
            "X-JWT": "jwt-header-secret",
            "X-Session-ID": "session-header-secret",
            "X-CSRF": "csrf-header-secret",
            "X-XSRF": "xsrf-header-secret",
            "X-Trace-ID": "trace-safe",
        }
    }

    redacted = redact_json_text(json.dumps(payload))

    assert redacted is not None
    assert is_sensitive_header("X-JWT")
    assert is_sensitive_header("X-Session-ID")
    assert is_sensitive_header("X-CSRF")
    assert is_sensitive_header("X-XSRF")
    assert "jwt-header-secret" not in redacted
    assert "session-header-secret" not in redacted
    assert "csrf-header-secret" not in redacted
    assert "xsrf-header-secret" not in redacted
    assert "trace-safe" in redacted


def test_redact_sensitive_text_redacts_embedded_playwright_fill_and_type_values():
    redacted = redact_sensitive_text(
        'Login blocked after fill "#email" "email-fill-secret", '
        'type e2 "typed-value-secret", and type e3 plain-value-secret'
    )

    assert "email-fill-secret" not in redacted
    assert "typed-value-secret" not in redacted
    assert "plain-value-secret" not in redacted
    assert f'fill "#email" "{REDACTED_VALUE}"' in redacted
    assert f'type e2 "{REDACTED_VALUE}"' in redacted
    assert f"type e3 {REDACTED_VALUE}" in redacted
    assert redact_sensitive_text(redacted) == redacted


def test_redact_sensitive_text_redacts_spaced_credential_markers():
    redacted = redact_sensitive_text(
        "JWT jwt-secret, CSRF csrf-secret, X-CSRF xcsrf-secret, "
        "X-XSRF xsrf-secret, Cookie session=cookie-secret"
    )

    for secret in ("jwt-secret", "csrf-secret", "xcsrf-secret", "xsrf-secret", "cookie-secret"):
        assert secret not in redacted
    assert REDACTED_VALUE in redacted
