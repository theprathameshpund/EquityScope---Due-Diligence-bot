"""Filing HTML parsing and section splitting."""

from __future__ import annotations

from app.rag.parsing import html_to_text, parse_filing, split_sections

MINI_10K = """
<html><head><style>p</style></head><body>
<p>TABLE OF CONTENTS</p>
<p>Item 1. Business ......... 3</p>
<p>Item 1A. Risk Factors ......... 10</p>
<p>Item 7. MD&amp;A ......... 30</p>
<p>Item 1. Business</p>
<p>{business}</p>
<p>Item 1A. Risk Factors</p>
<p>{risks}</p>
<p>Item 7. Management's Discussion and Analysis</p>
<p>{mdna}</p>
</body></html>
""".format(
    business="The company designs consumer electronics. " * 20,
    risks="The business faces intense competition and supply chain risk. " * 20,
    mdna="Net sales increased due to higher services revenue. " * 20,
)


def test_html_to_text_strips_markup() -> None:
    text = html_to_text("<html><body><script>x=1</script><p>Hello <b>world</b></p></body></html>")
    assert "Hello" in text
    assert "world" in text
    assert "x=1" not in text
    assert "<p>" not in text


def test_split_sections_skips_toc() -> None:
    sections = parse_filing(MINI_10K, "10-K")
    items = {s.item for s in sections}
    assert {"1", "1A", "7"} <= items
    risk = next(s for s in sections if s.item == "1A")
    assert risk.title == "Risk Factors"
    assert "intense competition" in risk.text
    # The ToC line must not have become the section body.
    assert len(risk.text) > 500


def test_non_10k_form_single_section() -> None:
    sections = split_sections("Material definitive agreement entered.", "8-K")
    assert len(sections) == 1
    assert sections[0].item == "full"
