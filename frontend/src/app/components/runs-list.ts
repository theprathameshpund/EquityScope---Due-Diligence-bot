import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { RunSummary } from '../models';

@Component({
  selector: 'app-runs-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="panel">
      <h3>Recent analyses</h3>
      @if (!runs().length) {
        <p class="empty">Nothing yet — your finished reports will appear here.</p>
      }
      <ul>
        @for (run of runs(); track run.run_id) {
          <li
            [class.active]="run.run_id === selected()"
            (click)="open.emit(run.run_id)"
            (keydown.enter)="open.emit(run.run_id)"
            tabindex="0"
          >
            <span class="avatar">{{ initials(run.company) }}</span>
            <div class="info">
              <div class="top">
                <span class="name">{{ run.company || 'Unknown' }}</span>
                <span class="status {{ run.status }}">
                  <i></i>{{ run.status }}
                </span>
              </div>
              <div class="sub">
                <span class="when">{{ when(run.updated_at) }}</span>
                @if (run.cost_usd !== null) {
                  <span class="cost">\${{ run.cost_usd.toFixed(4) }}</span>
                }
              </div>
            </div>
          </li>
        }
      </ul>
    </div>
  `,
  styles: `
    .panel {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 1.1rem 1.1rem 1.2rem;
      box-shadow: var(--shadow-soft);
    }
    h3 { margin: 0 0 0.9rem; font-size: 0.95rem; }
    .empty { color: var(--faint); font-size: 0.82rem; line-height: 1.5; }
    ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.45rem; }
    li {
      display: flex; align-items: center; gap: 0.7rem;
      background: var(--panel-2);
      border: 1px solid transparent;
      border-radius: var(--radius-sm);
      padding: 0.55rem 0.7rem;
      cursor: pointer;
      transition: border-color 0.15s, transform 0.12s;
    }
    li:hover { border-color: var(--border-strong); transform: translateX(2px); }
    li.active { border-color: var(--accent); background: rgba(91, 140, 255, 0.08); }
    .avatar {
      flex-shrink: 0;
      width: 34px; height: 34px;
      display: grid; place-items: center;
      border-radius: 9px;
      background: var(--panel-3);
      color: var(--text-dim);
      font-weight: 700; font-size: 0.72rem; letter-spacing: 0.02em;
    }
    li.active .avatar { background: var(--accent-grad); color: #fff; }
    .info { min-width: 0; flex: 1; }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
    .name {
      font-weight: 600; font-size: 0.86rem;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .status {
      display: inline-flex; align-items: center; gap: 0.3rem;
      font-size: 0.68rem; color: var(--muted); flex-shrink: 0;
    }
    .status i { width: 6px; height: 6px; border-radius: 50%; background: var(--faint); }
    .status.done i { background: var(--good); }
    .status.failed i { background: var(--bad); }
    .status.running i { background: var(--accent); animation: pulse 1.1s infinite; }
    .sub {
      display: flex; justify-content: space-between;
      color: var(--faint); font-size: 0.72rem; margin-top: 0.15rem;
    }
  `,
})
export class RunsListComponent {
  readonly runs = input.required<RunSummary[]>();
  readonly selected = input<string | null>(null);
  readonly open = output<string>();

  initials(company: string): string {
    if (!company) return '?';
    const clean = company.replace(/[^A-Za-z0-9 ]/g, '').trim();
    if (clean.length <= 4 && !clean.includes(' ')) return clean.toUpperCase();
    return clean
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word[0]?.toUpperCase() ?? '')
      .join('');
  }

  when(iso: string): string {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (Number.isNaN(then)) return '';
    const minutes = Math.floor((Date.now() - then) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}h ago`;
    return `${Math.floor(hours / 24)}d ago`;
  }
}
