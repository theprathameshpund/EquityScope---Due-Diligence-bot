import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { Claim, EvidenceChunk, MetricValue, ReportStatus } from '../models';
import { ClaimsComponent } from './claims';

interface NavSection {
  id: string;
  no: string;
  label: string;
  count: number | null;
}

@Component({
  selector: 'app-report-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ClaimsComponent],
  template: `
    @if (data().report; as report) {
      <!-- Masthead -->
      <div class="masthead">
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
        · {{ formatDate(report.metadata.generated_at) }}
      </p>
      <div class="gold-rule"></div>

      <!-- Verdict cards -->
      <div class="verdicts">
        <div class="verdict">
          <label>Risk profile</label>
          <b class="risk-{{ riskProfile().tone }}">● {{ riskProfile().label }}</b>
          <span>{{ riskProfile().detail }}</span>
        </div>
        <div class="verdict">
          <label>Verified claims</label>
          <b>{{ claimCount() }}</b>
          <span>every one cited &amp; checked</span>
        </div>
        <div class="verdict">
          <label>Disclosures</label>
          <b>{{ report.data_gaps.length }}</b>
          <span>gaps &amp; dropped content listed</span>
        </div>
        <div class="verdict">
          <label>Run cost</label>
          <b class="mono">\${{ report.metadata.cost_usd.toFixed(4) }}</b>
          <span>{{ report.metadata.tokens_used.toLocaleString() }} tokens · {{ report.metadata.duration_s.toFixed(0) }}s</span>
        </div>
      </div>

      <!-- Contents -->
      <nav class="contents">
        @for (section of nav(); track section.id) {
          <a [href]="'#' + section.id">
            <span class="no">{{ section.no }}</span>
            {{ section.label }}
            @if (section.count !== null) { <span class="count">{{ section.count }}</span> }
          </a>
        }
      </nav>

      <section id="sec-exec">
        <h3><span class="no">01</span> Executive summary</h3>
        <app-claims [claims]="report.executive_summary" [evidence]="evidenceById()" />
      </section>

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
              <b [class.pos]="tile.tone === 'pos'" [class.neg]="tile.tone === 'neg'">{{ tile.value }}</b>
              <span>{{ tile.period }}</span>
            </div>
          }
        </div>

        @if (view() === 'detailed') {
          <details class="full-table" open>
            <summary>All {{ report.financial_health.table.metrics.length }} metrics</summary>
            <table class="metrics">
              <thead><tr><th>Metric</th><th>Value</th><th>Period</th></tr></thead>
              <tbody>
                @for (metric of report.financial_health.table.metrics; track metric.metric_id) {
                  <tr>
                    <td>{{ metric.name }}</td>
                    <td class="num" [class.pos]="isPositive(metric)" [class.neg]="isNegative(metric)">
                      {{ formatMetric(metric) }}
                    </td>
                    <td class="dim">{{ metric.period }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </details>
          <app-claims [claims]="report.financial_health.commentary" [evidence]="evidenceById()" />
        }
      </section>

      <section id="sec-risk">
        <h3><span class="no">{{ sectionNo('sec-risk') }}</span> Risk matrix</h3>
        @if (!report.risk_matrix.length) { <p class="note">No risks identified.</p> }
        <div class="risk-grid">
          @for (risk of report.risk_matrix; track risk.title) {
            <div class="risk sev-{{ risk.severity }}">
              <div class="risk-head">
                <strong>{{ risk.title }}</strong>
                <div class="badges">
                  <span class="badge b-{{ risk.severity }}">{{ risk.severity }} severity</span>
                  <span class="badge b-{{ risk.likelihood }}">{{ risk.likelihood }} likelihood</span>
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
            <p class="note">No red flags surfaced by this run.</p>
          }
          @for (flag of report.red_flags; track flag.claim_id) {
            <div class="flag-band">
              <span class="flag-label">⚑ Red flag</span>
              <p>{{ flag.text }}</p>
            </div>
          }
        </section>
      }

      @if (report.data_gaps.length) {
        <section id="sec-gaps">
          <h3><span class="no">{{ sectionNo('sec-gaps') }}</span> Data gaps &amp; disclosures</h3>
          <p class="note">Nothing unverifiable ships silently — everything dropped or unavailable is disclosed here.</p>
          @if (view() === 'summary') {
            <p class="gap-roll">{{ report.data_gaps.length }} disclosure{{ report.data_gaps.length === 1 ? '' : 's' }} — switch to Detailed to read them.</p>
          } @else {
            @for (gap of report.data_gaps; track gap) {
              <p class="gap">{{ gap }}</p>
            }
          }
        </section>
      }

      <div class="foot">
        <span class="provenance">
          Generated from {{ claimCount() }} verified findings ·
          models: {{ modelSummary() }}
        </span>
        <div class="downloads">
          <button (click)="download('md')">Markdown</button>
          <button (click)="download('json')">JSON</button>
        </div>
      </div>
    }
  `,
  styles: `
    /* Masthead */
    .masthead { display: flex; justify-content: space-between; align-items: center; gap: 1rem; flex-wrap: wrap; }
    .confidential {
      font-size: 0.68rem; letter-spacing: 0.14em; text-transform: uppercase;
      color: var(--gold); font-weight: 600;
    }
    .mode-toggle {
      display: inline-flex;
      border: 1px solid var(--line-strong);
      border-radius: 999px;
      overflow: hidden;
    }
    .mode-toggle button {
      border: none; background: transparent;
      padding: 0.34rem 1rem; font-size: 0.8rem;
      color: var(--ink-2); cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }
    .mode-toggle button.on { background: var(--brand); color: #f6f3ec; font-weight: 600; }

    /* Title — editorial serif */
    .title {
      font-family: var(--serif);
      font-size: 1.9rem; font-weight: 600;
      margin: 0.9rem 0 0.2rem; color: var(--ink);
      letter-spacing: -0.01em;
    }
    .subtitle { margin: 0; color: var(--ink-2); font-size: 0.9rem; }
    .tick {
      font-family: var(--mono); font-size: 0.74rem; font-weight: 600;
      background: var(--brand-soft); color: var(--brand);
      padding: 0.1rem 0.5rem; border-radius: 5px; margin-left: 0.2rem;
    }
    .gold-rule { height: 2px; background: var(--gold); opacity: 0.55; margin: 1.1rem 0 1.3rem; }

    /* Verdicts */
    .verdicts {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 0.8rem; margin-bottom: 1.4rem;
    }
    .verdict {
      background: var(--surface-2);
      border-radius: var(--radius-sm);
      padding: 0.85rem 1rem;
      display: flex; flex-direction: column; gap: 0.18rem;
    }
    .verdict label {
      color: var(--ink-3); font-size: 0.66rem;
      text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600;
    }
    .verdict b { font-size: 1.12rem; color: var(--ink); }
    .verdict b.mono { font-family: var(--mono); font-weight: 500; }
    .verdict span { color: var(--ink-3); font-size: 0.72rem; }
    .risk-low { color: var(--green) !important; }
    .risk-medium { color: var(--amber) !important; }
    .risk-high { color: var(--red) !important; }

    /* Contents */
    .contents {
      display: flex; gap: 0.45rem; flex-wrap: wrap;
      position: sticky; top: 66px; z-index: 5;
      background: color-mix(in srgb, var(--surface) 92%, transparent);
      backdrop-filter: blur(8px);
      padding: 0.6rem 0; margin-bottom: 0.4rem;
    }
    .contents a {
      display: inline-flex; align-items: center; gap: 0.45rem;
      padding: 0.3rem 0.8rem; border-radius: 999px;
      border: 1px solid var(--line-strong);
      color: var(--ink-2); font-size: 0.78rem;
      transition: all 0.15s;
    }
    .contents a:hover { color: var(--ink); border-color: var(--brand-2); text-decoration: none; }
    .contents .no { font-family: var(--mono); font-size: 0.68rem; color: var(--gold); font-weight: 500; }
    .count { background: var(--surface-2); border-radius: 999px; padding: 0 0.42rem; font-size: 0.7rem; color: var(--ink-2); }

    section { margin-top: 1.8rem; scroll-margin-top: 130px; }
    h3 {
      font-family: var(--serif);
      display: flex; align-items: baseline; gap: 0.6rem;
      font-size: 1.22rem; font-weight: 600; margin: 0 0 0.8rem;
      padding-bottom: 0.45rem; border-bottom: 1px solid var(--line);
      color: var(--ink);
    }
    h3 .no { font-family: var(--mono); font-size: 0.78rem; color: var(--gold); font-weight: 500; }
    .note { color: var(--ink-3); font-size: 0.82rem; margin: -0.3rem 0 0.8rem; }

    /* Tiles */
    .tiles {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 0.8rem; margin-bottom: 1rem;
    }
    .tile {
      background: var(--surface-2);
      border-radius: var(--radius-sm); padding: 0.8rem 1rem;
      display: flex; flex-direction: column; gap: 0.15rem;
    }
    .tile label { color: var(--ink-2); font-size: 0.74rem; }
    .tile b { font-size: 1.28rem; font-family: var(--mono); font-weight: 500; color: var(--ink); }
    .tile span { color: var(--ink-3); font-size: 0.7rem; font-family: var(--mono); }
    .pos { color: var(--green) !important; }
    .neg { color: var(--red) !important; }

    .full-table { margin-bottom: 1rem; }
    .full-table summary { cursor: pointer; color: var(--brand-2); font-size: 0.84rem; margin-bottom: 0.5rem; }
    .metrics { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    .metrics th {
      text-align: left; color: var(--ink-3); font-weight: 600;
      font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em;
      background: var(--surface-2);
    }
    .metrics th, .metrics td { padding: 0.5rem 0.7rem; border-bottom: 1px solid var(--line); }
    .metrics tbody tr:hover { background: var(--surface-2); }
    .num { font-family: var(--mono); font-size: 0.84rem; }
    .dim { color: var(--ink-3); font-family: var(--mono); font-size: 0.78rem; }

    /* Risks */
    .risk-grid { display: flex; flex-direction: column; gap: 0.85rem; }
    .risk {
      border-left: 3px solid var(--line-strong);
      border-radius: 0;
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
    .risk-head strong { font-family: var(--serif); font-size: 1rem; color: var(--ink); }
    .badges { display: flex; gap: 0.4rem; }
    .badge { font-size: 0.7rem; padding: 0.16rem 0.6rem; border-radius: 999px; font-weight: 600; }
    .badge.b-high { color: var(--red); background: var(--red-bg); }
    .badge.b-medium { color: var(--amber); background: var(--amber-bg); }
    .badge.b-low { color: var(--green); background: var(--green-bg); }

    /* Red flags */
    .flag-band {
      background: var(--red-bg);
      border-left: 3px solid var(--red);
      border-radius: 0;
      border-top-right-radius: var(--radius-sm);
      border-bottom-right-radius: var(--radius-sm);
      padding: 0.85rem 1.1rem; margin-bottom: 0.7rem;
    }
    .flag-label {
      font-size: 0.68rem; letter-spacing: 0.1em; text-transform: uppercase;
      color: var(--red); font-weight: 700;
    }
    .flag-band p { margin: 0.3rem 0 0; font-family: var(--serif); font-size: 0.98rem; color: var(--ink); }

    .gap, .gap-roll {
      background: var(--amber-bg);
      border-left: 3px solid var(--amber);
      border-radius: 0;
      border-top-right-radius: 6px; border-bottom-right-radius: 6px;
      padding: 0.55rem 0.9rem;
      font-size: 0.84rem; color: var(--ink-2);
      margin: 0 0 0.5rem;
    }

    /* Footer */
    .foot {
      display: flex; justify-content: space-between; align-items: center;
      gap: 1rem; flex-wrap: wrap;
      margin-top: 2rem; padding-top: 1.2rem; border-top: 1px solid var(--line);
    }
    .provenance { color: var(--ink-3); font-size: 0.74rem; font-family: var(--mono); }
    .downloads { display: flex; gap: 0.7rem; }
    .downloads button {
      background: var(--surface); color: var(--ink);
      border: 1px solid var(--line-strong); border-radius: var(--radius-sm);
      padding: 0.5rem 1.1rem; cursor: pointer; font-size: 0.85rem;
      transition: border-color 0.15s, transform 0.12s;
    }
    .downloads button:hover { border-color: var(--brand-2); transform: translateY(-1px); }
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

  readonly riskProfile = computed(() => {
    const report = this.data().report;
    if (!report) return { label: 'Unknown', tone: 'medium', detail: '' };
    const high = report.risk_matrix.filter((risk) => risk.severity === 'high').length;
    const medium = report.risk_matrix.filter((risk) => risk.severity === 'medium').length;
    const low = report.risk_matrix.filter((risk) => risk.severity === 'low').length;
    const detail = `${high} high · ${medium} medium · ${low} low`;
    if (high > 0 || report.red_flags.length > 0) return { label: 'Elevated', tone: 'high', detail };
    if (medium > 0) return { label: 'Moderate', tone: 'medium', detail };
    return { label: 'Low', tone: 'low', detail };
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
    if (detailed) push('sec-biz', 'Business', report.business_overview.length);
    push('sec-fin', 'Financials', report.financial_health.table.metrics.length);
    push('sec-risk', 'Risks', report.risk_matrix.length);
    if (detailed) push('sec-dev', 'Developments', report.recent_developments.length);
    if (detailed || report.red_flags.length) push('sec-flags', 'Red flags', report.red_flags.length);
    if (report.data_gaps.length) push('sec-gaps', 'Disclosures', report.data_gaps.length);
    return sections;
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
      { id: 'share_dilution', label: 'Share count change' },
    ];
    return picks
      .map(({ id, label }) => {
        const metric = byId.get(id);
        if (!metric) return null;
        let tone: 'pos' | 'neg' | '' = '';
        if (id === 'revenue_growth_yoy' || id === 'revenue_cagr_3y' || id === 'fcf_margin') {
          tone = metric.value >= 0 ? 'pos' : 'neg';
        }
        if (id === 'share_dilution') tone = metric.value <= 0 ? 'pos' : 'neg';
        if (id === 'current_ratio') tone = metric.value >= 1 ? 'pos' : 'neg';
        if (id === 'debt_to_ebitda') tone = metric.value <= 3 ? 'pos' : 'neg';
        return { id, label, value: this.formatMetric(metric), period: metric.period, tone };
      })
      .filter((tile): tile is NonNullable<typeof tile> => tile !== null);
  });

  sectionNo(id: string): string {
    return this.nav().find((section) => section.id === id)?.no ?? '··';
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

  download(kind: 'md' | 'json'): void {
    const data = this.data();
    const content =
      kind === 'md' ? (data.markdown ?? '') : JSON.stringify(data.report, null, 2);
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
