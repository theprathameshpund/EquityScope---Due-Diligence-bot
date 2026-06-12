import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { Claim, EvidenceChunk, MetricValue, ReportStatus } from '../models';
import { ClaimsComponent } from './claims';

interface NavSection {
  id: string;
  label: string;
  count: number | null;
}

@Component({
  selector: 'app-report-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ClaimsComponent],
  template: `
    @if (data().report; as report) {
      <!-- Company banner -->
      <div class="banner">
        <div class="identity">
          <span class="ticker-badge">{{ report.company.ticker || '—' }}</span>
          <div>
            <h2>{{ report.company.name }}</h2>
            <p class="sub">
              CIK {{ report.company.cik }}
              · generated {{ formatDate(report.metadata.generated_at) }}
              · run <code>{{ report.metadata.run_id }}</code>
            </p>
          </div>
        </div>
        <div class="stats">
          <div class="stat"><label>Verified claims</label><b>{{ claimCount() }}</b></div>
          <div class="stat"><label>Tokens</label><b>{{ report.metadata.tokens_used.toLocaleString() }}</b></div>
          <div class="stat"><label>Cost</label><b>\${{ report.metadata.cost_usd.toFixed(4) }}</b></div>
          <div class="stat"><label>Duration</label><b>{{ report.metadata.duration_s.toFixed(0) }}s</b></div>
        </div>
      </div>

      <!-- Section navigation -->
      <nav class="section-nav">
        @for (section of nav(); track section.id) {
          <a [href]="'#' + section.id">
            {{ section.label }}
            @if (section.count !== null) { <span class="count">{{ section.count }}</span> }
          </a>
        }
      </nav>

      <section id="sec-exec">
        <h3><span class="sec-icon">📌</span> Executive Summary</h3>
        <app-claims [claims]="report.executive_summary" [evidence]="evidenceById()" />
      </section>

      <section id="sec-biz">
        <h3><span class="sec-icon">🏢</span> Business Overview</h3>
        <app-claims [claims]="report.business_overview" [evidence]="evidenceById()" />
      </section>

      <section id="sec-fin">
        <h3><span class="sec-icon">🧮</span> Financial Health</h3>
        <p class="sec-note">All figures computed deterministically from SEC XBRL filings — never by the AI.</p>

        <div class="metric-tiles">
          @for (tile of headlineTiles(); track tile.id) {
            <div class="tile">
              <label>{{ tile.label }}</label>
              <b [class.pos]="tile.tone === 'pos'" [class.neg]="tile.tone === 'neg'">{{ tile.value }}</b>
              <span class="period">{{ tile.period }}</span>
            </div>
          }
        </div>

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
                  <td class="muted">{{ metric.period }}</td>
                </tr>
              }
            </tbody>
          </table>
        </details>

        <app-claims [claims]="report.financial_health.commentary" [evidence]="evidenceById()" />
      </section>

      <section id="sec-risk">
        <h3><span class="sec-icon">⚠️</span> Risk Matrix</h3>
        @if (!report.risk_matrix.length) { <p class="sec-note">No risks identified.</p> }
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
              <app-claims [claims]="risk.claims" [evidence]="evidenceById()" />
            </div>
          }
        </div>
      </section>

      <section id="sec-dev">
        <h3><span class="sec-icon">🗞️</span> Recent Developments</h3>
        <app-claims [claims]="report.recent_developments" [evidence]="evidenceById()" />
      </section>

      <section id="sec-flags">
        <h3><span class="sec-icon">🚩</span> Red Flags</h3>
        <app-claims [claims]="report.red_flags" [evidence]="evidenceById()" />
      </section>

      @if (report.data_gaps.length) {
        <section id="sec-gaps">
          <h3><span class="sec-icon">🕳️</span> Data Gaps &amp; Disclosures</h3>
          <p class="sec-note">
            EquityScope never silently keeps unverifiable content — everything dropped or unavailable is listed here.
          </p>
          @for (gap of report.data_gaps; track gap) {
            <p class="gap">{{ gap }}</p>
          }
        </section>
      }

      <div class="foot">
        <div class="models">
          @for (entry of modelEntries(); track entry[0]) {
            <span class="model"><label>{{ entry[0] }}</label>{{ entry[1] }}</span>
          }
        </div>
        <div class="downloads">
          <button (click)="download('md')">⬇ Markdown</button>
          <button (click)="download('json')">⬇ JSON</button>
        </div>
      </div>
    }
  `,
  styles: `
    /* Banner */
    .banner {
      display: flex; justify-content: space-between; align-items: center;
      gap: 1.4rem; flex-wrap: wrap;
      padding-bottom: 1.2rem; border-bottom: 1px solid var(--border);
    }
    .identity { display: flex; align-items: center; gap: 1rem; }
    .ticker-badge {
      display: grid; place-items: center;
      min-width: 64px; height: 64px; padding: 0 0.6rem;
      border-radius: 14px;
      background: var(--accent-grad);
      color: #fff; font-weight: 800; font-size: 1.15rem; letter-spacing: 0.03em;
      box-shadow: 0 6px 18px rgba(91, 140, 255, 0.35);
    }
    h2 { margin: 0; font-size: 1.4rem; }
    .sub { margin: 0.2rem 0 0; color: var(--muted); font-size: 0.82rem; }

    .stats { display: flex; gap: 1.6rem; }
    .stat { display: flex; flex-direction: column; align-items: flex-end; }
    .stat label { color: var(--faint); font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.07em; }
    .stat b { font-size: 1.25rem; font-variant-numeric: tabular-nums; }

    /* Section nav */
    .section-nav {
      position: sticky; top: 64px; z-index: 5;
      display: flex; gap: 0.4rem; flex-wrap: wrap;
      padding: 0.7rem 0;
      background: linear-gradient(180deg, var(--panel-solid) 80%, transparent);
      margin: 0 0 0.4rem;
    }
    .section-nav a {
      display: inline-flex; align-items: center; gap: 0.4rem;
      padding: 0.32rem 0.8rem;
      border-radius: 999px;
      border: 1px solid var(--border-strong);
      color: var(--muted); font-size: 0.78rem;
      transition: all 0.15s;
    }
    .section-nav a:hover { color: var(--text); border-color: var(--accent); text-decoration: none; }
    .count {
      background: var(--panel-3); border-radius: 999px;
      padding: 0 0.4rem; font-size: 0.7rem; color: var(--text-dim);
    }

    section { margin-top: 1.7rem; scroll-margin-top: 130px; }
    h3 {
      display: flex; align-items: center; gap: 0.55rem;
      font-size: 1.02rem; margin: 0 0 0.8rem;
      padding-bottom: 0.45rem; border-bottom: 1px solid var(--border);
    }
    .sec-icon { font-size: 1rem; }
    .sec-note { color: var(--faint); font-size: 0.82rem; margin: -0.3rem 0 0.8rem; }

    /* Metric tiles */
    .metric-tiles {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 0.8rem; margin-bottom: 1rem;
    }
    .tile {
      background: var(--panel-2); border: 1px solid var(--border);
      border-radius: var(--radius-sm); padding: 0.75rem 0.95rem;
      display: flex; flex-direction: column; gap: 0.15rem;
    }
    .tile label { color: var(--muted); font-size: 0.74rem; }
    .tile b { font-size: 1.3rem; font-variant-numeric: tabular-nums; }
    .tile .period { color: var(--faint); font-size: 0.7rem; }
    .pos { color: var(--good); }
    .neg { color: var(--bad); }

    .full-table { margin-bottom: 1rem; }
    .full-table summary { cursor: pointer; color: var(--accent); font-size: 0.84rem; margin-bottom: 0.5rem; }
    .metrics { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
    .metrics th {
      text-align: left; color: var(--faint); font-weight: 600;
      font-size: 0.74rem; text-transform: uppercase; letter-spacing: 0.05em;
    }
    .metrics th, .metrics td { padding: 0.5rem 0.65rem; border-bottom: 1px solid var(--border); }
    .metrics tbody tr:hover { background: rgba(255, 255, 255, 0.02); }
    .num { font-variant-numeric: tabular-nums; }
    .muted { color: var(--muted); }

    /* Risks */
    .risk-grid { display: flex; flex-direction: column; gap: 0.9rem; }
    .risk {
      border: 1px solid var(--border); border-left-width: 3px;
      border-radius: var(--radius-sm);
      padding: 0.9rem 1.1rem;
      background: var(--panel-2);
    }
    .risk.sev-high { border-left-color: var(--bad); }
    .risk.sev-medium { border-left-color: var(--warn); }
    .risk.sev-low { border-left-color: var(--good); }
    .risk-head {
      display: flex; justify-content: space-between; align-items: center;
      gap: 0.8rem; flex-wrap: wrap; margin-bottom: 0.6rem;
    }
    .badges { display: flex; gap: 0.4rem; }
    .badge {
      font-size: 0.7rem; padding: 0.16rem 0.6rem; border-radius: 999px;
      background: var(--panel-3); color: var(--muted);
    }
    .badge.b-high { color: var(--bad); background: var(--bad-bg); }
    .badge.b-medium { color: var(--warn); background: var(--warn-bg); }
    .badge.b-low { color: var(--good); background: var(--good-bg); }

    .gap {
      background: var(--warn-bg);
      border-left: 3px solid var(--warn);
      padding: 0.55rem 0.9rem; border-radius: 6px;
      font-size: 0.85rem; color: var(--text-dim);
      margin: 0 0 0.5rem;
    }

    /* Footer */
    .foot {
      display: flex; justify-content: space-between; align-items: center;
      gap: 1rem; flex-wrap: wrap;
      margin-top: 2rem; padding-top: 1.2rem; border-top: 1px solid var(--border);
    }
    .models { display: flex; gap: 0.9rem; flex-wrap: wrap; }
    .model { display: flex; flex-direction: column; font-size: 0.72rem; color: var(--text-dim); }
    .model label { color: var(--faint); font-size: 0.64rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .downloads { display: flex; gap: 0.7rem; }
    .downloads button {
      background: var(--panel-2); color: var(--text);
      border: 1px solid var(--border-strong); border-radius: var(--radius-sm);
      padding: 0.5rem 1.1rem; cursor: pointer; font-size: 0.86rem;
      transition: border-color 0.15s, transform 0.12s;
    }
    .downloads button:hover { border-color: var(--accent); transform: translateY(-1px); }

    @media (max-width: 760px) {
      .stats { width: 100%; justify-content: space-between; }
      .stat { align-items: flex-start; }
    }
  `,
})
export class ReportViewComponent {
  readonly data = input.required<ReportStatus>();

  readonly evidenceById = computed<Record<string, EvidenceChunk>>(() => {
    const map: Record<string, EvidenceChunk> = {};
    for (const chunk of this.data().evidence ?? []) {
      map[chunk.chunk_id] = chunk;
    }
    return map;
  });

  readonly claimCount = computed(() => {
    const report = this.data().report;
    if (!report) return 0;
    return this.allClaims().length;
  });

  readonly nav = computed<NavSection[]>(() => {
    const report = this.data().report;
    if (!report) return [];
    const sections: NavSection[] = [
      { id: 'sec-exec', label: 'Summary', count: report.executive_summary.length },
      { id: 'sec-biz', label: 'Business', count: report.business_overview.length },
      { id: 'sec-fin', label: 'Financials', count: report.financial_health.table.metrics.length },
      { id: 'sec-risk', label: 'Risks', count: report.risk_matrix.length },
      { id: 'sec-dev', label: 'Developments', count: report.recent_developments.length },
      { id: 'sec-flags', label: 'Red flags', count: report.red_flags.length },
    ];
    if (report.data_gaps.length) {
      sections.push({ id: 'sec-gaps', label: 'Data gaps', count: report.data_gaps.length });
    }
    return sections;
  });

  /** Hand-picked headline metrics for the tile row, when present. */
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

  readonly modelEntries = computed<[string, string][]>(() => {
    const report = this.data().report;
    return report ? Object.entries(report.metadata.model_versions) : [];
  });

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
