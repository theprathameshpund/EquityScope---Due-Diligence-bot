"""yfinance wrapper — market snapshot, peer multiples, analyst consensus,
short interest, insider activity, and optional FRED macro data. No LLM here."""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.config import settings
from app.logging_setup import get_logger
from app.state import (
    InsiderActivity,
    InsiderTransaction,
    MarketSnapshot,
    PeerMultiple,
)

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


def _info_int(info: dict[str, Any], key: str) -> int:
    value = info.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    return 0


def get_snapshot(ticker: str) -> MarketSnapshot:
    """Full market snapshot: price, valuation, analyst consensus, short interest."""
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

    recommendation = str(info.get("recommendationKey") or "").lower()
    officers: list[dict[str, str | int | float | None]] = []
    for officer in cast("list[dict[str, Any]]", info.get("companyOfficers") or [])[:8]:
        officers.append(
            {
                "name": str(officer.get("name") or ""),
                "title": str(officer.get("title") or ""),
                "age": _info_int(officer, "age") or None,
                "total_pay": _info_float(officer, "totalPay"),
                "year_born": _info_int(officer, "yearBorn") or None,
            }
        )

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
        price_to_book=_info_float(info, "priceToBook"),
        beta=_info_float(info, "beta"),
        sector=str(info.get("sector") or ""),
        industry=str(info.get("industry") or ""),
        officers=officers,
        # Analyst consensus
        recommendation=recommendation,
        recommendation_mean=_info_float(info, "recommendationMean"),
        target_mean=_info_float(info, "targetMeanPrice"),
        target_high=_info_float(info, "targetHighPrice"),
        target_low=_info_float(info, "targetLowPrice"),
        num_analysts=_info_int(info, "numberOfAnalystOpinions"),
        # Short interest
        short_percent_float=_info_float(info, "shortPercentOfFloat"),
        short_ratio=_info_float(info, "shortRatio"),
        # Dividends
        dividend_yield=_info_float(info, "dividendYield"),
        payout_ratio=_info_float(info, "payoutRatio"),
    )


def get_peer_multiples(tickers: list[str]) -> list[PeerMultiple]:
    """Valuation multiples for a list of peer tickers (best effort each)."""
    import yfinance as yf

    peers: list[PeerMultiple] = []
    for symbol in tickers[:5]:
        try:
            info = cast("dict[str, Any]", yf.Ticker(symbol).info or {})
            peers.append(
                PeerMultiple(
                    ticker=symbol.upper(),
                    pe_ttm=_info_float(info, "trailingPE"),
                    price_to_sales=_info_float(info, "priceToSalesTrailing12Months"),
                    ev_to_ebitda=_info_float(info, "enterpriseToEbitda"),
                    price_to_book=_info_float(info, "priceToBook"),
                    sector=str(info.get("sector") or ""),
                )
            )
        except Exception as exc:
            log.warning("peer_fetch_failed", ticker=symbol, error=str(exc))
    return peers


def get_insider_activity(ticker: str) -> InsiderActivity:
    """Fetch recent insider transactions via yfinance (SEC Form 4 data)."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        df = t.insider_transactions
        if df is None or df.empty:
            return InsiderActivity(available=False, error="No insider transaction data available")

        transactions: list[InsiderTransaction] = []
        net_shares = 0.0
        net_value = 0.0

        for _, row in df.iterrows():
            try:
                txn_text = str(row.get("Transaction") or row.get("Text") or "")
                is_purchase = any(k in txn_text.lower() for k in ("purchase", "buy", "acqui"))
                is_sale = any(k in txn_text.lower() for k in ("sale", "sell", "sold"))
                if not is_purchase and not is_sale:
                    continue
                txn_type = "Purchase" if is_purchase else "Sale"

                shares = float(row.get("Shares") or 0)
                value = None
                raw_val = row.get("Value")
                if raw_val is not None:
                    try:
                        value = float(raw_val)
                    except (TypeError, ValueError):
                        pass

                date_raw = row.get("Start Date") or row.get("Date") or ""
                date_str = str(date_raw)[:10] if date_raw else ""

                insider = str(row.get("Insider") or row.get("Name") or "Unknown")
                position = str(row.get("Position") or row.get("Title") or "")

                txn = InsiderTransaction(
                    name=insider,
                    title=position,
                    transaction_type=txn_type,
                    shares=abs(shares),
                    value=abs(value) if value is not None else None,
                    date=date_str,
                )
                transactions.append(txn)

                signed_shares = abs(shares) if is_purchase else -abs(shares)
                net_shares += signed_shares
                if value is not None:
                    net_value += abs(value) if is_purchase else -abs(value)
            except Exception as row_exc:
                log.debug("insider_row_skip", error=str(row_exc))
                continue

        if not transactions:
            return InsiderActivity(available=False, error="No buy/sell insider transactions found")

        if net_shares > 0:
            sentiment = "bullish"
        elif net_shares < 0:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        log.info(
            "insider_activity_fetched",
            ticker=ticker,
            transactions=len(transactions),
            net_shares=net_shares,
            sentiment=sentiment,
        )
        return InsiderActivity(
            available=True,
            transactions=transactions[:20],  # cap display
            net_shares=net_shares,
            net_value=net_value,
            sentiment=sentiment,
        )
    except Exception as exc:
        log.warning("insider_activity_failed", ticker=ticker, error=str(exc))
        return InsiderActivity(available=False, error=str(exc))


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
