import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { EvidenceChunk, MetricValue, ReportStatus } from '../models';
import { ClaimsComponent } from './claims';

@Component({
  selector: 'app-report-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ClaimsComponent],
  template: `
    @if (data().report; as report) {
      <div class="meta-cards">
        <div class="card"><label>Tokens</label><b>{{ report.metadata.tokens_used.toLocaleString() }}</b></div>
        <div class="card"><label>Cost</label><b>\${{ report.metadata.cost_usd.toFixed(4) }}</b></div>
        <div class="card"><label>Duration</label><b>{{ report.metadata.duration_s.toFixed(0) }}s</b></div>
        <div class="card"><label>Claims</label><b>{{ claimCount() }}</b></div>
      </div>

      <h2>{{ report.company.name }} ({{ report.company.ticker }})</h2>
      <p class="sub">CIK {{ report.company.cik }} · run <code>{{ report.metadata.run_id }}</code></p>

      <section>
        <h3>Executive Summary</h3>
        <app-claims [claims]="report.executive_summary" [evidence]="evidenceById()" />
      </section>

      <section>
        <h3>Business Overview</h3>
        <app-claims [claims]="report.business_overview" [evidence]="evidenceById()" />
      </section>

      <section>
        <h3>Financial Health</h3>
        <table class="metrics">
          <thead><tr><th>Metric</th><th>Value</th><th>Period</th></tr></thead>
          <tbody>
            @for (metric of report.financial_health.table.metrics; track metric.metric_id) {
              <tr>
                <td>{{ metric.name }}</td>
                <td class="num">{{ formatMetric(metric) }}</td>
                <td class="muted">{{ metric.period }}</td>
              </tr>
            }
          </tbody>
        </table>
        <app-claims [claims]="report.financial_health.commentary" [evidence]="evidenceById()" />
      </section>

      <section>
        <h3>Risk Matrix</h3>
        @if (!report.risk_matrix.length) { <p class="muted">No risks identified.</p> }
        @for (risk of report.risk_matrix; track risk.title) {
          <div class="risk">
            <div class="risk-head">
              <strong>{{ risk.title }}</strong>
              <span class="badge sev-{{ risk.severity }}">severity: {{ risk.severity }}</span>
              <span class="badge sev-{{ risk.likelihood }}">likelihood: {{ risk.likelihood }}</span>
            </div>
            <app-claims [claims]="risk.claims" [evidence]="evidenceById()" />
          </div>
        }
      </section>

      <section>
        <h3>Recent Developments</h3>
        <app-claims [claims]="report.recent_developments" [evidence]="evidenceById()" />
      </section>

      <section>
        <h3>Red Flags</h3>
        <app-claims [claims]="report.red_flags" [evidence]="evidenceById()" />
      </section>

      @if (report.data_gaps.length) {
        <section>
          <h3>Data Gaps</h3>
          @for (gap of report.data_gaps; track gap) {
            <p class="gap">⚠️ {{ gap }}</p>
          }
        </section>
      }

      <div class="downloads">
        <button (click)="download('md')">⬇ Markdown</button>
        <button (click)="download('json')">⬇ JSON</button>
      </div>
    }
  `,
  styles: `
    .meta-cards { display: flex; gap: 1rem; margin-bottom: 1.2rem; flex-wrap: wrap; }
    .card {
      background: var(--panel-2); border-radius: 10px; padding: 0.7rem 1.2rem;
      display: flex; flex-direction: column; gap: 0.2rem; min-width: 7rem;
    }
    .card label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; }
    .card b { font-size: 1.3rem; }
    h2 { margin: 0.4rem 0 0; }
    .sub { color: var(--muted); margin-top: 0.2rem; }
    section { margin-top: 1.6rem; }
    h3 { border-bottom: 1px solid var(--panel-2); padding-bottom: 0.4rem; }
    .metrics { width: 100%; border-collapse: collapse; margin-bottom: 1rem; font-size: 0.9rem; }
    .metrics th, .metrics td { text-align: left; padding: 0.45rem 0.6rem; border-bottom: 1px solid var(--panel-2); }
    .metrics .num { font-variant-numeric: tabular-nums; }
    .muted { color: var(--muted); }
    .risk { margin-bottom: 1rem; }
    .risk-head { display: flex; gap: 0.7rem; align-items: center; margin-bottom: 0.5rem; }
    .badge { font-size: 0.72rem; padding: 0.15rem 0.55rem; border-radius: 999px; background: var(--panel-2); }
    .sev-high { color: var(--bad); }
    .sev-medium { color: var(--warn); }
    .sev-low { color: var(--good); }
    .gap { background: rgba(255, 200, 0, 0.07); border-left: 3px solid var(--warn);
           padding: 0.5rem 0.8rem; border-radius: 4px; font-size: 0.88rem; }
    .downloads { display: flex; gap: 0.8rem; margin-top: 1.6rem; }
    button {
      background: var(--panel-2); color: var(--text); border: 1px solid #333;
      border-radius: 8px; padding: 0.5rem 1rem; cursor: pointer;
    }
    button:hover { border-color: var(--accent); }
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
    return (
      report.executive_summary.length +
      report.business_overview.length +
      report.financial_health.commentary.length +
      report.risk_matrix.reduce((n, risk) => n + risk.claims.length, 0) +
      report.recent_developments.length +
      report.red_flags.length
    );
  });

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
