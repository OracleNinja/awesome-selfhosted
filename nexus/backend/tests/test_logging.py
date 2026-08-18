"""Logging tests, with emphasis on the promise that secrets never appear."""

from __future__ import annotations

import json
import logging

from nexus.core.logging import (
    REDACTED,
    ConsoleFormatter,
    JsonFormatter,
    bind_request_id,
    redact,
    request_id_var,
    scrub_text,
    user_id_var,
)


def _record(message: str = "something_happened", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="nexus.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestRedaction:
    def test_password_key_is_removed(self) -> None:
        assert redact({"password": "hunter2"}) == {"password": REDACTED}

    def test_matching_is_case_insensitive_and_substring_based(self) -> None:
        result = redact(
            {
                "Authorization": "Bearer abc",
                "api_key": "k",
                "session_id": "s",
                "user_password_hash": "h",
            }
        )
        assert all(value == REDACTED for value in result.values())

    def test_nested_structures_are_redacted(self) -> None:
        result = redact({"outer": {"inner": {"token": "abc", "safe": 1}}})
        assert result["outer"]["inner"]["token"] == REDACTED
        assert result["outer"]["inner"]["safe"] == 1

    def test_lists_are_traversed(self) -> None:
        result = redact({"items": [{"secret": "s"}, {"ok": "value"}]})
        assert result["items"][0]["secret"] == REDACTED
        assert result["items"][1]["ok"] == "value"

    def test_non_sensitive_values_survive(self) -> None:
        payload = {"device_ip": "192.168.1.5", "count": 3, "ok": True}
        assert redact(payload) == payload

    def test_depth_is_bounded(self) -> None:
        """A deeply nested payload must not blow the stack inside the logger —
        the observability path has to survive the incident it is describing."""
        deep: dict = {}
        cursor = deep
        for _ in range(50):
            cursor["next"] = {}
            cursor = cursor["next"]
        result = redact(deep)
        assert "[truncated]" in json.dumps(result)


class TestTextScrubbing:
    """Key-based redaction cannot see a secret embedded in a sentence."""

    def test_url_credentials_are_removed(self) -> None:
        result = scrub_text("could not connect to postgresql://nexus:hunter2@db/nexus")
        assert "hunter2" not in result
        # The useful part survives, so the message still helps diagnosis.
        assert "db/nexus" in result
        assert "nexus:" in result

    def test_https_credentials_are_removed(self) -> None:
        assert "s3cret" not in scrub_text("posting to https://user:s3cret@hook.example")

    def test_inline_tokens_are_removed(self) -> None:
        assert "eyJhbGciOi" not in scrub_text("Authorization: Bearer eyJhbGciOi")
        assert "abc123" not in scrub_text("request failed with api_key=abc123")

    def test_ordinary_text_is_untouched(self) -> None:
        message = "device 192.168.1.73 exceeded 400 connections in 60s"
        assert scrub_text(message) == message

    def test_string_values_are_scrubbed_by_redact(self) -> None:
        result = redact({"error": "connect failed: postgres://u:p4ss@host/db"})
        assert "p4ss" not in result["error"]


class TestJsonFormatter:
    def test_emits_one_json_object(self) -> None:
        payload = json.loads(JsonFormatter().format(_record()))
        assert payload["event"] == "something_happened"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "nexus.test"
        assert payload["ts"].endswith("Z")

    def test_extra_fields_are_promoted_to_top_level(self) -> None:
        payload = json.loads(JsonFormatter().format(_record(device_ip="192.168.1.5", status=200)))
        assert payload["device_ip"] == "192.168.1.5"
        assert payload["status"] == 200

    def test_sensitive_extras_are_redacted(self) -> None:
        line = JsonFormatter().format(_record(password="hunter2", token="abc"))
        assert "hunter2" not in line
        assert "abc" not in line

    def test_request_id_is_attached_from_context(self) -> None:
        token = request_id_var.set("abc123def456")
        try:
            payload = json.loads(JsonFormatter().format(_record()))
            assert payload["request_id"] == "abc123def456"
        finally:
            request_id_var.reset(token)

    def test_user_id_is_attached_when_bound(self) -> None:
        token = user_id_var.set("user-1")
        try:
            payload = json.loads(JsonFormatter().format(_record()))
            assert payload["user_id"] == "user-1"
        finally:
            user_id_var.reset(token)

    def test_unserialisable_values_do_not_break_logging(self) -> None:
        class Opaque:
            pass

        line = JsonFormatter().format(_record(thing=Opaque()))
        payload = json.loads(line)
        assert "Opaque" in payload["thing"]

    def test_exception_text_goes_to_the_log(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = _record("failed")
            record.exc_info = sys.exc_info()
            payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]


class TestConsoleFormatter:
    def test_message_comes_first(self) -> None:
        line = ConsoleFormatter().format(_record("hello"))
        assert "hello" in line

    def test_secrets_are_redacted_there_too(self) -> None:
        line = ConsoleFormatter().format(_record(password="hunter2"))
        assert "hunter2" not in line


class TestRequestIds:
    def test_generated_ids_are_unique(self) -> None:
        assert bind_request_id() != bind_request_id()

    def test_supplied_id_is_used(self) -> None:
        assert bind_request_id("supplied-id-value") == "supplied-id-value"
