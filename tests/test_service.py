from __future__ import annotations

from tests.fakes import text_response, tool_response
from tests.helpers import make_service, tg_msg


async def test_memory_keeps_last_turns_and_reset_clears(settings):
    settings.ben_history_turns = 2
    service, client = make_service(settings, [text_response(f"answer {i}") for i in range(4)])
    for i in range(3):
        assert await service.handle(tg_msg(f"question {i}")) == f"answer {i}"
    # Third call saw the two previous exchanges.
    sent = client.messages.calls[2]["messages"]
    assert [m["content"] for m in sent] == [
        "question 0",
        "answer 0",
        "question 1",
        "answer 1",
        "question 2",
    ]

    assert "fresh" in await service.handle(tg_msg("/reset"))
    await service.handle(tg_msg("after reset"))
    assert [m["content"] for m in client.messages.calls[3]["messages"]] == ["after reset"]


async def test_memory_is_per_chat(settings):
    service, client = make_service(settings, [text_response("a"), text_response("b")])
    await service.handle(tg_msg("hello", chat="1"))
    await service.handle(tg_msg("hi", chat="2"))
    assert len(client.messages.calls[1]["messages"]) == 1


async def test_sources_command_shows_last_citations(settings):
    service, _ = make_service(
        settings, [tool_response("search_library", {"query": "x"}), text_response("ok")]
    )
    assert "didn't use" in await service.handle(tg_msg("/sources"))
    await service.handle(tg_msg("what is high risk?"))
    reply = await service.handle(tg_msg("/sources@BenBot"))
    assert "EU AI Act summary — Art. 6, p.3" in reply


async def test_commands(settings):
    service, client = make_service(settings, [])
    assert "Ben" in await service.handle(tg_msg("/start"))
    assert "/reset" in await service.handle(tg_msg("/help"))
    assert "EU AI Act summary" in await service.handle(tg_msg("/frameworks"))
    assert "Unknown command" in await service.handle(tg_msg("/nope"))
    assert client.messages.calls == []


async def test_duplicate_deliveries_and_non_text(settings):
    service, client = make_service(settings, [text_response("once")])
    assert await service.handle(tg_msg("q", mid="1:1")) == "once"
    assert await service.handle(tg_msg("q", mid="1:1")) is None
    assert "text messages" in await service.handle(tg_msg(""))
    assert len(client.messages.calls) == 1
