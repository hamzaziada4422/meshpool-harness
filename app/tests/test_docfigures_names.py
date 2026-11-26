"""Release review item 3: finding the document a figure question names is linear in the message, and still finds
"report.pdf" in "the chart in report.pdf" and names with spaces."""
import time

from meshpool_harness import docfigures as DF


def test_a_long_message_that_names_a_chart_is_searched_quickly(tmp_path):
    for n in (20_000, 80_000):
        q = "chart " + ("word (a) b-c " * (n // 12))[:n]
        t0 = time.perf_counter()
        assert DF.docs_in_chat(tmp_path, [], q) == []
        assert time.perf_counter() - t0 < 0.2, n


def test_the_document_a_question_names_is_still_found(tmp_path):
    (tmp_path / "report.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "Annual Report 2024.pdf").write_bytes(b"%PDF-1.4\n")
    (tmp_path / "deck.pptx").write_bytes(b"x")
    got = DF.docs_in_chat(tmp_path, [], "What does the barriers chart in report.pdf show?")
    assert [p.name for p in got] == ["report.pdf"]
    got = DF.docs_in_chat(tmp_path, [], "Explain figure 2 of Annual Report 2024.pdf please")
    assert [p.name for p in got] == ["Annual Report 2024.pdf"]
    got = DF.docs_in_chat(tmp_path, [], "Compare the chart in report.pdf with the one in deck.pptx")
    assert [p.name for p in got] == ["report.pdf", "deck.pptx"]
    assert DF.docs_in_chat(tmp_path, [], "the chart in missing.pdf") == []
