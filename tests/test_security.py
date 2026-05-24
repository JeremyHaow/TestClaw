import json

from cryptography.fernet import Fernet

from app.core.redaction import (
    REDACTED_VALUE,
    is_sensitive_header,
    redact_json_text,
    redact_sensitive_data,
    redact_sensitive_text,
)
from app.core import security
from app.worker.tasks import _safe_task_result


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


def test_redact_json_text_strips_control_chars_from_keys_and_values():
    payload = {
        "api_execution_result": {
            "results": [
                {
                    "body": {
                        "bad\x00key": "safe\x07value",
                    }
                }
            ]
        }
    }

    redacted = redact_json_text(json.dumps(payload, ensure_ascii=False))

    assert redacted is not None
    assert "\x00" not in redacted
    assert "\x07" not in redacted
    assert "\\u0000" not in redacted
    assert "\\u0007" not in redacted
    parsed = json.loads(redacted)
    body = parsed["api_execution_result"]["results"][0]["body"]
    assert body == {"badkey": "safevalue"}


def test_redaction_preserves_nested_openapi_source_json_security_shape():
    openapi_source = json.dumps(
        {
            "openapi": "3.0.3",
            "info": {"title": "Example API", "version": "1.0.0"},
            "servers": [{"url": "https://api.example.test"}],
            "paths": {
                "/private": {
                    "get": {
                        "security": [{"Authorization": []}],
                        "responses": {"200": {"description": "OK"}},
                        "description": "example password=source-secret",
                    }
                }
            },
            "components": {
                "securitySchemes": {
                    "Authorization": {
                        "type": "apiKey",
                        "name": "Authorization",
                        "in": "header",
                    }
                }
            },
        }
    )
    payload = {
        "source_input": openapi_source,
        "auth_headers": {"Authorization": "Bearer runtime-secret"},
    }

    redacted = redact_sensitive_data(payload)
    serialized = json.dumps(redacted, ensure_ascii=False)
    nested_source = json.loads(redacted["source_input"])

    assert nested_source["paths"]["/private"]["get"]["security"] == [{"Authorization": []}]
    assert (
        nested_source["components"]["securitySchemes"]["Authorization"]["name"]
        == "Authorization"
    )
    assert "source-secret" not in serialized
    assert "runtime-secret" not in serialized


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


def test_worker_task_result_strips_runtime_auth_material():
    safe = _safe_task_result(
        {
            "db_session": object(),
            "auth_config": {"enabled": True, "password": "password-secret"},
            "auth_headers": {"Authorization": "Bearer token-secret"},
            "custom_headers": {"X-Api-Key": "api-key-secret"},
            "auth_credentials": {"username": "admin", "password": "credential-secret"},
            "auth_preflight": {"auth_preflight_id": "cached-preflight-token"},
            "login_playwright_commands": ['fill "#password" "fill-secret"'],
            "setup_instructions": "password=setup-secret",
            "safe": "visible",
        }
    )

    serialized = json.dumps(safe, ensure_ascii=False)
    assert "auth_config" not in safe
    assert "auth_headers" not in safe
    assert "custom_headers" not in safe
    assert "auth_credentials" not in safe
    assert "auth_preflight" not in safe
    for secret in (
        "password-secret",
        "token-secret",
        "api-key-secret",
        "credential-secret",
        "cached-preflight-token",
        "fill-secret",
        "setup-secret",
    ):
        assert secret not in serialized
    assert "visible" in serialized
