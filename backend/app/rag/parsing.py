"""Filing HTML → clean text, split into 10-K/10-Q Item sections."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# Some 8-K primary documents are XML; lxml's HTML parser handles them fine.
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Canonical 10-K item titles, used to label sections when matched.
ITEM_TITLES_10K: dict[str, str] = {
    "1": "Business",
    "1A": "Risk Factors",
    "1B": "Unresolved Staff Comments",
    "1C": "Cybersecurity",
    "2": "Properties",
    "3": "Legal Proceedings",
    "4": "Mine Safety Disclosures",
    "5": "Market for Registrant's Common Equity",
    "6": "Selected Financial Data",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
    "8": "Financial Statements and Supplementary Data",
    "9": "Changes in and Disagreements with Accountants",
    "9A": "Controls and Procedures",
    "9B": "Other Information",
    "10": "Directors, Executive Officers and Corporate Governance",
    "11": "Executive Compensation",
    "12": "Security Ownership",
    "13": "Certain Relationships and Related Transactions",
    "14": "Principal Accountant Fees and Services",
    "15": "Exhibits and Financial Statement Schedules",
}

_ITEM_RE = re.compile(
    r"^\s*item\s+(\d{1,2}[ABC]?)\s*[.:—–-]",  # noqa: RUF001 — filings use en/em dashes
    re.IGNORECASE | re.MULTILINE,
)

_WHITESPACE_RE = re.compile(r"[ \t\x0b\f\r ]+")  # noqa: RUF001 - NBSP is common in EDGAR HTML
_BLANKLINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class FilingSection:
    item: str  # e.g. "1A" or "full"
    title: str  # e.g. "Risk Factors"
    text: str


def html_to_text(html: str) -> str:
    """Strip an EDGAR filing's HTML down to readable plain text."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = _WHITESPACE_RE.sub(" ", text)
    lines = (line.strip() for line in text.split("\n"))
    text = "\n".join(lines)
    return _BLANKLINES_RE.sub("\n\n", text).strip()


def split_sections(text: str, form: str) -> list[FilingSection]:
    """Split filing text into Item sections; fallback: one section.

    - 10-K and 20-F: both use "Item N" structure — split into labelled sections.
    - 10-Q: same Item structure.
    - 6-K, 8-K, and others: treated as a single full-text section.

    Filings start with a table of contents that also says "Item 1." etc., so
    when an item number appears more than once we keep the LAST occurrence
    (the real section body, which is by far the longest run of text).
    """
    _SECTIONED_FORMS = {"10-K", "10-Q", "20-F", "20-F/A"}
    if form not in _SECTIONED_FORMS:
        return [FilingSection(item="full", title=form, text=text)]

    matches = list(_ITEM_RE.finditer(text))
    if len(matches) < 3:
        return [FilingSection(item="full", title=form, text=text)]

    # Keep the last occurrence of each item number (skips the ToC).
    last_pos: dict[str, int] = {}
    for m in matches:
        last_pos[m.group(1).upper()] = m.start()
    boundaries = sorted(last_pos.items(), key=lambda kv: kv[1])

    sections: list[FilingSection] = []
    for i, (item, start) in enumerate(boundaries):
        end = boundaries[i + 1][1] if i + 1 < len(boundaries) else len(text)
        body = text[start:end].strip()
        if len(body) < 200:  # ToC stragglers / empty items
            continue
        title = ITEM_TITLES_10K.get(item, f"Item {item}")
        sections.append(FilingSection(item=item, title=title, text=body))

    return sections or [FilingSection(item="full", title=form, text=text)]


def parse_filing(html: str, form: str) -> list[FilingSection]:
    """Full pipeline: HTML → clean text → labeled sections."""
    return split_sections(html_to_text(html), form)
