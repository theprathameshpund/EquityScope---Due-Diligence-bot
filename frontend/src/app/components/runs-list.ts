import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { RunSummary } from '../models';
import { AppIcon } from './icon';

type StatusFilter = 'all' | 'done' | 'failed';

@Component({
  selector: 'app-runs-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, AppIcon],
  template: `
    <div class="panel">
      <div class="head">
        <h3>Analyses</h3>
        @if (!loading()) { <span class="total">{{ filtered().length }}</span> }
      </div>

      <div class="search">
        <app-icon name="search" [size]="14" />
        <input
          type="search"
          placeholder="Filter by company…"
          [ngModel]="query()"
          (ngModelChange)="query.set($event)"
          aria-label="Filter analyses"
        />
      </div>

      <div class="filters" role="tablist" aria-label="Status filter">
        @for (option of filterOptions; track option.id) {
          <button
            role="tab"
            [attr.aria-selected]="filter() === option.id"
            [class.on]="filter() === option.id"
            (click)="filter.set(option.id)"
          >{{ option.label }}</button>
        }
      </div>

      @if (loading()) {
        @for (i of [1, 2, 3, 4]; track i) {
          <div class="row-skel">
            <div class="skeleton" style="width: 34px; height: 34px; border-radius: 9px;"></div>
            <div style="flex: 1; display: flex; flex-direction: column; gap: 6px;">
              <div class="skeleton" style="width: 70%;"></div>
              <div class="skeleton" style="width: 45%; height: 0.65rem;"></div>
            </div>
          </div>
        }
      } @else if (!filtered().length) {
        <p class="empty">
          @if (runs().length) { No analyses match this filter. }
          @else { Nothing yet — your finished reports will appear here. }
        </p>
      }

      <ul>
        @for (run of filtered(); track run.run_id) {
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
                <span class="status {{ run.status }}"><i></i>{{ run.status }}</span>
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
      background: var(--surface);
      border-radius: var(--radius);
      padding: 1.05rem 1rem 1.1rem;
      box-shadow: var(--shadow-rest);
    }
    .head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem; }
    h3 {
      font-size: 0.72rem;
      text-transform: uppercase; letter-spacing: 0.1em;
      color: var(--ink-2); font-weight: 650;
    }
    .total {
      font-size: 0.7rem; font-family: var(--mono);
      color: var(--ink-3);
      background: var(--surface-2);
      border-radius: 999px; padding: 0.06rem 0.5rem;
    }

    .search {
      display: flex; align-items: center; gap: 0.5rem;
      border: 1px solid var(--line-strong);
      border-radius: var(--radius-sm);
      padding: 0 0.7rem;
      color: var(--ink-3);
      margin-bottom: 0.6rem;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .search:focus-within { border-color: var(--brand-2); box-shadow: var(--focus-ring); }
    .search input {
      flex: 1; min-width: 0;
      border: none; background: transparent;
      color: var(--ink); font-family: var(--font); font-size: 0.84rem;
      padding: 0.5rem 0;
    }
    .search input:focus { outline: none; box-shadow: none; }
    .search input::placeholder { color: var(--ink-3); }

    .filters { display: flex; gap: 0.3rem; margin-bottom: 0.7rem; }
    .filters button {
      flex: 1;
      border: none; background: transparent;
      color: var(--ink-3); font-size: 0.74rem; font-weight: 600;
      padding: 0.3rem 0; border-radius: 6px;
      cursor: pointer;
      transition: color 0.15s;
    }
    .filters button:hover { color: var(--ink-2); background: var(--surface-2); }
    .filters button.on { background: var(--surface-2); color: var(--ink); }

    .row-skel { display: flex; gap: 0.7rem; align-items: center; padding: 0.55rem 0.3rem; }

    .empty { color: var(--ink-3); font-size: 0.8rem; line-height: 1.5; padding: 0.3rem 0.2rem; }

    ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.3rem; }
    li {
      display: flex; align-items: center; gap: 0.7rem;
      border-radius: var(--radius-sm);
      padding: 0.52rem 0.6rem;
      cursor: pointer;
      border: 1px solid transparent;
      transition: border-color 0.15s;
    }
    li:hover { background: var(--surface-2); }
    li.active { background: var(--brand-soft); border-color: color-mix(in srgb, var(--brand-2) 35%, transparent); }
    .avatar {
      flex-shrink: 0;
      width: 34px; height: 34px;
      display: grid; place-items: center;
      border-radius: 9px;
      background: var(--surface-2);
      color: var(--ink-2);
      font-weight: 650; font-size: 0.66rem; letter-spacing: 0.02em;
      font-family: var(--mono);
    }
    li.active .avatar { background: var(--brand); color: #f6f3ec; }
    :host-context([data-theme="dark"]) li.active .avatar { color: #10131a; }
    .info { min-width: 0; flex: 1; }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; }
    .name {
      font-weight: 600; font-size: 0.85rem; color: var(--ink);
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    .status {
      display: inline-flex; align-items: center; gap: 0.3rem;
      font-size: 0.66rem; color: var(--ink-3); flex-shrink: 0;
    }
    .status i { width: 6px; height: 6px; border-radius: 50%; background: var(--ink-3); }
    .status.done i { background: var(--green); }
    .status.failed i { background: var(--red); }
    .status.running i { background: var(--brand-2); animation: pulse 1.1s infinite; }
    .sub {
      display: flex; justify-content: space-between;
      color: var(--ink-3); font-size: 0.7rem; margin-top: 0.12rem;
    }
    .cost { font-family: var(--mono); }
  `,
})
export class RunsListComponent {
  readonly runs = input.required<RunSummary[]>();
  readonly loading = input(false);
  readonly selected = input<string | null>(null);
  readonly open = output<string>();

  readonly query = signal('');
  readonly filter = signal<StatusFilter>('all');

  readonly filterOptions: { id: StatusFilter; label: string }[] = [
    { id: 'all', label: 'All' },
    { id: 'done', label: 'Done' },
    { id: 'failed', label: 'Failed' },
  ];

  readonly filtered = computed(() => {
    const query = this.query().trim().toLowerCase();
    const filter = this.filter();
    return this.runs().filter((run) => {
      if (filter !== 'all' && run.status !== filter) return false;
      if (query && !`${run.company} ${run.run_id}`.toLowerCase().includes(query)) return false;
      return true;
    });
  });

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
