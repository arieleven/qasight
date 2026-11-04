from __future__ import annotations

import json
from pathlib import Path

import pytest

from qasight.security.redaction import (
    REDACTED,
    extra_patterns,
    redact_run_artifacts,
    redact_text,
    redact_value,
)


def test_redact_text_masks_token_patterns() -> None:
    payload = "Authorization: Bearer sk-test-abc123456789"
    assert "sk-test-abc123456789" not in redact_text(payload)


def test_redact_value_masks_nested_structures() -> None:
    payload = {
        "error": "api_key=SECRET12345678",
        "items": ["Bearer verysecret12345"],
    }
    redacted = redact_value(payload)
    assert redacted["error"] == "***REDACTED***"
    assert redacted["items"][0] == "***REDACTED***"


def test_redact_value_masks_sensitive_mapping_keys() -> None:
    payload = {
        "api_key": "SECRET12345678",
        "nested": {
            "authorization": "Bearer runtime-token-123456",
            "safe": "diagnostic evidence",
        },
    }

    redacted = redact_value(payload)

    assert redacted["api_key"] == "***REDACTED***"
    assert redacted["nested"]["authorization"] == "***REDACTED***"
    assert redacted["nested"]["safe"] == "diagnostic evidence"


@pytest.mark.parametrize(
    "secret",
    [
        "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "glpat-abcdefghij1234567890",
        "npm_abcdefghijklmnopqrstuvwxyz0123456789",
        "xoxb-123456789012-abcdefghijkl",
        "AKIAIOSFODNN7EXAMPLE",
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123",
        "dXNlcjpwYXNzd29yZA==",
        "hunter22",
        "MIIEowIBAAKCAQEA",
        "AIzaSyA-abcdefghijklmnopqrstuvwxyz12345",
        "abcdefghijk123",
    ],
)
def test_common_secret_formats_are_masked(secret: str) -> None:
    text = "\n".join(
        [
            "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcDEF123",
            "Authorization: Basic dXNlcjpwYXNzd29yZA==",
            "GET https://ci:hunter22@git.example.com/repo.git",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
            '{"access_token": "abcdefghijk123"}',
            "tokens: github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz "
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789 glpat-abcdefghij1234567890 "
            "npm_abcdefghijklmnopqrstuvwxyz0123456789 xoxb-123456789012-abcdefghijkl "
            "AKIAIOSFODNN7EXAMPLE AIzaSyA-abcdefghijklmnopqrstuvwxyz12345",
        ]
    )
    assert secret not in redact_text(text)


def test_url_keeps_the_user_name_and_ordinary_text_survives() -> None:
    assert redact_text("postgresql://u:pw123456@db/t") == "postgresql://u:***REDACTED***@db/t"
    for text in ("assert 1 == 2", "https://example.com/pkg@v1", "http://localhost:8000/api"):
        assert redact_text(text) == text


def test_more_sensitive_mapping_keys() -> None:
    redacted = redact_value({"password": "p", "DATABASE_URL": "d", "privateKey": "k", "name": "n"})
    assert redacted == {
        "password": "***REDACTED***",
        "DATABASE_URL": "***REDACTED***",
        "privateKey": "***REDACTED***",
        "name": "n",
    }


# -- secrets in stored artifacts ---------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "AKIAABCDEFGHIJKLMNOP",
        "ghp_" + "a" * 36,
        "github_pat_" + "B" * 40,
        "glpat-" + "c" * 20,
        "xoxb-1234567890-abcdefghij",
        "https://hooks.slack.com/services/T000/B000/XXXXXXXX",
        "AIza" + "d" * 35,
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop",
    ],
)
def test_common_token_shapes_are_hidden(secret: str) -> None:
    out = redact_text(f"request failed with {secret} in the header")
    assert secret not in out
    assert REDACTED in out
    assert out.startswith("request failed with ")


def test_passwords_url_credentials_and_private_keys_are_hidden() -> None:
    text = (
        "login password=hunter22 failed\n"
        "GET https://admin:s3cr3tpass@db.example.com/x\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nAAAA\n-----END RSA PRIVATE KEY-----"
    )
    out = redact_text(text)
    assert "hunter22" not in out and "s3cr3tpass" not in out and "MIIEow" not in out
    assert "https://admin:***REDACTED***@db.example.com/x" in out


def test_ordinary_test_output_is_left_alone() -> None:
    text = "AssertionError: assert 1 == 2\n  where password = None\ntests/test_shop.py:12"
    assert redact_text(text) == text


def test_user_patterns_come_from_the_environment() -> None:
    env = {"TESTO_REDACT_PATTERNS": "ACME-[0-9]{6}\n\n(unclosed"}
    patterns = extra_patterns(env)
    assert len(patterns) == 1  # the broken one is skipped with a warning
    assert redact_text("order ACME-123456 failed", patterns) == f"order {REDACTED} failed"


def test_run_artifacts_are_rewritten_and_stay_valid_json(tmp_path: Path) -> None:
    results = tmp_path / "web" / "allure-results"
    results.mkdir(parents=True)
    case = {
        "name": "test_login",
        "status": "failed",
        "statusDetails": {
            "message": 'token=abcdefgh12345678 rejected, url "https://u:p4ssw0rd@x.io"',
            "trace": "Bearer abcdefghijklmnop\\n  at login()",
        },
    }
    result_file = results / "1-result.json"
    result_file.write_text(json.dumps(case), encoding="utf-8")
    log = tmp_path / "web" / "stage.log"
    log.write_text("using ghp_" + "z" * 36 + "\n", encoding="utf-8")
    binary = tmp_path / "web" / "trace.zip"
    binary.write_bytes(b"PK\x03\x04 token=abcdefgh12345678")
    clean = tmp_path / "web" / "ok.txt"
    clean.write_text("all good", encoding="utf-8")

    assert redact_run_artifacts(tmp_path, env={}) == 2

    stored = json.loads(result_file.read_text(encoding="utf-8"))
    assert "abcdefgh12345678" not in stored["statusDetails"]["message"]
    assert "p4ssw0rd" not in stored["statusDetails"]["message"]
    assert "abcdefghijklmnop" not in stored["statusDetails"]["trace"]
    assert "ghp_" not in log.read_text(encoding="utf-8")
    assert b"token=abcdefgh12345678" in binary.read_bytes()


def test_run_artifact_redaction_can_be_switched_off(tmp_path: Path) -> None:
    (tmp_path / "a.log").write_text("token=abcdefgh12345678", encoding="utf-8")
    assert redact_run_artifacts(tmp_path, env={"TESTO_REDACT": "0"}) == 0
    assert "abcdefgh12345678" in (tmp_path / "a.log").read_text(encoding="utf-8")
