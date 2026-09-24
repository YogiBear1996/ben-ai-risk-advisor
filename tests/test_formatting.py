from ben.channels.formatting import split_message, to_telegram_html, to_whatsapp


def test_telegram_html_escapes_and_converts():
    out = to_telegram_html("**High-risk** under *Art. 6* & `AIRC-002` <script>\n- item")
    assert "<b>High-risk</b>" in out
    assert "<i>Art. 6</i>" in out
    assert "<code>AIRC-002</code>" in out
    assert "&amp;" in out and "&lt;script&gt;" in out
    assert "• item" in out


def test_code_spans_are_not_formatted():
    assert to_telegram_html("`a**b**c`") == "<code>a**b**c</code>"


def test_whatsapp_formatting():
    out = to_whatsapp("## Heading\n**bold** and *italic*\n- point [link](https://x.org)")
    assert "*Heading*" in out
    assert "*bold*" in out and "_italic_" in out
    assert "• point link (https://x.org)" in out


def test_split_message_respects_limit_and_paragraphs():
    text = "\n\n".join(f"Paragraph {i}. " + "word " * 50 for i in range(20))
    parts = split_message(text, 600)
    assert all(len(p) <= 600 for p in parts)
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace(
        "\n", ""
    )
    assert split_message("short", 100) == ["short"]
    assert split_message("", 100) == []


def test_split_message_handles_single_huge_line():
    parts = split_message("x" * 2500, 1000)
    assert [len(p) for p in parts] == [1000, 1000, 500]
