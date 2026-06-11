import { ChangeDetectionStrategy, Component, input } from '@angular/core';

export interface TimelineRow {
  node: string;
  status: string;
  message: string;
}

const NODE_LABELS: Record<string, string> = {
  run: 'Run',
  ingest_check: 'Ingest & index filings',
  filings: 'Filings research (RAG)',
  market: 'Market data',
  news: 'News scan',
  analyst: 'Financial analysis (XBRL)',
  writer: 'Report writer',
  critic: 'Critic verification',
  finalize: 'Finalize',
};

const NODE_ICONS: Record<string, string> = {
  ingest_check: '📥',
  filings: '📄',
  market: '📈',
  news: '📰',
  analyst: '🧮',
  writer: '✍️',
  critic: '🔍',
  finalize: '✅',
  run: '🚀',
};

@Component({
  selector: 'app-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="timeline">
      @for (row of rows(); track row.node) {
        <div class="row" [class]="'st-' + row.status">
          <span class="state">
            @switch (row.status) {
              @case ('start') { <span class="spin">◌</span> }
              @case ('end') { ✅ }
              @case ('done') { 🏁 }
              @case ('error') { ❌ }
              @default { • }
            }
          </span>
          <span class="icon">{{ icon(row.node) }}</span>
          <span class="label">{{ label(row.node) }}</span>
          <span class="status">{{ row.status }}</span>
          @if (row.message) {
            <span class="msg" [title]="row.message">{{ row.message }}</span>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .timeline { display: flex; flex-direction: column; gap: 0.45rem; }
    .row {
      display: flex; align-items: center; gap: 0.6rem;
      padding: 0.45rem 0.7rem; border-radius: 8px;
      background: var(--panel-2); font-size: 0.92rem;
    }
    .row.st-error { outline: 1px solid var(--bad); }
    .label { font-weight: 600; }
    .status { color: var(--muted); font-size: 0.8rem; }
    .msg {
      color: var(--muted); font-size: 0.8rem; font-style: italic;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 26rem;
    }
    .spin { display: inline-block; animation: rot 0.9s linear infinite; }
    @keyframes rot { to { transform: rotate(360deg); } }
  `,
})
export class TimelineComponent {
  readonly rows = input.required<TimelineRow[]>();

  label(node: string): string {
    return NODE_LABELS[node] ?? node;
  }

  icon(node: string): string {
    return NODE_ICONS[node] ?? '•';
  }
}
