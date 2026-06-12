# EquityScope DD Platform — Product Design Specification

**Version 1.0 · Full Due Diligence Application UI**
*Audience: analysts, investors, auditors, consultants. Positioning: a high-end investment-firm tool — McKinsey rigor, Stripe polish, Notion calm.*

---

## 0 · Design Philosophy

Four laws govern every screen:

1. **The document is the product.** Everything funnels into one artifact — the diligence report. The UI is a factory for that artifact; the report view is the showroom.
2. **Evidence or it doesn't exist.** Every number, claim, and risk carries provenance one click away. Trust is the entire value proposition; the UI must *perform* trustworthiness.
3. **Progressive disclosure, always.** Analysts need density; partners need a headline. Every module renders at two altitudes — Summary and Detail — toggled, never duplicated.
4. **Calm surfaces, loud exceptions.** The interface stays neutral so red flags, anomalies, and overdue items are the only things that shout.

---

## 1 · Information Architecture

### 1.1 Object model

```
Organization
└── Project (one target company per project)
    ├── Workstreams: Financial · Legal · Compliance · Operations · ESG · Risk
    │   └── Sections → Findings (atomic, citable units)
    ├── Data Room (documents, versions, AI summaries)
    ├── Risk Register (scored, tagged, linked to findings)
    ├── Checklists (per workstream, templated)
    ├── Activity & Approvals
    └── Report (generated from findings; Summary/Detailed; exportable)
```

**The atomic unit is the Finding**: a verified statement with severity, citations (document + page/chunk), owner, status, and bookmarks. Dashboards aggregate findings; reports narrate them; risks weight them. One model, many lenses.

### 1.2 Navigation model

- **Global rail** (far left, 56 px, icons): Home · Projects · Data Room · Reports · Settings.
- **Project sidebar** (240 px, collapsible to icons): the six workstreams + Data Room + Risk Register + Report. Per-item progress ring and flag count.
- **Top header**: breadcrumb (`Projects / Helios Corp / Financials`), global search (⌘K), AI assist toggle, avatars of present collaborators, share button.
- **Right context drawer** (on demand, 360 px): comments, AI summaries, document preview, version history — never a navigation level, always context for the center pane.

Three-pane rhythm: **navigate left, work center, contextualize right.**

---

## 2 · Visual Design System

### 2.1 Color — "Banker's Daylight" (light, default)

| Token | Value | Use |
|---|---|---|
| `--paper` | `#FAFAF7` | App background (warm off-white) |
| `--surface` | `#FFFFFF` | Cards, panels |
| `--surface-2` | `#F3F2EE` | Wells, table headers, hover |
| `--ink` | `#1A1D23` | Primary text (charcoal, not black) |
| `--ink-2` | `#5A6172` | Secondary text |
| `--ink-3` | `#9AA1B0` | Hints, metadata |
| `--line` | `#E8E6E0` | Hairline dividers (no harsh borders) |
| `--brand` | `#1E3A5F` | Deep slate-blue: primary actions, active nav |
| `--brand-soft` | `#EAF0F7` | Selected states, tints |
| `--gold` | `#B08A3E` | Premium accent: key insights, bookmarks, exec-summary rules |
| `--green` | `#2E7D5B` | Positive deltas, complete, low risk |
| `--amber` | `#C77F1F` | Medium risk, pending |
| `--red` | `#B3392E` | High risk, red flags (deep brick, not alarm-red) |

Dark mode flips paper→`#12141A`, surface→`#1A1E27`, keeps the same hue logic with raised luminance accents. Charts use a fixed 6-color categorical ramp derived from brand+gold so exports match the screen.

**Rule:** semantic colors are reserved. Nothing decorative may be red, amber, or gold.

### 2.2 Typography — editorial, two faces

| Role | Face | Sizes |
|---|---|---|
| Display / report headings | **Tiempos Headline** (serif; fallback: Source Serif 4, Georgia) | 40/32/24 |
| UI & body | **Inter** | 15 body · 13 secondary · 11 caps-label (tracked +6%) |
| Numbers & code | **Söhne Mono** (fallback: JetBrains Mono) | tabular-nums everywhere data lives |

Serif appears **only** in the report and executive summary — the moment content becomes "the document," typography shifts to editorial. That contrast is the brand.

Body line-height 1.6; report measure capped at 68 ch; section numbers (01, 02…) in mono gold.

### 2.3 Space, shape, elevation

- 8-pt grid; card padding 24; section gaps 32; page gutters 40.
- Radii: 10 (cards) · 8 (inputs) · 999 (chips). Nothing sharp, nothing bubbly.
- Elevation by **shadow, never border**: `0 1px 2px rgba(26,29,35,.05)` resting → `0 8px 24px rgba(26,29,35,.10)` raised. Hairlines (`--line`) only *inside* components (table rows, dividers).
- 12-column grid, 1440 design width, content max 1280.

### 2.4 Core components

| Component | Spec |
|---|---|
| **Sidebar item** | Icon + label + progress ring (donut, 14 px) + flag count badge. Active: brand-soft fill, 3 px brand left rail. |
| **Breadcrumb header** | 56 px, sticky, paper with 80% blur. Breadcrumbs in ink-2; current page ink. Right cluster: ⌘K search pill, AI spark icon, presence avatars (stacked −8 px), gold Share button. |
| **Data table** | 44 px rows, surface-2 sticky header in caps-label style, hairline rows only. Sort on header click; filter chips above; column pinning; virtualized beyond 200 rows. Numeric cells right-aligned mono. Row hover reveals action icons (bookmark, comment, open). |
| **Risk badge** | Chip with dot: ● High `--red` / ● Medium `--amber` / ● Low `--green`, surface-2 ground, never filled solid (except in Red Flag callouts). |
| **Status chip** | Complete ✓ green-tint / Pending ◐ amber-tint / Flagged ⚑ red-tint / In review ○ brand-tint. |
| **KPI card** | Caps-label title, 28 px mono value, delta arrow + % colored semantically, 32 px sparkline footer. |
| **Callout** | Insight: gold 3 px left rule + gold ✦. Red Flag: red rule, red-tint wash, serif heading — the loudest element in the system. |
| **Modal / Drawer** | Modal for destructive/committal only; everything else is a right drawer (460 px, paper, spring-in 280 ms). Drawers stack max 2, with breadcrumb back. |
| **Timeline / Activity** | Vertical hairline with event dots colored by type (edit ink-2, comment brand, approval green, flag red). Relative timestamps; hover reveals absolute. |
| **AI Assist panel** | Right drawer with spark header; output rendered as draft Findings with "insert with citation" buttons — AI never writes directly into the record. |

---

## 3 · Screen-by-Screen Breakdown

### 3.1 Dashboard (Home)

**Purpose:** Monday-morning answer to "where is every deal, and what's burning?"

```
┌─ rail ─┬───────────────────────────────────────────────────────────────┐
│        │  Good morning, Priya            ⌘K Search        [+ New project]│
│  HOME  │                                                                 │
│        │  ┌─ PORTFOLIO PULSE ────────────────────────────────────────┐  │
│        │  │  4 Active · 2 In review · 12 Red flags · 3 due this week │  │
│        │  └──────────────────────────────────────────────────────────┘  │
│        │  ┌── Project card ─────────┐ ┌── Project card ─────────┐       │
│        │  │ HELIOS CORP   ● High    │ │ NORDWIND AG   ● Low     │       │
│        │  │ Acquisition · Tech      │ │ Minority stake · Energy │       │
│        │  │ ████████░░ 78%          │ │ ███░░░░░░░ 31%          │       │
│        │  │ ⚑ 5 flags  ✓ 41/52     │ │ ⚑ 0 flags  ✓ 12/40     │       │
│        │  │ EV $2.4B · DSCR 1.8x    │ │ EV €480M · DSCR 2.4x    │       │
│        │  │ ◔ Report due Fri        │ │ ◔ Kickoff Mon           │       │
│        │  └─────────────────────────┘ └─────────────────────────┘       │
│        │  ┌─ NEEDS ATTENTION ────────────┐ ┌─ ACTIVITY ─────────────┐   │
│        │  │ ⚑ Helios: undisclosed lit…   │ │ ● Sam approved Legal §3 │   │
│        │  │ ⚑ Vega: covenant breach FY24 │ │ ● AI summarized 12 docs │   │
│        │  │ ◐ Nordwind: 8 docs unreviewed│ │ ● Mia flagged ESG §2    │   │
│        │  └──────────────────────────────┘ └────────────────────────┘   │
└────────┴────────────────────────────────────────────────────────────────┘
```

- **Project cards**: company name (serif), deal type, overall risk badge, progress bar (checklist completion), flag count, two headline KPIs, next milestone. Click → workspace. Hover lifts 2 px.
- **Needs attention** is a triaged queue (red flags first, then overdue, then unreviewed volume), not a raw feed.
- KPI strip across the top of each card pulls from the financial module — valuation, revenue, liabilities, compliance score — so partners never open a project just to ask.

### 3.2 Project Workspace

**Purpose:** the analyst's home for one target; 80% of time lives here.

```
┌─ rail ─┬─ project sidebar ──┬────── content ──────────────┬─ drawer ──┐
│        │ HELIOS CORP        │ Financials ▸ Quality of      │ 💬 (4)    │
│        │ ● High · 78%       │ Earnings                     │ ──────    │
│        │                    │                              │ M. Chen   │
│        │ ◉ Overview         │ ▾ 01 Revenue recognition     │ "Check    │
│        │ ◔ Financials  ⚑2   │   [finding] [finding]        │  note 14" │
│        │ ◔ Legal       ⚑3   │ ▾ 02 EBITDA adjustments      │           │
│        │ ○ Compliance       │   ┌─ ✦ KEY INSIGHT ────────┐ │ @reply…   │
│        │ ◔ Operations       │   │ $4.2M of add-backs are  │ │           │
│        │ ○ ESG              │   │ non-recurring only by   │ │           │
│        │ ◉ Risk register    │   │ management assertion. ⌲ │ │           │
│        │ ─────────────      │   └─────────────────────────┘ │           │
│        │ 🗂 Data room (214) │ ▸ 03 Working capital         │           │
│        │ 📄 Report          │ ▸ 04 Debt & commitments      │           │
└────────┴────────────────────┴──────────────────────────────┴───────────┘
```

- Sections are **collapsible numbered subsections**; each holds Finding cards (statement → severity dot → citation chips → owner avatar → bookmark star).
- **Inline annotation**: select any text → floating mini-toolbar (Comment · Flag · Bookmark · Ask AI). Comments anchor to the selection and live in the right drawer with mentions (`@sam`) and resolve states.
- Top of content pane: workstream progress, "Summary | Detailed" altitude toggle, and a `⌲ trace` icon on every number that opens its source document at the exact page.

### 3.3 Data Room

- **Three-pane**: folder/category tree (auto-categorized: Corporate, Financial, Contracts, HR, IP, Litigation…) → virtualized file table (name, type icon, version, status chip, reviewer, AI-summary indicator ✦) → preview pane.
- **Preview**: PDF and Excel render inline with page thumbnails; AI summary panel sits above the preview as a gold-ruled card: three-bullet digest, detected entities (parties, dates, amounts), and red-phrase detection ("termination for convenience", "change of control") — each phrase deep-links into the page.
- **Versioning**: stacked version chips (v3 ▾); diff view for re-uploaded files highlights changed pages; every Finding citation pins to a *version*, so report links never rot.
- Upload is a full-pane drop zone; on drop, files animate into the tree as categorization resolves (skeleton chip → category chip).

### 3.4 Financial Analysis

- **Chart canvas** (revenue, EBITDA, FCF, margins) — area/line charts on hairline grid, no chart junk; hover scrubber shows a mono tooltip column with all series values. Period selector (1Y/3Y/5Y/Custom) as segmented control.
- **Ratio board**: KPI cards grouped Growth · Margins · Leverage · Liquidity · Returns; each card flips (180 ms) to reveal formula and the exact source values — provenance theater.
- **Forecast & scenarios**: assumption sliders (growth %, margin bps, capex %) in a left rail; chart re-renders base vs scenario as solid vs dashed with a shaded delta band; sensitivity table updates live. Scenarios save as named chips (Bear / Base / Bull) comparable side-by-side.
- Every computed figure carries the ⌲ trace affordance to its XBRL/source cell.

### 3.5 Risk Assessment

- **Matrix**: 5×5 severity × probability grid; cells tint from green→amber→red wash; risks render as draggable dots (drag = rescore, logged to activity). Click a cell → drawer lists its risks.
- **Register table**: sortable by score (severity × probability × confidence weight, shown as a 0–25 mono score with a small radial), tag chips (Legal · Financial · Operational · ESG), linked findings count, owner, mitigation status.
- **Scoring drawer**: two steppers (severity, probability), confidence slider, auto-computed score with the formula visible — auditors must see the math.

### 3.6 Compliance & Legal Review

- **Checklist UI**: templated trees (e.g., "Reg-readiness", "AML/KYC") with tri-state rows — ✓ Complete (green), ◐ Pending (amber), ⚑ Flagged (red). Section headers roll up child progress.
- Each row expands inline: notes, evidence attachments, responsible reviewer, due date.
- **Flag → Issue** promotion: flagged items become tracked issues with status lane (Open → In review → Resolved → Accepted-risk), shown as a compact board *and* filterable table. Issues link back to checklist origin and forward into the report's red-flag section automatically.

### 3.7 Collaboration & Approvals

- Comments/mentions anchored anywhere (text, table row, chart point, document page) — one unified comment model, all surfaced in the project drawer with filters (Open / Resolved / Mine).
- **Approval workflow**: each workstream has a sign-off bar — reviewer avatars with state rings (grey pending, brand in-review, green approved). Report export is gated: every workstream approved or explicitly waived (waiver text appears in the report appendix — honesty by design).
- Activity log is filterable by type and exportable (audit trail requirement).

---

## 4 · The Report — flagship surface

**Not a PDF. A digital editorial document** ("the living memo") that happens to export beautifully.

### 4.1 Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│  ☰ Contents      HELIOS CORP — Due Diligence     [Summary ◉ Detailed]│
│                                                   [Export ▾] [Share] │
├────────────┬─────────────────────────────────────────────────────────┤
│ CONTENTS   │   PROJECT HELIOS                          CONFIDENTIAL  │
│ 01 Exec    │                                                         │
│    summary │   Due Diligence Report                                  │
│ 02 Business│   Helios Corporation · Acquisition readiness            │
│ 03 Financial   ───────────────────────────── gold hairline ───────   │
│ 04 Legal   │                                                         │
│ 05 Risk    │   ┌────────────┐ ┌────────────┐ ┌────────────┐          │
│    matrix  │   │ VERDICT    │ │ RISK SCORE │ │ RED FLAGS  │          │
│ 06 Red     │   │ Proceed w/ │ │ 14 / 25    │ │ 5 critical │          │
│    flags   │   │ conditions │ │ ● Medium   │ │ 2 resolved │          │
│ 07 Appendix│   └────────────┘ └────────────┘ └────────────┘          │
│            │                                                         │
│ ──────     │   01 — Executive Summary            (serif, 32 px)      │
│ ⚑ flags    │   Helios presents a fundamentally sound acquisition     │
│ ✦ insights │   case with three material caveats…  (68ch measure)     │
│ ★ marks    │                                                         │
│            │   ┌─ ✦ KEY FINDING ─────────────────────────────────┐   │
│            │   │ Revenue concentration: top-2 customers = 41%    │   │
│            │   │ of FY25 revenue.              [trace ⌲] [★]     │   │
│            │   └─────────────────────────────────────────────────┘   │
│            │                                                         │
│            │   [embedded chart: revenue bridge, full-bleed]          │
│            │   Fig. 3 — Revenue bridge FY23→FY25 (caption, ink-3)    │
└────────────┴─────────────────────────────────────────────────────────┘
```

### 4.2 Report mechanics

- **Contents rail** behaves like a digital book: numbered chapters, scroll-spy highlight, thin progress bar per chapter; quick filters jump to all ⚑ flags / ✦ insights / ★ bookmarks across the document.
- **Summary ⇄ Detailed toggle** (the centerpiece): Summary collapses every section to its verdict card + key findings (a partner reads it in 6 minutes); Detailed expands narrative, embedded charts, methodology, and full evidence. Crossfade 240 ms; scroll position maps to the same section.
- **Expandable insights**: paragraphs with underlying depth show a subtle `+ evidence (3)` affordance; expanding accordions citations with document name, page, and quote block.
- **Charts in narrative**: full-bleed within the text column, serif figure captions, consistent ramp; every chart has "view data" (table flip) for auditors.
- **Red Flag sections**: red-tinted full-width band, serif heading, severity badge, narrative, *and* a mini-timeline (identified → investigated → status). Unmissable, but elegant — brick red on paper, never neon.
- **Verdict cards** open the report: Proceed / Proceed-with-conditions / Decline, risk score dial, flag tally. The "answer" is on page one; everything else is the argument.
- **Export ▾**: PDF (print-graded CSS: serif headings survive, charts vectorized, flags get full-page callouts), PPT (each section → titled slide with chart + 3 bullets), Shareable link (read-only, expiring, watermarked with viewer email, section-level access control).
- Footer of every section: provenance line — "Generated from 87 verified findings · 214 documents · methodology v2.1" in ink-3 mono.

---

## 5 · Interaction Patterns & Microinteractions

**Navigation & speed**
- ⌘K omnibar: projects, sections, documents, findings, actions ("export Helios PPT") with type-ahead grouping. Sub-100 ms perceived.
- Sidebar number keys (1–6) jump workstreams; `[` `]` move between sections; `r` opens report.
- Virtualized tables + skeleton shimmer (no spinners on content surfaces; spinners only on actions).

**Micro-moments (all 150–280 ms, ease-out, respects `prefers-reduced-motion`)**
- Card hover: translateY(−2px) + shadow bloom.
- Checklist complete: checkmark draws in (SVG stroke 200 ms), row tints green for 600 ms, section ring increments with spring.
- Flag raised: chip pulses once (scale 1→1.06→1), red dot drops onto the sidebar count.
- KPI delta: number ticks up/down (mono digits roll 300 ms) on data refresh.
- Drawer: slide + 4 px overshoot spring; backdrop paper-fade, never black.
- Summary⇄Detail toggle: content crossfades while the toggle thumb slides; section anchors preserved.
- Bookmark: star fills with gold and emits a single 4-particle sparkle (subtle; finance, not gaming).
- AI assist: results stream in with a gold cursor; insert action animates the finding flying into its section (120 ms arc) so users learn where AI content lands.

**Data-density UX**
- Inline edit everywhere a value is owned by the user (assumptions, notes, statuses): click-to-edit with structured inputs (currency, %, date), Esc cancels, optimistic save with undo toast.
- Bookmarks (★) on any finding/chart/row → personal "Marked" tray in the report rail.
- Highlights: analysts can gold-highlight narrative text; highlights aggregate into the Summary view automatically (highlight = "this matters").

---

## 6 · Responsiveness

| Breakpoint | Behavior |
|---|---|
| ≥1280 (primary) | Full three-pane; report shows contents rail. |
| 1024–1279 (tablet landscape) | Project sidebar collapses to icons; context drawer overlays instead of docking. |
| 768–1023 (tablet portrait) | Single pane + bottom tab bar (Overview · Sections · Data · Report); tables switch to card-rows (label/value pairs); charts simplify to headline series. |
| Report on tablet | Becomes pure reading mode: contents in a sheet, full editorial column — best reading experience in the product, deliberately. |

Density is never solved by shrinking type below 13 px — it's solved by removing panes.

---

## 7 · Mapping to the current EquityScope build

| Spec | Today | Path |
|---|---|---|
| Findings model | `Claim` with citations + verification | Rename/extend: severity, owner, bookmark |
| Report Summary/Detail | single view | Add altitude toggle over existing sections |
| Risk matrix | severity/likelihood badges | Add 5×5 grid component fed by `risk_matrix` |
| Data room | EDGAR auto-ingest | Add upload + preview + AI summary drawer |
| Light theme | dark only | Implement token sheet above as `:root[data-theme=light]`, keep dark as option |
| Approvals/comments | — | New backend models; UI slots already exist (right drawer) |

The current Angular component structure (sidebar runs list, pipeline stepper, sectioned report with citation expanders) maps 1:1 onto this architecture — this spec is the destination, not a restart.
