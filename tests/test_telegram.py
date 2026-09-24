from __future__ import annotations

from telegram import Update
from telegram.error import BadRequest

from ben.channels.telegram import TelegramAdapter
from ben.core.models import Channel


class FakeBot:
    def __init__(self, fail_html: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail_html = fail_html

    async def send_message(self, **kwargs):
        if self.fail_html and kwargs.get("parse_mode"):
            raise BadRequest("Can't parse entities")
        self.sent.append(kwargs)

    async def send_chat_action(self, **kwargs):
        pass


UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 42,
        "date": 1700000000,
        "chat": {"id": 111, "type": "private"},
        "from": {"id": 111, "is_bot": False, "first_name": "Ada", "last_name": "L"},
        "text": "  What is Art. 6?  ",
    },
}


def test_parse_incoming_normalises_message():
    (msg,) = TelegramAdapter(FakeBot()).parse_incoming(UPDATE)
    assert msg.channel is Channel.TELEGRAM
    assert (msg.chat_id, msg.user_id, msg.text) == ("111", "111", "What is Art. 6?")
    assert msg.display_name == "Ada L"
    assert msg.message_id == "111:42"


def test_parse_ignores_non_messages_and_bots():
    adapter = TelegramAdapter(FakeBot())
    assert adapter.parse_incoming({"update_id": 2, "edited_message": {}}) == []
    bot_msg = {**UPDATE, "message": {**UPDATE["message"], "from": {"id": 5, "is_bot": True}}}
    assert adapter.parse_incoming(bot_msg) == []


def test_non_text_message_has_empty_text():
    photo = {**UPDATE, "message": {k: v for k, v in UPDATE["message"].items() if k != "text"}}
    (msg,) = TelegramAdapter(FakeBot()).parse_incoming(photo)
    assert msg.text == ""


def test_polling_updates_parse_the_same_way():
    update = Update.de_json(UPDATE, None)
    (msg,) = TelegramAdapter(FakeBot()).parse_incoming(update.to_dict())
    assert msg.user_id == "111"


async def test_send_reply_formats_and_chunks():
    bot = FakeBot()
    adapter = TelegramAdapter(bot)
    long_text = "\n\n".join(f"**Point {i}** " + "detail " * 80 for i in range(12))
    count = await adapter.send_reply("111", long_text)
    assert count == len(bot.sent) > 1
    assert all(m["parse_mode"] == "HTML" for m in bot.sent)
    assert all(len(m["text"]) <= 4096 for m in bot.sent)
    assert bot.sent[0]["text"].startswith("<b>Point 0</b>")
    assert bot.sent[-1]["text"].endswith(f"({count}/{count})")


async def test_falls_back_to_plain_text_when_html_rejected():
    bot = FakeBot(fail_html=True)
    await TelegramAdapter(bot).send_reply("111", "**bold** & more")
    assert bot.sent == [{"chat_id": "111", "text": "bold & more"}]
