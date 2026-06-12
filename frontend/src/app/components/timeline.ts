import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export interface TimelineRow {
  node: string;
  status: string;
  message: string;
}

interface Stage {
  node: string;
  label: string;
  icon: string;
  detail: string;
  parallel?: boolean;
}

/** Canonical pipeline order — shown even before events arrive. */
const STAGES: Stage[] = [
  { node: 'ingest_check', label: 'Ingest & index', icon: '📥', detail: 'SEC filings → chunks → vector index' },
  { node: 'filings', label: 'Filings research', icon: '📄', detail: 'agentic RAG over 10-K/10-Q/8-K', parallel: true },
  { node: 'market', label: 'Market data', icon: '📈', detail: 'price, multiples, peers', parallel: true },
  { node: 'news', label: 'News scan', icon: '📰', detail: 'last 90 days, sentiment-tagged', parallel: true },
  { node: 'analyst', label: 'Financial analysis', icon: '🧮', detail: 'deterministic XBRL metrics' },
  { node: 'writer', label: 'Report writer', icon: '✍️', detail: 'structured, fully cited draft' },
  { node: 'critic', label: 'Critic verification', icon: '🔍', detail: 'NLI entailment + numeric checks' },
  { node: 'finalize', label: 'Finalize', icon: '🏁', detail: 'drop unverified, compute cost' },
];

type StageState = 'pending' | 'running' | 'done' | 'error';

@Component({
  selector: 'app-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="pipeline">
      @for (stage of stages(); track stage.node) {
        <div class="stage {{ stage.state }}" [class.parallel]="stage.parallel">
          <div class="rail">
            <span class="dot">
              @switch (stage.state) {
                @case ('running') { <span class="ring"></span> }
                @case ('done') { ✓ }
                @case ('error') { ✕ }
                @default { }
              }
            </span>
          </div>
          <div class="body">
            <div class="line1">
              <span class="icon">{{ stage.icon }}</span>
              <span class="label">{{ stage.label }}</span>
              @if (stage.parallel) { <span class="par-badge">parallel</span> }
              <span class="state-txt">{{ stage.state }}</span>
            </div>
            <div class="detail">{{ stage.message || stage.detail }}</div>
          </div>
        </div>
      }
    </div>
  `,
  styles: `
    .pipeline { display: flex; flex-direction: column; }
    .stage { display: flex; gap: 0.9rem; }
    .rail { display: flex; flex-direction: column; align-items: center; width: 26px; }
    .dot {
      width: 24px; height: 24px; border-radius: 50%;
      display: grid; place-items: center;
      background: var(--panel-3);
      border: 1px solid var(--border-strong);
      color: var(--faint);
      font-size: 0.72rem; font-weight: 800;
      flex-shrink: 0;
      position: relative;
    }
    .rail::after {
      content: '';
      flex: 1; width: 2px; min-height: 10px;
      background: var(--panel-3);
      margin: 2px 0;
    }
    .stage:last-child .rail::after { display: none; }

    .stage.done .dot { background: var(--good-bg); border-color: var(--good); color: var(--good); }
    .stage.error .dot { background: var(--bad-bg); border-color: var(--bad); color: var(--bad); }
    .stage.running .dot { border-color: var(--accent); }
    .ring {
      width: 10px; height: 10px; border-radius: 50%;
      background: var(--accent);
      animation: pulse 1.1s ease-in-out infinite;
    }

    .body { padding-bottom: 0.9rem; min-width: 0; flex: 1; }
    .line1 { display: flex; align-items: center; gap: 0.5rem; }
    .icon { font-size: 0.95rem; }
    .label { font-weight: 650; font-size: 0.92rem; }
    .stage.pending .label { color: var(--muted); font-weight: 500; }
    .par-badge {
      font-size: 0.62rem; text-transform: uppercase; letter-spacing: 0.05em;
      color: var(--accent-2);
      border: 1px solid rgba(139, 92, 246, 0.4);
      border-radius: 999px; padding: 0.05rem 0.45rem;
    }
    .state-txt { margin-left: auto; color: var(--faint); font-size: 0.74rem; }
    .stage.running .state-txt { color: var(--accent); }
    .stage.error .state-txt { color: var(--bad); }
    .detail {
      color: var(--muted); font-size: 0.79rem; margin-top: 0.1rem;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .stage.error .detail { color: var(--bad); white-space: normal; }
  `,
})
export class TimelineComponent {
  readonly rows = input.required<TimelineRow[]>();

  readonly stages = computed(() => {
    const byNode = new Map(this.rows().map((row) => [row.node, row]));
    return STAGES.map((stage) => {
      const event = byNode.get(stage.node);
      let state: StageState = 'pending';
      if (event) {
        state =
          event.status === 'start' ? 'running'
          : event.status === 'error' ? 'error'
          : 'done';
      }
      return { ...stage, state, message: event?.message ?? '' };
    });
  });
}
