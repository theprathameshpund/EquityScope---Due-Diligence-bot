import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { RunSummary } from '../models';

@Component({
  selector: 'app-runs-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h3>Recent runs</h3>
    @if (!runs().length) {
      <p class="muted">No runs yet — start one above.</p>
    }
    <ul>
      @for (run of runs(); track run.run_id) {
        <li
          [class.active]="run.run_id === selected()"
          (click)="open.emit(run.run_id)"
          (keydown.enter)="open.emit(run.run_id)"
          tabindex="0"
        >
          <div class="top">
            <strong>{{ run.company || '?' }}</strong>
            <span class="badge {{ run.status }}">{{ run.status }}</span>
          </div>
          <div class="sub">
            <code>{{ run.run_id }}</code>
            @if (run.cost_usd !== null) {
              <span>\${{ run.cost_usd.toFixed(4) }}</span>
            }
          </div>
        </li>
      }
    </ul>
  `,
  styles: `
    h3 { margin-top: 0; }
    .muted { color: var(--muted); font-size: 0.85rem; }
    ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.5rem; }
    li {
      background: var(--panel-2); border-radius: 8px; padding: 0.6rem 0.8rem;
      cursor: pointer; border: 1px solid transparent;
    }
    li:hover, li.active { border-color: var(--accent); }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
    .sub { display: flex; justify-content: space-between; color: var(--muted); font-size: 0.75rem; margin-top: 0.3rem; }
    .badge { font-size: 0.7rem; padding: 0.1rem 0.5rem; border-radius: 999px; background: var(--panel); }
    .badge.done { color: var(--good); }
    .badge.failed { color: var(--bad); }
    .badge.running { color: var(--accent); }
  `,
})
export class RunsListComponent {
  readonly runs = input.required<RunSummary[]>();
  readonly selected = input<string | null>(null);
  readonly open = output<string>();
}
