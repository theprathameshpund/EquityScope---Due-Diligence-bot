import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import {
  Claim,
  EvidenceChunk,
  MetricValue,
  ReportStatus,
} from '../models';
import { ClaimsComponent } from './claims';
import { AppIcon } from './icon';

interface NavSection {
  id: string;
  no: string;
  label: string;
  count: number | null;
}

interface MetricGroup {
  name: string;
  metrics: MetricValue[];
}

const METRIC_GROUP_RULES: { name: string; match: RegExp }[] = [
  { name: 'Growth', match: /^revenue_/ },
  { name: 'Cash flow & quality', match: /^(fcf|accruals|cash_conversion|roic)/ },
  { name: 'Margins', match: /margin/ },
  { name: 'Leverage & liquidity', match: /^(debt_to_ebitda|current_ratio)/ },
  { name: 'Share count', match: /dilution/ },
];

@Component({
  selector: 'app-report-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ClaimsComponent, AppIcon],
  template: `
    @if (data().report; as report) {
      <!-- Masthead -->
      <div class="masthead no-print">
        <span class="confidential">Confidential · research use only</span>
        <div class="mode-toggle" role="tablist" aria-label="Report depth">
          <button
            role="tab"
            [attr.aria-selected]="view() === 'summary'"
            [class.on]="view() === 'summary'"
            (click)="view.set('summary')"
          >Summary</button>
          <button
            role="tab"
            [attr.aria-selected]="view() === 'detailed'"
            [class.on]="view() === 'detailed'"
            (click)="view.set('detailed')"
          >Detailed</button>
        </div>
      </div>

      <h2 class="title">Due diligence report</h2>
      <p class="subtitle">
        {{ report.company.name }}
        <span class="tick">{{ report.company.ticker }}</span>
        · CIK {{ report.company.cik }}
        @if (report.company.sector) { · {{ report.company.sector }} }
        · {{ formatDate(report.metadata.generated_at) }}
      </p>
      <div class="gold-rule"></div>

      <!-- Verdict cards -->
      <div class="verdicts">
        <div class="verdict">
          <label>Investment rating</label>
          <b class="tone-{{ labelTone(report.institutional_summary.investment_rating) }}">
            {{ report.institutional_summary.investment_rating }}
          </b>
          <span>
            {{ report.institutional_summary.confidence_score }}/100 confidence ·
            {{ report.institutional_summary.investment_horizon }}
          </span>
        </div>
        <div class="verdict">
          <label>Expected return</label>
          <b class="mono">{{ report.institutional_summary.expected_return_range }}</b>
          <span>free-source verified range when available</span>
        </div>
        @if (report.scorecard.available && report.scorecard.composite_label) {
          <div class="verdict">
            <label>Scorecard</label>
            <b class="tone-{{ labelTone(report.scorecard.composite_label) }}">
              {{ report.scorecard.composite_label }}
            </b>
            <span class="mono-sub">{{ report.scorecard.composite_score.toFixed(1) }} / 5.0 composite</span>
          </div>
        }
        <div class="verdict">
          <label>Risk profile</label>
          <b class="tone-{{ riskProfile().tone }}">{{ riskProfile().label }}</b>
          <span>{{ riskProfile().detail }}</span>
        </div>
        <div class="verdict">
          <label>Verified claims</label>
          <b>{{ claimCount() }}</b>
          <span>every one cited &amp; checked</span>
        </div>
        <div class="verdict">
          <label>Run cost</label>
          <b class="mono">\${{ report.metadata.cost_usd.toFixed(4) }}</b>
          <span>{{ report.metadata.tokens_used.toLocaleString() }} tokens · {{ report.metadata.duration_s.toFixed(0) }}s</span>
        </div>
      </div>

      <!-- Contents -->
      <nav class="contents no-print">
        @for (section of nav(); track section.id) {
          <a [href]="'#' + section.id">
            <span class="no">{{ section.no }}</span>
            {{ section.label }}
            @if (section.count !== null) { <span class="count">{{ section.count }}</span> }
          </a>
        }
      </nav>

      <section id="sec-exec">
        <h3><span class="no">{{ sectionNo('sec-exec') }}</span> Executive summary</h3>
        <app-claims [claims]="report.executive_summary" [evidence]="evidenceById()" />
      </section>

      @if (report.scorecard.available && report.scorecard.composite_label) {
        <section id="sec-scorecard">
          <h3><span class="no">{{ sectionNo('sec-scorecard') }}</span> Investment scorecard</h3>
          <div class="scorecard-grid">
            @for (dim of report.scorecard.dimensions; track dim.name) {
              <div class="score-dim">
                <div class="score-head">
                  <span class="dim-name">{{ dim.name }}</span>
                  <span class="score-val tone-{{ scoreTone(dim.score) }}">{{ dim.score }}/5</span>
                </div>
                <div class="score-bar">
                  @for (i of [1, 2, 3, 4, 5]; track i) {
                    <div class="bar-seg" [class.filled]="i <= dim.score"></div>
                  }
                </div>
                @if (view() === 'detailed') {
                  <p class="score-note">{{ dim.rationale }}</p>
                }
              </div>
            }
          </div>
        </section>
      }

      @if (view() === 'detailed') {
        <section id="sec-biz">
          <h3><span class="no">{{ sectionNo('sec-biz') }}</span> Business overview</h3>
          <app-claims [claims]="report.business_overview" [evidence]="evidenceById()" />
        </section>
      }

      <section id="sec-fin">
        <h3><span class="no">{{ sectionNo('sec-fin') }}</span> Financial health</h3>
        <p class="note">All figures computed deterministically from SEC XBRL filings — never by the AI.</p>

        <div class="tiles">
          @for (tile of headlineTiles(); track tile.id) {
            <div class="tile">
              <label>{{ tile.label }}</label>
              <b [class.pos]="tile.tone === 'pos'" [class.neg]="tile.tone === 'neg'">
                {{ tile.value }}
                @if (tile.tone === 'pos') { <app-icon name="trend-up" [size]="13" /> }
                @else if (tile.tone === 'neg') { <app-icon name="trend-down" [size]="13" /> }
              </b>
              <span>{{ tile.period }}</span>
            </div>
          }
        </div>

        @if (view() === 'detailed') {
          <table class="metrics">
            <thead><tr><th>Metric</th><th class="num-h">Value</th><th>Period</th></tr></thead>
            <tbody>
              @for (group of metricGroups(); track group.name) {
                <tr class="group-row"><td colspan="3">{{ group.name }}</td></tr>
                @for (metric of group.metrics; track metric.metric_id) {
                  <tr>
                    <td>{{ metric.name }}</td>
                    <td class="num" [class.pos]="isPositive(metric)" [class.neg]="isNegative(metric)">
                      {{ formatMetric(metric) }}
                    </td>
                    <td class="dim">{{ metric.period }}</td>
                  </tr>
                }
              }
            </tbody>
          </table>
          <app-claims [claims]="report.financial_health.commentary" [evidence]="evidenceById()" />
        }
      </section>

      @if (hasValuation() && view() === 'detailed') {
        <section id="sec-val">
          <h3><span class="no">{{ sectionNo('sec-val') }}</span> Valuation &amp; market data</h3>

          <div class="tiles">
            @for (item of valuationTiles(); track item.label) {
              <div class="tile">
                <label>{{ item.label }}</label>
                <b>{{ item.value }}</b>
              </div>
            }
          </div>

          @if (report.valuation.recommendation) {
            <div class="consensus-row">
              <span class="pill-badge tone-{{ recTone(report.valuation.recommendation) }}">
                {{ report.valuation.recommendation.replace('_', ' ') }}
              </span>
              @if (report.valuation.num_analysts) {
                <span class="dim-text">{{ report.valuation.num_analysts }} analysts</span>
              }
              @if (report.valuation.target_mean) {
                <span class="target-range">
                  Target <b>\${{ report.valuation.target_mean!.toFixed(2) }}</b>
                  @if (report.valuation.target_high && report.valuation.target_low) {
                    <span class="dim-text">({{ '$' + report.valuation.target_low!.toFixed(0) }}–{{ '$' + report.valuation.target_high!.toFixed(0) }})</span>
                  }
                </span>
              }
            </div>
          }

          @if (report.valuation.peers.length) {
            <table class="metrics">
              <thead><tr><th>Peer</th><th class="num-h">P/E</th><th class="num-h">P/S</th><th class="num-h">EV/EBITDA</th><th class="num-h">P/B</th></tr></thead>
              <tbody>
                @for (peer of report.valuation.peers; track peer.ticker) {
                  <tr>
                    <td><strong>{{ peer.ticker }}</strong></td>
                    <td class="num">{{ peer.pe_ttm != null ? peer.pe_ttm.toFixed(1) + 'x' : '—' }}</td>
                    <td class="num">{{ peer.price_to_sales != null ? peer.price_to_sales.toFixed(1) + 'x' : '—' }}</td>
                    <td class="num">{{ peer.ev_to_ebitda != null ? peer.ev_to_ebitda.toFixed(1) + 'x' : '—' }}</td>
                    <td class="num">{{ peer.price_to_book != null ? peer.price_to_book.toFixed(1) + 'x' : '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          }

          <app-claims [claims]="report.valuation.commentary" [evidence]="evidenceById()" />
        </section>
      }

      @if (hasEarningsQuality() && view() === 'detailed') {
        <section id="sec-eq">
          <h3><span class="no">{{ sectionNo('sec-eq') }}</span> Earnings quality</h3>
          @if (report.earnings_quality.quality_label) {
            <span class="pill-badge tone-{{ eqTone(report.earnings_quality.quality_label) }}">
              {{ report.earnings_quality.quality_label }} quality
            </span>
          }
          <div class="tiles" style="margin-top: 0.9rem;">
            @if (report.earnings_quality.accruals_ratio != null) {
              <div class="tile">
                <label>Accruals ratio</label>
                <b [class.pos]="report.earnings_quality.accruals_ratio! < 2"
                   [class.neg]="report.earnings_quality.accruals_ratio! > 10">
                  {{ report.earnings_quality.accruals_ratio!.toFixed(2) }}%
                </b>
                <span>lower is better</span>
              </div>
            }
            @if (report.earnings_quality.cash_conversion != null) {
              <div class="tile">
                <label>Cash conversion</label>
                <b [class.pos]="report.earnings_quality.cash_conversion! >= 1"
                   [class.neg]="report.earnings_quality.cash_conversion! < 0.8">
                  {{ report.earnings_quality.cash_conversion!.toFixed(2) }}x
                </b>
                <span>OCF / net income</span>
              </div>
            }
          </div>
          @for (flag of report.earnings_quality.flags; track flag) {
            <p class="gap"><app-icon name="alert" [size]="13" /> {{ flag }}</p>
          }
          <app-claims [claims]="report.earnings_quality.commentary" [evidence]="evidenceById()" />
        </section>
      }

      <section id="sec-risk">
        <h3><span class="no">{{ sectionNo('sec-risk') }}</span> Risk matrix</h3>
        @if (!report.risk_matrix.length) { <p class="note">Insufficient data for this section: risk matrix requires at least two verified or structural risks.</p> }
        <div class="risk-grid">
          @for (risk of report.risk_matrix; track risk.title) {
            <div class="risk sev-{{ risk.severity }}">
              <div class="risk-head">
                <strong>{{ risk.title }}</strong>
                <div class="badges">
                  <span class="badge tone-{{ sevTone(risk.severity) }}">{{ risk.severity }} severity</span>
                  <span class="badge tone-{{ sevTone(risk.likelihood) }}">{{ risk.likelihood }} likelihood</span>
                </div>
              </div>
              @if (view() === 'detailed') {
                <app-claims [claims]="risk.claims" [evidence]="evidenceById()" />
              }
            </div>
          }
        </div>
      </section>

      @if (view() === 'detailed') {
        <section id="sec-dev">
          <h3><span class="no">{{ sectionNo('sec-dev') }}</span> Recent developments</h3>
          <app-claims [claims]="report.recent_developments" [evidence]="evidenceById()" />
        </section>
      }

      @if (report.red_flags.length || view() === 'detailed') {
        <section id="sec-flags">
          <h3><span class="no">{{ sectionNo('sec-flags') }}</span> Red flags</h3>
          @if (!report.red_flags.length) {
            <p class="note">No material red flags identified based on available data.</p>
          }
          @for (flag of report.red_flags; track flag.claim_id) {
            <div class="flag-band">
              <span class="flag-label"><app-icon name="flag" [size]="11" /> Red flag</span>
              <p>{{ flag.text }}</p>
            </div>
          }
        </section>
      }

      @if (hasInsiders() && view() === 'detailed') {
        <section id="sec-insider">
          <h3><span class="no">{{ sectionNo('sec-insider') }}</span> Insider activity <span class="h-note">SEC Form 4</span></h3>
          <div class="consensus-row">
            <span class="pill-badge tone-{{ sentimentTone(report.insider_activity.sentiment) }}">
              {{ report.insider_activity.sentiment }}
            </span>
            <span class="dim-text">
              Net shares:
              <b [class.pos]="report.insider_activity.net_shares > 0"
                 [class.neg]="report.insider_activity.net_shares < 0">
                {{ report.insider_activity.net_shares > 0 ? '+' : '' }}{{ report.insider_activity.net_shares.toLocaleString() }}
              </b>
            </span>
            @if (report.insider_activity.net_value) {
              <span class="dim-text">
                Net value:
                <b [class.pos]="report.insider_activity.net_value > 0"
                   [class.neg]="report.insider_activity.net_value < 0">
                  {{ report.insider_activity.net_value > 0 ? '+' : '−' }}\${{ absMillions(report.insider_activity.net_value) }}M
                </b>
              </span>
            }
          </div>
          <table class="metrics">
            <thead><tr><th>Insider</th><th>Title</th><th>Type</th><th class="num-h">Shares</th><th>Date</th></tr></thead>
            <tbody>
              @for (txn of report.insider_activity.transactions.slice(0, 10); track txn.name + txn.date) {
                <tr>
                  <td>{{ txn.name }}</td>
                  <td class="dim">{{ txn.title || '—' }}</td>
                  <td>
                    <span class="badge tone-{{ txn.transaction_type === 'Purchase' ? 'good' : 'bad' }}">
                      {{ txn.transaction_type }}
                    </span>
                  </td>
                  <td class="num">{{ txn.shares.toLocaleString() }}</td>
                  <td class="dim">{{ txn.date }}</td>
                </tr>
              }
            </tbody>
          </table>
          <app-claims [claims]="report.insider_activity.commentary" [evidence]="evidenceById()" />
        </section>
      }

      @if (report.management_questions.length && view() === 'detailed') {
        <section id="sec-questions">
          <h3><span class="no">{{ sectionNo('sec-questions') }}</span> Questions for management</h3>
          <p class="note">Generated from detected anomalies and data gaps — bring these to the next earnings call.</p>
          <ol class="question-list">
            @for (q of report.management_questions; track q) {
              <li>{{ q }}</li>
            }
          </ol>
        </section>
      }

      @if (report.data_gaps.length) {
        <section id="sec-gaps">
          <h3><span class="no">{{ sectionNo('sec-gaps') }}</span> Data gaps &amp; disclosures</h3>
          <p class="note">Nothing unverifiable ships silently — everything dropped or unavailable is disclosed here.</p>
          @if (view() === 'summary') {
            <p class="gap">{{ report.data_gaps.length }} disclosure{{ report.data_gaps.length === 1 ? '' : 's' }} — switch to Detailed to read them.</p>
          } @else {
            @for (gap of report.data_gaps; track gap) {
              <p class="gap">{{ gap }}</p>
            }
          }
        </section>
      }

      <div class="foot">
        <span class="provenance">
          {{ claimCount() }} verified findings · models: {{ modelSummary() }}
        </span>
        <div class="downloads no-print">
          <button class="btn btn-ghost" (click)="download('md')">
            <app-icon name="download" [size]="14" /> Markdown
          </button>
          <button class="btn btn-ghost" (click)="downloadHtml()">
            <app-icon name="download" [size]="14" /> HTML
          </button>
          <button class="btn btn-ghost" (click)="download('json')">
            <app-icon name="download" [size]="14" /> JSON
          </button>
        </div>
      </div>
    }
  `,
  styles: `
    /* Masthead */
    .masthead { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
    .confidential {
      font-size: 0.66rem; letter-spacing: 0.13em; text-transform: uppercase;
      color: var(--gold); font-weight: 650;
    }
    .mode-toggle {
      display: inline-flex;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      overflow: hidden;
    }
    .mode-toggle button {
      border: none; background: transparent;
      padding: 0.32rem 1rem; font-size: 0.79rem;
      color: var(--ink-2); cursor: pointer;
      transition: color 0.15s;
    }
    .mode-toggle button.on { background: var(--brand); color: #f6f3ec; font-weight: 600; }
    :host-context([data-theme="dark"]) .mode-toggle button.on { color: #10131a; }

    /* Title — editorial serif */
    .title {
      font-family: var(--serif);
      font-size: 1.85rem; font-weight: 600;
      margin: 0.9rem 0 0.2rem; color: var(--ink);
      letter-spacing: -0.01em;
    }
    .subtitle { margin: 0; color: var(--ink-2); font-size: 0.88rem; }
    .tick {
      font-family: var(--mono); font-size: 0.72rem; font-weight: 600;
      background: var(--brand-soft); color: var(--brand);
      padding: 0.1rem 0.5rem; border-radius: 5px; margin-left: 0.15rem;
    }
    :host-context([data-theme="dark"]) .tick { color: var(--brand-2); }
    .gold-rule { height: 2px; background: var(--gold); opacity: 0.5; margin: 1.05rem 0 1.25rem; }

    /* Verdicts */
    .verdicts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(175px, 1fr));
      gap: 0.8rem; margin-bottom: 1.35rem;
    }
    .verdict {
      background: var(--surface-2);
      border-radius: var(--radius-sm);
      padding: 0.85rem 1rem;
      display: flex; flex-direction: column; gap: 0.16rem;
    }
    .verdict label {
      color: var(--ink-3); font-size: 0.64rem;
      text-transform: uppercase; letter-spacing: 0.09em; font-weight: 650;
    }
    .verdict b { font-size: 1.1rem; color: var(--ink); }
    .verdict b.mono { font-family: var(--mono); font-weight: 500; }
    .verdict span { color: var(--ink-3); font-size: 0.71rem; }
    .verdict .mono-sub { font-family: var(--mono); }
    .tone-good { color: var(--green) !important; }
    .tone-warn { color: var(--amber) !important; }
    .tone-bad { color: var(--red) !important; }

    /* Contents */
    .contents {
      display: flex; gap: 0.4rem; flex-wrap: wrap;
      position: sticky; top: 56px; z-index: 5;
      background: color-mix(in srgb, var(--surface) 93%, transparent);
      backdrop-filter: blur(8px);
      padding: 0.55rem 0; margin-bottom: 0.3rem;
    }
    .contents a {
      display: inline-flex; align-items: center; gap: 0.42rem;
      padding: 0.28rem 0.78rem; border-radius: 999px;
      border: 1px solid var(--line-strong);
      color: var(--ink-2); font-size: 0.76rem;
      transition: color 0.15s, border-color 0.15s;
    }
    .contents a:hover { color: var(--ink); border-color: var(--brand-2); text-decoration: none; }
    .contents .no { font-family: var(--mono); font-size: 0.65rem; color: var(--gold); font-weight: 500; }
    .count { background: var(--surface-2); border-radius: 999px; padding: 0 0.4rem; font-size: 0.68rem; color: var(--ink-2); }

    section { margin-top: 1.75rem; scroll-margin-top: 124px; }
    h3 {
      font-family: var(--serif);
      display: flex; align-items: baseline; gap: 0.6rem;
      font-size: 1.18rem; font-weight: 600; margin: 0 0 0.8rem;
      padding-bottom: 0.45rem; border-bottom: 1px solid var(--line);
      color: var(--ink);
    }
    h3 .no { font-family: var(--mono); font-size: 0.74rem; color: var(--gold); font-weight: 500; }
    .h-note { font-family: var(--font); font-size: 0.72rem; color: var(--ink-3); font-weight: 450; margin-left: auto; }
    .note { color: var(--ink-3); font-size: 0.81rem; margin: -0.3rem 0 0.8rem; }

    /* Scorecard */
    .scorecard-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(215px, 1fr));
      gap: 0.8rem;
    }
    .score-dim {
      background: var(--surface-2);
      border-radius: var(--radius-sm); padding: 0.8rem 1rem;
    }
    .score-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.45rem; }
    .dim-name { font-size: 0.86rem; font-weight: 600; color: var(--ink); }
    .score-val { font-size: 0.78rem; font-weight: 650; font-family: var(--mono); }
    .score-bar { display: flex; gap: 3px; }
    .bar-seg { height: 4px; flex: 1; border-radius: 999px; background: var(--line-strong); }
    .bar-seg.filled { background: var(--brand-2); }
    .score-note { color: var(--ink-2); font-size: 0.77rem; margin: 0.5rem 0 0; line-height: 1.5; }

    /* Tiles */
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(158px, 1fr));
      gap: 0.8rem; margin-bottom: 1rem;
    }
    .tile {
      background: var(--surface-2);
      border-radius: var(--radius-sm); padding: 0.78rem 0.95rem;
      display: flex; flex-direction: column; gap: 0.14rem;
    }
    .tile label { color: var(--ink-2); font-size: 0.73rem; }
    .tile b {
      display: inline-flex; align-items: center; gap: 0.35rem;
      font-size: 1.24rem; font-family: var(--mono); font-weight: 500; color: var(--ink);
      font-variant-numeric: tabular-nums;
    }
    .tile span { color: var(--ink-3); font-size: 0.69rem; font-family: var(--mono); }
    .pos { color: var(--green) !important; }
    .neg { color: var(--red) !important; }

    /* Tables */
    .metrics { width: 100%; border-collapse: collapse; font-size: 0.86rem; margin-bottom: 1rem; }
    .metrics th {
      text-align: left; color: var(--ink-3); font-weight: 650;
      font-size: 0.67rem; text-transform: uppercase; letter-spacing: 0.07em;
      background: var(--surface-2);
    }
    .metrics th.num-h { text-align: right; }
    .metrics th, .metrics td { padding: 0.48rem 0.7rem; border-bottom: 1px solid var(--line); }
    .metrics tbody tr:hover { background: var(--surface-2); }
    .group-row td {
      font-size: 0.68rem; font-weight: 650; text-transform: uppercase;
      letter-spacing: 0.08em; color: var(--gold);
      padding-top: 0.85rem; border-bottom-color: var(--line-strong);
      background: transparent !important;
    }
    .num { font-family: var(--mono); font-size: 0.82rem; text-align: right; font-variant-numeric: tabular-nums; }
    .dim { color: var(--ink-3); font-family: var(--mono); font-size: 0.76rem; }
    .dim-text { color: var(--ink-2); font-size: 0.84rem; }

    /* Pills & badges */
    .pill-badge {
      display: inline-flex; align-items: center;
      padding: 0.26rem 0.85rem; border-radius: 999px;
      font-size: 0.78rem; font-weight: 650;
      text-transform: capitalize;
      background: var(--surface-2);
    }
    .pill-badge.tone-good { background: var(--green-bg); }
    .pill-badge.tone-warn { background: var(--amber-bg); }
    .pill-badge.tone-bad { background: var(--red-bg); }
    .consensus-row { display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap; margin: 0.3rem 0 1rem; }
    .target-range { font-size: 0.86rem; color: var(--ink-2); }
    .target-range b { color: var(--ink); font-family: var(--mono); font-weight: 500; }

    /* Risks */
    .risk-grid { display: flex; flex-direction: column; gap: 0.8rem; }
    .risk {
      border-left: 3px solid var(--line-strong);
      background: var(--surface-2);
      border-top-right-radius: var(--radius-sm);
      border-bottom-right-radius: var(--radius-sm);
      padding: 0.85rem 1.1rem;
    }
    .risk.sev-high { border-left-color: var(--red); }
    .risk.sev-medium { border-left-color: var(--amber); }
    .risk.sev-low { border-left-color: var(--green); }
    .risk-head {
      display: flex; justify-content: space-between; align-items: center;
      gap: 0.8rem; flex-wrap: wrap; margin-bottom: 0.4rem;
    }
    .risk-head strong { font-family: var(--serif); font-size: 0.99rem; color: var(--ink); }
    .badges { display: flex; gap: 0.4rem; }
    .badge {
      font-size: 0.69rem; padding: 0.15rem 0.6rem; border-radius: 999px;
      font-weight: 600; background: var(--surface-3); color: var(--ink-2);
    }
    .badge.tone-bad { color: var(--red); background: var(--red-bg); }
    .badge.tone-warn { color: var(--amber); background: var(--amber-bg); }
    .badge.tone-good { color: var(--green); background: var(--green-bg); }

    /* Red flags */
    .flag-band {
      background: var(--red-bg);
      border-left: 3px solid var(--red);
      border-top-right-radius: var(--radius-sm);
      border-bottom-right-radius: var(--radius-sm);
      padding: 0.85rem 1.1rem; margin-bottom: 0.7rem;
    }
    .flag-label {
      display: inline-flex; align-items: center; gap: 0.4rem;
      font-size: 0.66rem; letter-spacing: 0.1em; text-transform: uppercase;
      color: var(--red); font-weight: 700;
    }
    .flag-band p { margin: 0.3rem 0 0; font-family: var(--serif); font-size: 0.97rem; color: var(--ink); max-width: 72ch; }

    /* Questions */
    .question-list { padding-left: 1.3rem; margin: 0; }
    .question-list li {
      padding: 0.42rem 0; border-bottom: 1px solid var(--line);
      font-size: 0.9rem; line-height: 1.55; color: var(--ink);
      max-width: 72ch;
    }
    .question-list li:last-child { border-bottom: none; }
    .question-list li::marker { color: var(--gold); font-family: var(--mono); font-size: 0.8rem; }

    .gap {
      display: flex; align-items: flex-start; gap: 0.5rem;
      background: var(--amber-bg);
      border-left: 3px solid var(--amber);
      border-top-right-radius: 6px; border-bottom-right-radius: 6px;
      padding: 0.55rem 0.9rem;
      font-size: 0.83rem; color: var(--ink-2);
      margin: 0 0 0.5rem;
    }

    /* Footer */
    .foot {
      display: flex; justify-content: space-between; align-items: center;
      gap: 1rem; flex-wrap: wrap;
      margin-top: 2rem; padding-top: 1.1rem; border-top: 1px solid var(--line);
    }
    .provenance { color: var(--ink-3); font-size: 0.72rem; font-family: var(--mono); }
  `,
})
export class ReportViewComponent {
  readonly data = input.required<ReportStatus>();
  readonly view = signal<'summary' | 'detailed'>('summary');

  readonly evidenceById = computed<Record<string, EvidenceChunk>>(() => {
    const map: Record<string, EvidenceChunk> = {};
    for (const chunk of this.data().evidence ?? []) {
      map[chunk.chunk_id] = chunk;
    }
    return map;
  });

  readonly claimCount = computed(() => this.allClaims().length);

  readonly hasValuation = computed(() => {
    const v = this.data().report?.valuation;
    return !!(v && (v.pe_ttm != null || v.forward_pe != null || v.recommendation || v.peers?.length));
  });

  readonly hasEarningsQuality = computed(() => {
    const eq = this.data().report?.earnings_quality;
    return !!(eq && (eq.accruals_ratio != null || eq.cash_conversion != null));
  });

  readonly hasInsiders = computed(() => {
    const ins = this.data().report?.insider_activity;
    return !!(ins?.available && ins.transactions?.length);
  });

  readonly riskProfile = computed(() => {
    const report = this.data().report;
    if (!report) return { label: 'Unknown', tone: 'warn', detail: '' };
    const high = report.risk_matrix.filter((risk) => risk.severity === 'high').length;
    const medium = report.risk_matrix.filter((risk) => risk.severity === 'medium').length;
    const low = report.risk_matrix.filter((risk) => risk.severity === 'low').length;
    const detail = `${high} high · ${medium} medium · ${low} low`;
    if (high > 0 || report.red_flags.length > 0) return { label: 'Elevated', tone: 'bad', detail };
    if (medium > 0) return { label: 'Moderate', tone: 'warn', detail };
    return { label: 'Low', tone: 'good', detail };
  });

  readonly nav = computed<NavSection[]>(() => {
    const report = this.data().report;
    if (!report) return [];
    const detailed = this.view() === 'detailed';
    const sections: NavSection[] = [];
    let index = 0;
    const push = (id: string, label: string, count: number | null) => {
      index += 1;
      sections.push({ id, no: index.toString().padStart(2, '0'), label, count });
    };
    push('sec-exec', 'Summary', report.executive_summary.length);
    if (report.scorecard.available && report.scorecard.composite_label) {
      push('sec-scorecard', 'Scorecard', null);
    }
    if (detailed) push('sec-biz', 'Business', report.business_overview.length);
    push('sec-fin', 'Financials', report.financial_health.table.metrics.length);
    if (detailed && this.hasValuation()) push('sec-val', 'Valuation', null);
    if (detailed && this.hasEarningsQuality()) push('sec-eq', 'Earnings quality', null);
    push('sec-risk', 'Risks', report.risk_matrix.length);
    if (detailed) push('sec-dev', 'Developments', report.recent_developments.length);
    if (detailed || report.red_flags.length) push('sec-flags', 'Red flags', report.red_flags.length);
    if (detailed && this.hasInsiders()) {
      push('sec-insider', 'Insiders', report.insider_activity.transactions.length);
    }
    if (detailed && report.management_questions.length) {
      push('sec-questions', 'Questions', report.management_questions.length);
    }
    if (report.data_gaps.length) push('sec-gaps', 'Disclosures', report.data_gaps.length);
    return sections;
  });

  readonly metricGroups = computed<MetricGroup[]>(() => {
    const report = this.data().report;
    if (!report) return [];
    const groups = new Map<string, MetricValue[]>();
    for (const metric of report.financial_health.table.metrics) {
      const rule = METRIC_GROUP_RULES.find((r) => r.match.test(metric.metric_id));
      const name = rule?.name ?? 'Other';
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name)!.push(metric);
    }
    const order = [...METRIC_GROUP_RULES.map((r) => r.name), 'Other'];
    return order
      .filter((name) => groups.has(name))
      .map((name) => ({ name, metrics: groups.get(name)! }));
  });

  readonly headlineTiles = computed(() => {
    const report = this.data().report;
    if (!report) return [];
    const byId = new Map(report.financial_health.table.metrics.map((m) => [m.metric_id, m]));
    const picks: { id: string; label: string }[] = [
      { id: 'revenue_growth_yoy', label: 'Revenue growth (YoY)' },
      { id: 'revenue_cagr_3y', label: 'Revenue CAGR (3y)' },
      { id: 'fcf', label: 'Free cash flow' },
      { id: 'fcf_margin', label: 'FCF margin' },
      { id: 'debt_to_ebitda', label: 'Debt / EBITDA' },
      { id: 'current_ratio', label: 'Current ratio' },
      { id: 'roic', label: 'ROIC' },
      { id: 'share_dilution', label: 'Share count change' },
    ];
    return picks
      .map(({ id, label }) => {
        const metric = byId.get(id);
        if (!metric) return null;
        let tone: 'pos' | 'neg' | '' = '';
        if (id === 'revenue_growth_yoy' || id === 'revenue_cagr_3y' || id === 'fcf_margin' || id === 'roic') {
          tone = metric.value >= 0 ? 'pos' : 'neg';
        }
        if (id === 'share_dilution') tone = metric.value <= 0 ? 'pos' : 'neg';
        if (id === 'current_ratio') tone = metric.value >= 1 ? 'pos' : 'neg';
        if (id === 'debt_to_ebitda') tone = metric.value <= 3 ? 'pos' : 'neg';
        return { id, label, value: this.formatMetric(metric), period: metric.period, tone };
      })
      .filter((tile): tile is NonNullable<typeof tile> => tile !== null);
  });

  readonly valuationTiles = computed(() => {
    const v = this.data().report?.valuation;
    if (!v) return [];
    const tiles: { label: string; value: string }[] = [];
    if (v.pe_ttm != null) tiles.push({ label: 'Trailing P/E', value: v.pe_ttm.toFixed(1) + 'x' });
    if (v.forward_pe != null) tiles.push({ label: 'Forward P/E', value: v.forward_pe.toFixed(1) + 'x' });
    if (v.ev_to_ebitda != null) tiles.push({ label: 'EV/EBITDA', value: v.ev_to_ebitda.toFixed(1) + 'x' });
    if (v.price_to_sales != null) tiles.push({ label: 'P/Sales', value: v.price_to_sales.toFixed(1) + 'x' });
    if (v.price_to_book != null) tiles.push({ label: 'P/Book', value: v.price_to_book.toFixed(1) + 'x' });
    if (v.beta != null) tiles.push({ label: 'Beta', value: v.beta.toFixed(2) });
    if (v.dividend_yield != null) tiles.push({ label: 'Dividend yield', value: (v.dividend_yield * 100).toFixed(2) + '%' });
    if (v.short_percent_float != null) tiles.push({ label: 'Short interest', value: (v.short_percent_float * 100).toFixed(1) + '%' });
    return tiles;
  });

  sectionNo(id: string): string {
    return this.nav().find((section) => section.id === id)?.no ?? '··';
  }

  labelTone(label: string): string {
    const lower = label.toLowerCase();
    if (lower.includes('buy')) return 'good';
    if (lower.includes('hold')) return 'warn';
    return 'bad';
  }

  scoreTone(score: number): string {
    return score >= 4 ? 'good' : score === 3 ? 'warn' : 'bad';
  }

  recTone(rec: string): string {
    if (rec.includes('buy')) return 'good';
    if (rec === 'hold') return 'warn';
    return 'bad';
  }

  eqTone(label: string): string {
    const lower = label.toLowerCase();
    return lower === 'high' ? 'good' : lower === 'medium' ? 'warn' : 'bad';
  }

  sentimentTone(sentiment: string): string {
    return sentiment === 'bullish' ? 'good' : sentiment === 'bearish' ? 'bad' : 'warn';
  }

  sevTone(level: string): string {
    return level === 'high' ? 'bad' : level === 'medium' ? 'warn' : 'good';
  }

  absMillions(value: number): string {
    return (Math.abs(value) / 1e6).toFixed(1);
  }

  modelSummary(): string {
    const report = this.data().report;
    if (!report) return '';
    const versions = report.metadata.model_versions;
    return ['writer', 'nli'].map((key) => versions[key]).filter(Boolean).join(' · ');
  }

  private allClaims(): Claim[] {
    const report = this.data().report;
    if (!report) return [];
    return [
      ...report.executive_summary,
      ...report.business_overview,
      ...report.financial_health.commentary,
      ...(report.valuation.commentary),
      ...(report.earnings_quality.commentary),
      ...(report.insider_activity.commentary),
      ...report.risk_matrix.flatMap((risk) => risk.claims),
      ...report.recent_developments,
      ...report.red_flags,
    ];
  }

  formatDate(iso: string): string {
    const date = new Date(iso);
    return Number.isNaN(date.getTime())
      ? iso
      : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  }

  formatMetric(metric: MetricValue): string {
    const v = metric.value;
    switch (metric.unit) {
      case 'USD':
        if (Math.abs(v) >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
        if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
        return `$${v.toLocaleString()}`;
      case '%':
        return `${v.toFixed(2)}%`;
      case 'x':
        return `${v.toFixed(2)}x`;
      case 'bps':
        return `${v > 0 ? '+' : ''}${v.toFixed(0)}bps`;
      default:
        return `${v.toLocaleString()} ${metric.unit}`;
    }
  }

  isPositive(metric: MetricValue): boolean {
    return metric.unit === 'bps' && metric.value > 0;
  }

  isNegative(metric: MetricValue): boolean {
    return metric.unit === 'bps' && metric.value < 0;
  }

  downloadHtml(): void {
    const data = this.data();
    const blob = new Blob([this.buildStandaloneHtml(data)], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `equityscope_${data.run_id}.html`;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  private buildStandaloneHtml(data: ReportStatus): string {
    const report = data.report;
    if (!report) {
      return '<!doctype html><html><body><p>No report available.</p></body></html>';
    }

    const esc = (value: unknown): string => this.escapeHtml(String(value ?? ''));
    const list = (items: string[]): string =>
      items.length ? `<ul>${items.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>` : '<p class="muted">Data unavailable or unverifiable.</p>';
    const claims = (items: Claim[]): string =>
      items.length
        ? `<ul class="claim-list">${items.map((claim) => `<li>${esc(claim.text)}${this.sourceBadges(claim)}</li>`).join('')}</ul>`
        : '<p class="muted">Data unavailable or unverifiable.</p>';
    const metrics = report.financial_health.table.metrics
      .map((metric) => `<tr><td>${esc(metric.name)}</td><td class="num">${esc(this.formatMetric(metric))}</td><td>${esc(metric.period)}</td></tr>`)
      .join('');
    const valuationRows = this.valuationTiles()
      .map((item) => `<tr><td>${esc(item.label)}</td><td class="num">${esc(item.value)}</td></tr>`)
      .join('');
    const risks = report.risk_matrix
      .map((risk) => `
        <article class="risk risk-${esc(risk.severity)}">
          <div class="risk-title">
            <h3>${esc(risk.title)}</h3>
            <span>${esc(risk.severity)} impact</span><span>${esc(risk.likelihood)} probability</span>
          </div>
          ${claims(risk.claims)}
          <p><strong>Mitigation:</strong> ${esc(risk.mitigation)}</p>
          <p><strong>Monitoring metrics:</strong> ${esc((risk.monitoring_metrics ?? []).join('; ') || 'Data unavailable or unverifiable.')}</p>
        </article>`)
      .join('');
    const scoreRows = Object.entries(report.quality_checks.final_scorecard ?? {})
      .map(([key, value]) => `<tr><td>${esc(key)}</td><td class="num">${esc(value)}</td></tr>`)
      .join('');

    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${esc(report.company.name)} Due Diligence Report</title>
  <style>
    :root { color-scheme: light; --ink:#111827; --muted:#667085; --line:#d9e2ef; --soft:#f6f8fb; --brand:#163b73; --gold:#b88900; --green:#0f766e; --amber:#a16207; --red:#b42318; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #eef2f7; color: var(--ink); font: 14px/1.55 Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, Arial, sans-serif; }
    .page { max-width: 1040px; margin: 28px auto; background: #fff; padding: 48px 56px; box-shadow: 0 24px 70px rgba(15,23,42,.12); }
    header { border-bottom: 3px solid var(--brand); padding-bottom: 22px; margin-bottom: 28px; }
    .eyebrow { color: var(--gold); font-size: 11px; font-weight: 800; letter-spacing: .16em; text-transform: uppercase; }
    h1 { font-family: Georgia, 'Times New Roman', serif; font-size: 34px; line-height: 1.12; margin: 8px 0; }
    .subtitle { color: var(--muted); }
    .decision { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: 12px; margin: 24px 0 30px; }
    .card { border: 1px solid var(--line); background: var(--soft); padding: 14px 16px; border-radius: 6px; }
    .card label { display:block; color: var(--muted); font-size: 11px; font-weight: 800; letter-spacing: .08em; text-transform: uppercase; }
    .card b { display:block; font-size: 18px; margin-top: 4px; }
    section { break-inside: avoid; margin-top: 30px; }
    h2 { font-family: Georgia, 'Times New Roman', serif; font-size: 21px; border-bottom: 1px solid var(--line); padding-bottom: 8px; margin: 0 0 12px; }
    h3 { margin: 0 0 8px; font-size: 15px; }
    ul { margin: 8px 0 0 20px; padding: 0; }
    li { margin: 6px 0; }
    .claim-list { list-style: none; margin-left: 0; }
    .claim-list li { border-left: 3px solid var(--brand); background: #fbfdff; padding: 9px 12px; border-radius: 4px; }
    .sources { display: block; margin-top: 5px; color: var(--muted); font-size: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-size: 11px; letter-spacing: .08em; text-transform: uppercase; background: var(--soft); }
    .num { text-align: right; font-variant-numeric: tabular-nums; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
    .risk { border: 1px solid var(--line); border-left-width: 4px; border-radius: 6px; padding: 14px; margin-bottom: 12px; }
    .risk-high { border-left-color: var(--red); } .risk-medium { border-left-color: var(--amber); } .risk-low { border-left-color: var(--green); }
    .risk-title { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .risk-title span { border:1px solid var(--line); border-radius:999px; padding:2px 8px; color:var(--muted); font-size:11px; text-transform:capitalize; }
    .muted { color: var(--muted); }
    footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 12px; }
    @media print { body { background:#fff; } .page { box-shadow:none; margin:0; padding: 24px; max-width: none; } }
    @media (max-width: 760px) { .page { margin:0; padding:28px 22px; } .decision, .grid-2 { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main class="page">
    <header>
      <div class="eyebrow">Institutional Due Diligence Report</div>
      <h1>${esc(report.company.name)} (${esc(report.company.ticker)})</h1>
      <div class="subtitle">CIK ${esc(report.company.cik)} | ${esc(report.company.sector || 'Sector unavailable')} | Generated ${esc(this.formatDate(report.metadata.generated_at))}</div>
    </header>

    <div class="decision">
      <div class="card"><label>Rating</label><b>${esc(report.institutional_summary.investment_rating)}</b></div>
      <div class="card"><label>Confidence</label><b>${esc(report.institutional_summary.confidence_score)}/100</b></div>
      <div class="card"><label>Horizon</label><b>${esc(report.institutional_summary.investment_horizon)}</b></div>
      <div class="card"><label>Expected Return</label><b>${esc(report.institutional_summary.expected_return_range)}</b></div>
    </div>

    <section><h2>Executive Summary</h2>${claims(report.executive_summary)}</section>
    <section class="grid-2"><div><h2>Key Bull Thesis</h2>${list(report.institutional_summary.key_bull_thesis)}</div><div><h2>Key Bear Thesis</h2>${list(report.institutional_summary.key_bear_thesis)}</div></section>
    <section class="grid-2"><div><h2>Top Catalysts</h2>${list(report.institutional_summary.top_catalysts)}</div><div><h2>Top Risks</h2>${list(report.institutional_summary.top_risks)}</div></section>
    <section><h2>Business Overview</h2>${claims(report.business_overview)}</section>
    <section><h2>Financial Analysis</h2><table><thead><tr><th>Metric</th><th class="num">Value</th><th>Period</th></tr></thead><tbody>${metrics}</tbody></table>${claims(report.financial_health.commentary)}</section>
    <section><h2>Valuation</h2><table><tbody>${valuationRows || '<tr><td>Data unavailable or unverifiable.</td><td></td></tr>'}</tbody></table>${claims(report.valuation.commentary)}</section>
    <section><h2>Earnings Quality</h2><p><strong>Quality label:</strong> ${esc(report.earnings_quality.quality_label || 'Data unavailable or unverifiable.')}</p>${claims(report.earnings_quality.commentary)}${list(report.earnings_quality.flags)}</section>
    <section><h2>Risk Matrix</h2>${risks || '<p class="muted">Data unavailable or unverifiable.</p>'}</section>
    <section><h2>Recent Developments</h2>${claims(report.recent_developments)}</section>
    <section><h2>Red Flags</h2>${claims(report.red_flags)}</section>
    <section><h2>Investment Thesis</h2><div class="grid-2"><div><h3>Bull Case</h3>${list(report.investment_thesis.bull_case)}</div><div><h3>Bear Case</h3>${list(report.investment_thesis.bear_case)}</div></div><p><strong>Probability-weighted outcome:</strong> ${esc(report.investment_thesis.probability_weighted_outcome)}</p></section>
    <section><h2>Management Questions</h2>${list(report.management_questions)}</section>
    <section><h2>Data Gaps and Quality Checks</h2>${list(report.data_gaps)}<p>${esc(report.quality_checks.claim_verification)}</p><table><tbody>${scoreRows}</tbody></table></section>
    <footer>Free-source policy: ${esc(report.quality_checks.source_policy )} | Run ${esc(report.metadata.run_id )} | ${esc(report.metadata.tokens_used.toLocaleString())} tokens | $${esc(report.metadata.cost_usd.toFixed(4))}</footer>
  </main>
</body>
</html>`;
  }

  private sourceBadges(claim: Claim): string {
    const sources = [...claim.citation_chunk_ids, ...claim.metric_ids];
    return sources.length ? `<span class="sources">Sources: ${sources.map((source) => this.escapeHtml(source)).join(', ')}</span>` : '';
  }

  private escapeHtml(value: string): string {
    return value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  download(kind: 'md' | 'json'): void {
    const data = this.data();
    const content =
      kind === 'md' ? (data.markdown ?? '') : JSON.stringify(data.report ?? {}, null, 2);
    const blob = new Blob([content], {
      type: kind === 'md' ? 'text/markdown' : 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `equityscope_${data.run_id}.${kind === 'md' ? 'md' : 'json'}`;
    anchor.click();
    URL.revokeObjectURL(url);
  }
}

