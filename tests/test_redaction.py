"""Targeted redaction regressions for Plan Mode Chinese credential templates.

These tests guard the contract added by `.trellis/tasks/05-28-agent-plan-planner-intelligence`
which extends `redact_sensitive_text` to cover `账号是X` / `密码是X` / `验证码是X` / etc.
Earlier broader redaction tests live in `tests/test_security.py`.
"""

from app.core.redaction import REDACTED_VALUE, redact_sensitive_text


def test_redacts_chinese_credential_templates():
    cases = [
        ("登录账号是admin，密码是admin123", "登录账号是[REDACTED]，密码是[REDACTED]"),
        ("账号是alice 密码是pass", "账号是[REDACTED] 密码是[REDACTED]"),
        ("用户名是bob，密码为secret", "用户名是[REDACTED]，密码为[REDACTED]"),
        ("用户名为carl，口令是topsecret", "用户名为[REDACTED]，口令是[REDACTED]"),
        ("验证码是1234", "验证码是[REDACTED]"),
        ("验证码为9999", "验证码为[REDACTED]"),
        ("tenant是acme，租户是foo", "tenant是[REDACTED]，租户是[REDACTED]"),
    ]
    for source, expected in cases:
        result = redact_sensitive_text(source)
        assert result == expected, f"source={source!r} -> {result!r}, expected {expected!r}"
        assert REDACTED_VALUE in result
        # Idempotent: second pass through redaction must not introduce changes.
        assert redact_sensitive_text(result) == result


def test_chinese_credential_redaction_only_redacts_values():
    # Keys must remain readable; only the value after 是/为 is redacted.
    sample = "登录账号是Alice，密码是p@ss-WORD_99，验证码为123456"
    redacted = redact_sensitive_text(sample)
    assert "登录账号是" in redacted
    assert "密码是" in redacted
    assert "验证码为" in redacted
    assert "Alice" not in redacted
    assert "p@ss-WORD_99" not in redacted
    assert "123456" not in redacted


def test_chinese_credential_redaction_preserves_unrelated_text():
    sample = "这是普通描述：测试公开页面，没有凭据信息。"
    redacted = redact_sensitive_text(sample)
    assert redacted == sample


def test_chinese_credential_redaction_stops_at_punctuation_boundary():
    # Trailing Chinese / ASCII punctuation must not be eaten by the value match.
    sample = "登录账号是admin。这是另一句。"
    redacted = redact_sensitive_text(sample)
    assert "登录账号是[REDACTED]。" in redacted
    assert "另一句" in redacted


def test_chinese_credential_redaction_does_not_break_english_secret_patterns():
    sample = "password=secret-token Bearer real-token"
    redacted = redact_sensitive_text(sample)
    assert "secret-token" not in redacted
    assert "real-token" not in redacted
    assert "[REDACTED]" in redacted
