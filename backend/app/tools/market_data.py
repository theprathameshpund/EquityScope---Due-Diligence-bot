"""yfinance wrapper — market snapshot and peer multiples. No LLM here."""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.config import settings
from app.logging_setup import get_logger
from app.state import MarketSnapshot, PeerMultiple

log = get_logger(__name__)

FRED_SERIES = {
    "CPIAUCSL": "CPI (index)",
    "FEDFUNDS": "Fed funds rate (%)",
    "DGS10": "10Y Treasury yield (%)",
}
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _info_float(info: dict[str, Any], key: str) -> float | None:
    value = info.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def get_snapshot(ticker: str) -> MarketSnapshot:
    """1y price summary + valuation multiples for one ticker."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    info = cast("dict[str, Any]", t.info or {})
    history = t.history(period="1y")

    change_1y: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    price: float | None = None
    if history is not None and len(history) > 1:
        closes = history["Close"]
        first, last = float(closes.iloc[0]), float(closes.iloc[-1])
        price = last
        if first != 0:
            change_1y = (last / first - 1.0) * 100.0
        high_52w = float(closes.max())
        low_52w = float(closes.min())

    return MarketSnapshot(
        available=True,
        ticker=ticker.upper(),
        price=price if price is not None else _info_float(info, "currentPrice"),
        change_1y_pct=round(change_1y, 2) if change_1y is not None else None,
        high_52w=high_52w,
        low_52w=low_52w,
        market_cap=_info_float(info, "marketCap"),
        pe_ttm=_info_float(info, "trailingPE"),
        forward_pe=_info_float(info, "forwardPE"),
        price_to_sales=_info_float(info, "priceToSalesTrailing12Months"),
        ev_to_ebitda=_info_float(info, "enterpriseToEbitda"),
    )


def get_peer_multiples(tickers: list[str]) -> list[PeerMultiple]:
    """Valuation multiples for a list of peer tickers (best effort each)."""
    import yfinance as yf

    peers: list[PeerMultiple] = []
    for symbol in tickers[:3]:
        try:
            info = cast("dict[str, Any]", yf.Ticker(symbol).info or {})
            peers.append(
                PeerMultiple(
                    ticker=symbol.upper(),
                    pe_ttm=_info_float(info, "trailingPE"),
                    price_to_sales=_info_float(info, "priceToSalesTrailing12Months"),
                    ev_to_ebitda=_info_float(info, "enterpriseToEbitda"),
                )
            )
        except Exception as exc:
            log.warning("peer_fetch_failed", ticker=symbol, error=str(exc))
    return peers


def get_macro_notes() -> list[str]:
    """Latest FRED macro readings; empty when no API key is configured."""
    if not settings.fred_api_key:
        return []
    notes: list[str] = []
    with httpx.Client(timeout=15.0) as client:
        for series_id, label in FRED_SERIES.items():
            try:
                resp = client.get(
                    FRED_URL,
                    params={
                        "series_id": series_id,
                        "api_key": settings.fred_api_key,
                        "file_type": "json",
                        "sort_order": "desc",
                        "limit": 1,
                    },
                )
                resp.raise_for_status()
                observations = resp.json().get("observations", [])
                if observations:
                    obs = observations[0]
                    notes.append(f"{label}: {obs['value']} (as of {obs['date']})")
            except Exception as exc:
                log.warning("fred_fetch_failed", series=series_id, error=str(exc))
    return notes
