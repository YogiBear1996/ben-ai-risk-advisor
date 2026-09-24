from __future__ import annotations

import csv
from datetime import timedelta

from sqlmodel import select

from ben.core.models import Channel, IncomingMessage
from ben.security.allowlist import is_allowed
from ben.security.ratelimit import RateLimiter
from ben.security.redaction import redact
from ben.storage.db import session_scope
from ben.storage.models import AuditLog, Conversation, Message, utcnow
from ben.storage.repo import AuditRepo, run_retention
from tests.fakes import text_response, tool_response
from tests.helpers import make_service, tg_msg

# --- redaction ---------------------------------------------------------------


def test_redacts_emails_phones_and_ids():
    r = redact(
        "Mail jane.doe@example.com or call +971 50 123 4567 / 050-123-4567. "
        "Emirates ID 784-1990-1234567-1."
    )
    assert "example.com" not in r.text and "123 4567" not in r.text
    assert r.counts == {"EMAIL": 1, "PHONE": 2, "EMIRATES_ID": 1}


def test_redacts_passport_and_booking_references():
    r = redact("Passport no: N1234567, PNR X7K2QP and booking ref: ABCDEF. Also P98765432.")
    assert r.text == (
        "Passport no: [PASSPORT], PNR [BOOKING_REF] and booking ref: [BOOKING_REF]. "
        "Also [PASSPORT]."
    )


def test_redaction_leaves_governance_text_alone():
    text = (
        "Under EU AI Act Art. 6 and ISO/IEC 42001:2023 clause 6.1.4, control AIRC-004 "
        "applies from 2026-08-02 to GENAI SYSTEM owners (Regulation (EU) 2024/1689)."
    )
    assert redact(text).text == text
    assert not redact(text).redacted


# --- allowlist & rate limit ---------------------------------------------------


def test_allowlist(settings):
    assert is_allowed(settings, Channel.TELEGRAM, "111")
    assert not is_allowed(settings, Channel.TELEGRAM, "333")
    assert not is_allowed(settings, Channel.TELEGRAM, "abc")
    assert is_allowed(settings, Channel.WHATSAPP, "971500000001")
    assert is_allowed(settings, Channel.WHATSAPP, "+971 50 000 0001")
    assert not is_allowed(settings, Channel.WHATSAPP, "971500000002")
    assert is_allowed(settings, Channel.CLI, "anyone")


def test_empty_allowlist_denies_everyone(settings):
    settings.telegram_allowed_user_ids = []
    assert not is_allowed(settings, Channel.TELEGRAM, "111")


def test_rate_limiter_bucket():
    now = [0.0]
    limiter = RateLimiter(per_minute=6, burst=2, clock=lambda: now[0])
    assert limiter.allow("u") and limiter.allow("u")
    assert not limiter.allow("u")
    assert limiter.allow("other")
    now[0] += 10  # one token per 10s
    assert limiter.allow("u")
    assert not limiter.allow("u")


# --- service integration -------------------------------------------------------


def _audit_rows(settings) -> list[AuditLog]:
    return AuditRepo(settings).entries()


async def test_unknown_user_is_refused_and_logged(settings):
    service, client = make_service(settings, [])
    reply = await service.handle(tg_msg("hello", user="999", chat="999"))
    assert "only available" in reply and "999" in reply
    assert client.messages.calls == []
    (row,) = _audit_rows(settings)
    assert (row.status, row.user_id, row.channel) == ("denied", "999", "telegram")


async def test_rate_limited_user(settings):
    settings.rate_limit_per_minute, settings.rate_limit_burst = 1, 1
    service, client = make_service(settings, [text_response("first")])
    assert await service.handle(tg_msg("one")) == "first"
    assert "wait" in await service.handle(tg_msg("two"))
    assert [r.status for r in _audit_rows(settings)] == ["ok", "rate_limited"]


async def test_audit_log_records_full_exchange_with_redaction(settings):
    service, client = make_service(
        settings,
        [tool_response("search_library", {"query": "x"}), text_response("Noted, jane@x.com.")],
    )
    await service.handle(tg_msg("My email is jane@x.com, PNR X7K2QP - is this high-risk?"))
    (row,) = _audit_rows(settings)
    assert row.status == "ok"
    assert "jane@x.com" not in row.question and "[EMAIL]" in row.question
    assert "[BOOKING_REF]" in row.question
    assert row.answer == "Noted, [EMAIL]."
    assert row.redacted is True
    assert row.model == settings.ben_model
    assert row.input_tokens == 20 and row.output_tokens == 10
    assert row.sources[0]["doc_title"] == "EU AI Act summary"
    assert row.tool_calls[0]["name"] == "search_library"
    assert row.latency_ms >= 0
    # By default the model still sees the original text...
    assert "jane@x.com" in client.messages.calls[0]["messages"][-1]["content"]
    # ...but stored memory is redacted.
    with session_scope(settings) as s:
        stored = [m.content for m in s.exec(select(Message))]
    assert all("jane@x.com" not in c for c in stored)


async def test_redact_before_model(settings):
    settings.redact_before_model = True
    service, client = make_service(settings, [text_response("ok")])
    await service.handle(tg_msg("Call me on +971 50 123 4567"))
    assert client.messages.calls[0]["messages"][-1]["content"] == "Call me on [PHONE]"


async def test_redaction_can_be_disabled_for_logs(settings):
    settings.redact_before_logging = False
    service, _ = make_service(settings, [text_response("ok")])
    await service.handle(tg_msg("jane@x.com"))
    assert _audit_rows(settings)[0].question == "jane@x.com"


async def test_audit_export_csv(settings, tmp_path):
    service, _ = make_service(settings, [text_response("answer, with comma")])
    await service.handle(tg_msg("question"))
    await service.handle(tg_msg("/help"))
    out = tmp_path / "audit.csv"
    assert AuditRepo(settings).export_csv(out) == 2
    rows = list(csv.DictReader(out.open()))
    assert rows[0]["answer"] == "answer, with comma"
    assert rows[0]["model"] == settings.ben_model
    assert rows[1]["status"] == "command"
    future = utcnow() + timedelta(days=1)
    assert AuditRepo(settings).export_csv(out, since=future) == 0


async def test_retention_deletes_old_conversations_and_audit(settings):
    settings.retention_days, settings.audit_retention_days = 30, 365
    service, _ = make_service(settings, [text_response("a"), text_response("b")])
    await service.handle(tg_msg("old", chat="1"))
    await service.handle(IncomingMessage(Channel.TELEGRAM, "2", "222", "new"))
    with session_scope(settings) as s:
        old = s.exec(select(Conversation).where(Conversation.chat_id == "1")).one()
        old.updated_at = utcnow() - timedelta(days=31)
        s.add(old)
        audit = s.exec(select(AuditLog)).first()
        audit.timestamp = utcnow() - timedelta(days=400)
        s.add(audit)
        s.commit()
    assert run_retention(settings) == {"conversations": 1, "audit_entries": 1}
    with session_scope(settings) as s:
        assert [c.chat_id for c in s.exec(select(Conversation))] == ["2"]
        assert len(s.exec(select(Message)).all()) == 2
