import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

export interface TimelineRow {
  node: string;
  status: string;
  message: string;
}

interface Stage {
  node: string;
  label: string;
  detail: string;
  parallel?: boolean;
}

/** Canonical pipeline order — shown even before events arrive. */
const STAGES: Stage[] = [
  { node: 'ingest_check', label: 'Ingest & index', detail: 'SEC filings → chunks → vector index' },
  { node: 'filings', label: 'Filings research', detail: 'agentic RAG over 10-K / 10-Q / 8-K', parallel: true },
  { node: 'market', label: 'Market data', detail: 'price, multiples, peers', parallel: true },
  { node: 'news', label: 'News scan', detail: 'last 90 days, sentiment-tagged', parallel: true },
  { node: 'analyst', label: 'Financial analysis', detail: 'deterministic XBRL metrics' },
  { node: 'writer', label: 'Report writer', detail: 'structured, fully cited draft' },
  { node: 'critic', label: 'Critic verification', detail: 'NLI entailment + numeric checks' },
  { node: 'finalize', label: 'Finalize', detail: 'drop unverified, compute cost' },
];

type StageState = 'pending' | 'running' | 'done' | 'error';

@Component({
  selector: 'app-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="pipeline">
      @for (stage of stages(); track stage.node) {
        <div class="stage {{ stage.state }}">
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
      background: var(--surface-2);
      border: 1px solid var(--line-strong);
      color: var(--ink-3);
      font-size: 0.72rem; font-weight: 700;
      flex-shrink: 0;
    }
    .rail::after {
      content: '';
      flex: 1; width: 2px; min-height: 10px;
      background: var(--line);
      margin: 2px 0;
    }
    .stage:last-child .rail::after { display: none; }

    .stage.done .dot { background: var(--green-bg); border-color: var(--green); color: var(--green); }
    .stage.error .dot { background: var(--red-bg); border-color: var(--red); color: var(--red); }
    .stage.running .dot { border-color: var(--brand-2); }
    .ring {
      width: 10px; height: 10px; border-radius: 50%;
      background: var(--brand-2);
      animation: pulse 1.1s ease-in-out infinite;
    }

    .body { padding-bottom: 0.95rem; min-width: 0; flex: 1; }
    .line1 { display: flex; align-items: center; gap: 0.55rem; }
    .label { font-weight: 600; font-size: 0.92rem; color: var(--ink); }
    .stage.pending .label { color: var(--ink-3); font-weight: 500; }
    .par-badge {
      font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--gold);
      background: var(--gold-soft);
      border-radius: 999px; padding: 0.08rem 0.5rem;
      font-weight: 600;
    }
    .state-txt { margin-left: auto; color: var(--ink-3); font-size: 0.73rem; font-family: var(--mono); }
    .stage.running .state-txt { color: var(--brand-2); }
    .stage.error .state-txt { color: var(--red); }
    .detail {
      color: var(--ink-2); font-size: 0.79rem; margin-top: 0.12rem;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .stage.pending .detail { color: var(--ink-3); }
    .stage.error .detail { color: var(--red); white-space: normal; }
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
