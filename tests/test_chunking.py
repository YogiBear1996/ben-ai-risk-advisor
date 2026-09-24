from ben.knowledge.chunker import chunk_sections
from ben.knowledge.loaders import Section, is_clause_heading


def test_short_sections_become_single_chunks_with_heading():
    chunks = chunk_sections(
        [Section("Article 6", "Short text.", page=3), Section("Article 9", "More.", page=4)]
    )
    assert [(c.section, c.page, c.text) for c in chunks] == [
        ("Article 6", 3, "Short text."),
        ("Article 9", 4, "More."),
    ]


def test_long_section_split_on_paragraphs_with_overlap_and_size_cap():
    paras = [f"Paragraph {i} " + "word " * 60 for i in range(10)]
    chunks = chunk_sections([Section("Clause 8", "\n\n".join(paras))], max_chars=800, overlap=100)
    assert len(chunks) > 1
    assert all(len(c.text) <= 800 for c in chunks)
    assert all(c.section == "Clause 8" for c in chunks)
    # overlap: the start of chunk 2 repeats the end of chunk 1
    assert chunks[1].text.split("\n\n")[0] in chunks[0].text


def test_giant_paragraph_without_breaks_is_still_capped():
    chunks = chunk_sections([Section(None, "x" * 5000)], max_chars=1000, overlap=0)
    assert all(len(c.text) <= 1000 for c in chunks)
    assert "".join(c.text for c in chunks) == "x" * 5000


def test_clause_heading_detection():
    assert is_clause_heading("Article 6 — Classification rules")
    assert is_clause_heading("Clause 6.1.4 AI system impact assessment")
    assert is_clause_heading("6.1 Actions to address risks")
    assert is_clause_heading("Annex III")
    assert not is_clause_heading("The provider shall ensure that the system is robust.")
    assert not is_clause_heading("2024 was a busy year.")
