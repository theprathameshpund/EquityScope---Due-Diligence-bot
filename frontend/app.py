"""EquityScope Streamlit frontend.

Input form → kick off a run → live agent timeline via SSE → final report
with expandable citations and download buttons.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import streamlit as st


def _api_base() -> str:
    """API base URL: env var, else API_PORT from env or .env, else :8000."""
    if os.environ.get("API_BASE_URL"):
        return os.environ["API_BASE_URL"]
    port = os.environ.get("API_PORT", "")
    if not port and Path(".env").exists():
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("API_PORT="):
                port = line.split("=", 1)[1].split("#")[0].strip()
    return f"http://localhost:{port or '8000'}"


API_BASE = _api_base()

NODE_LABELS = {
    "run": "Run",
    "ingest_check": "📥 Ingest & index filings",
    "filings": "📄 Filings research (RAG)",
    "market": "📈 Market data",
    "news": "📰 News scan",
    "analyst": "🧮 Financial analysis (XBRL)",
    "writer": "✍️ Report writer",
    "critic": "🔍 Critic verification",
    "finalize": "✅ Finalize",
}

st.set_page_config(page_title="EquityScope", page_icon="🔬", layout="wide")
st.title("🔬 EquityScope — Due Diligence Copilot")
st.caption("Multi-agent research over SEC filings, market data, and news — every claim cited and verified.")


def start_run(company: str, focus: str) -> str:
    resp = httpx.post(
        f"{API_BASE}/api/reports", json={"company": company, "focus": focus}, timeout=30
    )
    resp.raise_for_status()
    return str(resp.json()["run_id"])


def stream_events(run_id: str, timeline: Any, meter: Any) -> None:
    """Follow the SSE stream, updating the timeline until the run ends."""
    statuses: dict[str, str] = {}
    with httpx.stream(
        "GET", f"{API_BASE}/api/reports/{run_id}/events", timeout=None
    ) as response:
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            node = event.get("node", "?")
            status = event.get("status", "?")
            statuses[node] = status

            rows = []
            for name, state_ in statuses.items():
                label = NODE_LABELS.get(name, name)
                icon = {"start": "🔄", "end": "✅", "error": "❌",
                        "done": "🏁", "warning": "⚠️"}.get(state_, "•")
                rows.append(f"{icon} **{label}** — {state_}")
                if event.get("message") and name == node:
                    rows.append(f"&nbsp;&nbsp;&nbsp;_{event['message']}_")
            timeline.markdown("\n\n".join(rows))
            meter.metric(
                "Tokens / Cost",
                f"{event.get('tokens_used', 0):,}",
                f"${event.get('cost_usd', 0.0):.4f}",
                delta_color="off",
            )
            if node == "run" and status in {"done", "error"}:
                return


def fetch_report(run_id: str) -> dict[str, Any]:
    resp = httpx.get(f"{API_BASE}/api/reports/{run_id}", timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def render_claims(claims: list[dict[str, Any]], chunks: dict[str, dict[str, Any]]) -> None:
    for claim in claims:
        st.markdown(f"- {claim['text']}")
        refs = claim.get("citation_chunk_ids", []) + claim.get("metric_ids", [])
        if refs:
            with st.expander(f"Sources ({len(refs)})"):
                for cid in claim.get("citation_chunk_ids", []):
                    chunk = chunks.get(cid)
                    if chunk:
                        st.markdown(
                            f"**{chunk.get('form_type', '?')} — {chunk.get('section', '?')}** "
                            f"([EDGAR]({chunk.get('source_url', '#')}))"
                        )
                        st.text(chunk.get("text", "")[:1200])
                    else:
                        st.markdown(f"`{cid}` (chunk text not embedded in report)")
                for mid in claim.get("metric_ids", []):
                    st.markdown(f"Computed metric: `{mid}`")


def render_report(data: dict[str, Any]) -> None:
    report = data.get("report") or {}
    markdown = data.get("markdown", "")
    meta = report.get("metadata", {})

    cols = st.columns(4)
    cols[0].metric("Tokens", f"{meta.get('tokens_used', 0):,}")
    cols[1].metric("Cost", f"${meta.get('cost_usd', 0.0):.4f}")
    cols[2].metric("Duration", f"{meta.get('duration_s', 0.0):.0f}s")
    cols[3].metric("Claims", str(_count_claims(report)))

    chunks: dict[str, dict[str, Any]] = {
        str(e.get("chunk_id")): e for e in (data.get("evidence") or [])
    }

    tab_report, tab_raw = st.tabs(["Report", "Raw JSON"])
    with tab_report:
        company = report.get("company", {})
        st.header(f"{company.get('name', '?')} ({company.get('ticker', '?')})")

        st.subheader("Executive Summary")
        render_claims(report.get("executive_summary", []), chunks)
        st.subheader("Business Overview")
        render_claims(report.get("business_overview", []), chunks)

        st.subheader("Financial Health")
        table = report.get("financial_health", {}).get("table", {}).get("metrics", [])
        if table:
            st.dataframe(
                [
                    {"Metric": m["name"], "Value": m["value"], "Unit": m["unit"],
                     "Period": m["period"]}
                    for m in table
                ],
                use_container_width=True,
            )
        render_claims(report.get("financial_health", {}).get("commentary", []), chunks)

        st.subheader("Risk Matrix")
        for risk in report.get("risk_matrix", []):
            st.markdown(
                f"**{risk['title']}** — severity: `{risk['severity']}`, "
                f"likelihood: `{risk['likelihood']}`"
            )
            render_claims(risk.get("claims", []), chunks)

        st.subheader("Recent Developments")
        render_claims(report.get("recent_developments", []), chunks)
        st.subheader("Red Flags")
        render_claims(report.get("red_flags", []), chunks)

        gaps = report.get("data_gaps", [])
        if gaps:
            st.subheader("Data Gaps")
            for gap in gaps:
                st.warning(gap)

    with tab_raw:
        st.json(report)

    st.download_button(
        "⬇️ Download Markdown", markdown or "", file_name="equityscope_report.md"
    )
    st.download_button(
        "⬇️ Download JSON",
        json.dumps(report, indent=2),
        file_name="equityscope_report.json",
    )


def _count_claims(report: dict[str, Any]) -> int:
    n = 0
    for key in ("executive_summary", "business_overview", "recent_developments", "red_flags"):
        n += len(report.get(key, []))
    n += len(report.get("financial_health", {}).get("commentary", []))
    for risk in report.get("risk_matrix", []):
        n += len(risk.get("claims", []))
    return n


# ── UI ─────────────────────────────────────────────────────────

# Allow opening a past run directly: http://localhost:8501/?run=<run_id>
if "run" in st.query_params and "run_id" not in st.session_state:
    st.session_state["run_id"] = st.query_params["run"]

with st.form("run_form"):
    col1, col2 = st.columns([1, 2])
    company = col1.text_input("Company or ticker", placeholder="NVDA")
    focus = col2.text_input("Focus (optional)", placeholder="supply-chain risk")
    submitted = st.form_submit_button("Run due diligence", type="primary")

if submitted and company.strip():
    try:
        run_id = start_run(company.strip(), focus.strip())
    except httpx.HTTPStatusError as exc:
        st.error(f"Failed to start run: {exc.response.status_code} {exc.response.text}")
        st.stop()
    st.session_state["run_id"] = run_id
    st.info(f"Run started: `{run_id}`")

    col_timeline, col_meter = st.columns([3, 1])
    timeline = col_timeline.empty()
    meter = col_meter.empty()
    with st.spinner("Agents at work…"):
        try:
            stream_events(run_id, timeline, meter)
        except httpx.HTTPError as exc:
            st.warning(f"Event stream interrupted ({exc}); polling for the result…")

run_id = st.session_state.get("run_id")
if run_id:
    data = fetch_report(str(run_id))
    status = data.get("status")
    if status == "done":
        render_report(data)
    elif status == "failed":
        st.error(f"Run failed: {data.get('error', 'unknown error')}")
    elif not submitted:
        st.info(f"Run `{run_id}` is {status}… refresh to update.")
