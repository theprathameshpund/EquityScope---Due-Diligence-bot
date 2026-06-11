"""SEC EDGAR access: CIK resolution, filing index, downloads.

Respects SEC fair-access rules: a contact User-Agent header from
EDGAR_USER_AGENT, at most 10 requests/second (token-spaced rate limiter),
and an on-disk cache under data/filings/ so nothing is fetched twice.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.config import settings
from src.logging_setup import get_logger

log = get_logger(__name__)

COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:0>10}.json"
ARCHIVE_DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_nodash}/{doc}"


class RateLimiter:
    """Spaces requests so we never exceed N per second (thread-safe)."""

    def __init__(self, max_per_second: int) -> None:
        self._interval = 1.0 / max_per_second
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = max(self._next_allowed, now) + self._interval


@dataclass(frozen=True)
class CompanyIdentity:
    cik: str  # zero-stripped numeric string
    ticker: str
    name: str


@dataclass(frozen=True)
class FilingMeta:
    accession: str  # with dashes, e.g. 0000320193-24-000123
    form: str
    filing_date: date
    report_date: date | None
    primary_doc: str
    cik: str

    @property
    def accession_nodash(self) -> str:
        return self.accession.replace("-", "")

    @property
    def source_url(self) -> str:
        return ARCHIVE_DOC_URL.format(
            cik=int(self.cik), accession_nodash=self.accession_nodash, doc=self.primary_doc
        )


class EdgarClient:
    """Rate-limited, disk-cached SEC EDGAR client."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or settings.filings_cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._limiter = RateLimiter(settings.edgar_max_requests_per_second)
        self._client = httpx.Client(
            headers={
                "User-Agent": settings.edgar_user_agent.strip('"'),
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    # ── HTTP with rate limit + cache ───────────────────────────

    def _get(self, url: str) -> httpx.Response:
        self._limiter.acquire()
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp

    def _cached_text(self, url: str, cache_path: Path) -> str:
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8", errors="replace")
        text = self._get(url).text
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8", errors="replace")
        log.info("edgar_downloaded", url=url, cached_to=str(cache_path))
        return text

    # ── CIK resolution ─────────────────────────────────────────

    def resolve_company(self, query: str) -> CompanyIdentity:
        """Resolve a ticker or company name to its SEC CIK.

        Exact ticker match wins; otherwise the first case-insensitive
        substring match on the registrant name.
        """
        mapping_path = self._cache_dir / "company_tickers.json"
        raw = self._cached_text(COMPANY_TICKERS_URL, mapping_path)
        data: dict[str, dict[str, Any]] = json.loads(raw)

        q = query.strip().upper()
        by_ticker: CompanyIdentity | None = None
        by_name: CompanyIdentity | None = None
        for entry in data.values():
            ticker = str(entry["ticker"]).upper()
            title = str(entry["title"])
            cik = str(int(entry["cik_str"]))
            if ticker == q:
                by_ticker = CompanyIdentity(cik=cik, ticker=ticker, name=title)
                break
            if by_name is None and q in title.upper():
                by_name = CompanyIdentity(cik=cik, ticker=ticker, name=title)

        identity = by_ticker or by_name
        if identity is None:
            raise ValueError(
                f"Could not resolve {query!r} to an SEC-registered company. "
                "Try the exact ticker symbol (e.g. NVDA) or the registrant name."
            )
        log.info("company_resolved", query=query, cik=identity.cik, ticker=identity.ticker)
        return identity

    # ── Filing index ───────────────────────────────────────────

    def get_submissions(self, cik: str) -> dict[str, Any]:
        url = SUBMISSIONS_URL.format(cik=cik)
        # Submissions move daily; cache for the current day only.
        cache_path = self._cache_dir / f"submissions_{cik}_{date.today().isoformat()}.json"
        raw = self._cached_text(url, cache_path)
        result: dict[str, Any] = json.loads(raw)
        return result

    def list_target_filings(self, cik: str) -> list[FilingMeta]:
        """Latest 10-K, last N 10-Qs, and 8-Ks from the lookback window."""
        subs = self.get_submissions(cik)
        recent = subs["filings"]["recent"]
        rows = zip(
            recent["accessionNumber"],
            recent["form"],
            recent["filingDate"],
            recent["reportDate"],
            recent["primaryDocument"],
            strict=True,
        )
        all_filings: list[FilingMeta] = []
        for accession, form, filing_dt, report_dt, primary_doc in rows:
            if not primary_doc:
                continue
            all_filings.append(
                FilingMeta(
                    accession=str(accession),
                    form=str(form),
                    filing_date=date.fromisoformat(str(filing_dt)),
                    report_date=date.fromisoformat(str(report_dt)) if report_dt else None,
                    primary_doc=str(primary_doc),
                    cik=cik,
                )
            )

        all_filings.sort(key=lambda f: f.filing_date, reverse=True)
        ten_ks = [f for f in all_filings if f.form == "10-K"][:1]
        ten_qs = [f for f in all_filings if f.form == "10-Q"][: settings.filings_num_10q]
        cutoff = date.today() - timedelta(days=30 * settings.filings_lookback_8k_months)
        eight_ks = [f for f in all_filings if f.form == "8-K" and f.filing_date >= cutoff]

        selected = ten_ks + ten_qs + eight_ks
        log.info(
            "filings_selected",
            cik=cik,
            n_10k=len(ten_ks),
            n_10q=len(ten_qs),
            n_8k=len(eight_ks),
        )
        return selected

    # ── Document + XBRL downloads ──────────────────────────────

    def download_filing(self, ticker: str, filing: FilingMeta) -> Path:
        """Download a filing's primary document into the disk cache."""
        safe_doc = re.sub(r"[^A-Za-z0-9._-]", "_", filing.primary_doc)
        path = self._cache_dir / ticker.upper() / f"{filing.accession_nodash}_{safe_doc}"
        self._cached_text(filing.source_url, path)
        return path

    def get_companyfacts(self, cik: str) -> dict[str, Any]:
        """EDGAR XBRL companyfacts JSON (cached for the current day)."""
        url = COMPANYFACTS_URL.format(cik=cik)
        cache_path = self._cache_dir / f"companyfacts_{cik}_{date.today().isoformat()}.json"
        raw = self._cached_text(url, cache_path)
        result: dict[str, Any] = json.loads(raw)
        return result

    def close(self) -> None:
        self._client.close()
