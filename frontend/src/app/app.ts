import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from './api.service';
import { AppIcon } from './components/icon';
import { ReportViewComponent } from './components/report-view';
import { RunsListComponent } from './components/runs-list';
import { TimelineComponent, TimelineRow } from './components/timeline';
import { Health, ProgressEvent, ReportStatus, RunSummary } from './models';

const FOCUS_SUGGESTIONS = [
  'supply-chain risk',
  'competition',
  'margin trends',
  'regulatory risk',
  'capital allocation',
  'AI strategy',
];

const THEME_KEY = 'equityscope-theme';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, AppIcon, TimelineComponent, ReportViewComponent, RunsListComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private closeStream: (() => void) | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private startedAt = 0;

  readonly focusSuggestions = FOCUS_SUGGESTIONS;

  readonly company = signal('');
  readonly focus = signal('');
  readonly formError = signal('');
  readonly theme = signal<'light' | 'dark'>('light');

  readonly health = signal<Health | null>(null);
  readonly runs = signal<RunSummary[]>([]);
  readonly runsLoading = signal(true);
  readonly currentRunId = signal<string | null>(null);
  readonly running = signal(false);
  readonly reportLoading = signal(false);
  readonly timeline = signal<TimelineRow[]>([]);
  readonly tokens = signal(0);
  readonly cost = signal(0);
  readonly elapsed = signal(0);
  readonly current = signal<ReportStatus | null>(null);

  /** Single rolled-up system status for the header. */
  readonly systemStatus = computed<{ tone: 'ok' | 'warn' | 'down'; label: string }>(() => {
    const health = this.health();
    if (!health) return { tone: 'down', label: 'API offline' };
    const up = [health.qdrant, health.postgres, health.redis].filter(Boolean).length;
    if (up === 3) return { tone: 'ok', label: 'All systems operational' };
    return { tone: 'warn', label: `Degraded — ${3 - up} service${up === 2 ? '' : 's'} down` };
  });

  ngOnInit(): void {
    const saved = localStorage.getItem(THEME_KEY);
    const osDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
    this.applyTheme(saved === 'dark' || saved === 'light' ? saved : osDark ? 'dark' : 'light');
    this.api.health().subscribe({
      next: (health) => this.health.set(health),
      error: () => this.health.set(null),
    });
    this.refreshRuns();
  }

  ngOnDestroy(): void {
    this.closeStream?.();
    this.stopTimer();
  }

  toggleTheme(): void {
    this.applyTheme(this.theme() === 'light' ? 'dark' : 'light');
  }

  private applyTheme(theme: 'light' | 'dark'): void {
    this.theme.set(theme);
    document.documentElement.dataset['theme'] = theme;
    localStorage.setItem(THEME_KEY, theme);
  }

  refreshRuns(): void {
    this.api.listRuns().subscribe({
      next: (response) => {
        this.runs.set(response.runs);
        this.runsLoading.set(false);
      },
      error: () => {
        this.runs.set([]);
        this.runsLoading.set(false);
      },
    });
  }

  pickFocus(suggestion: string): void {
    this.focus.set(this.focus() === suggestion ? '' : suggestion);
  }

  startRun(): void {
    const company = this.company().trim();
    if (!company) {
      this.formError.set('Type a company name or ticker first — e.g. NVDA or "Microsoft".');
      return;
    }
    this.formError.set('');
    this.current.set(null);
    this.timeline.set([]);
    this.tokens.set(0);
    this.cost.set(0);
    this.running.set(true);
    this.startTimer();

    this.api.createReport(company, this.focus().trim()).subscribe({
      next: ({ run_id }) => {
        this.currentRunId.set(run_id);
        this.attachStream(run_id);
        this.refreshRuns();
      },
      error: (err) => {
        this.running.set(false);
        this.stopTimer();
        this.formError.set(`Failed to start run: ${err?.error?.detail ?? err.message ?? err}`);
      },
    });
  }

  openRun(runId: string): void {
    this.closeStream?.();
    this.currentRunId.set(runId);
    this.timeline.set([]);
    this.current.set(null);
    this.reportLoading.set(true);
    this.api.getReport(runId).subscribe({
      next: (status) => {
        this.reportLoading.set(false);
        if (status.status === 'running' || status.status === 'queued') {
          this.running.set(true);
          this.startTimer();
          this.attachStream(runId);
        } else {
          this.running.set(false);
          this.stopTimer();
          this.current.set(status);
        }
      },
      error: () => this.reportLoading.set(false),
    });
  }

  private startTimer(): void {
    this.stopTimer();
    this.startedAt = Date.now();
    this.elapsed.set(0);
    this.timer = setInterval(
      () => this.elapsed.set(Math.floor((Date.now() - this.startedAt) / 1000)),
      1000,
    );
  }

  private stopTimer(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  elapsedLabel(): string {
    const total = this.elapsed();
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    return minutes ? `${minutes}m ${seconds.toString().padStart(2, '0')}s` : `${seconds}s`;
  }

  private attachStream(runId: string): void {
    this.closeStream?.();
    this.closeStream = this.api.streamEvents(
      runId,
      (event) => this.applyEvent(event),
      () => this.finishRun(runId),
    );
  }

  private applyEvent(event: ProgressEvent): void {
    this.tokens.set(event.tokens_used);
    this.cost.set(event.cost_usd);
    this.timeline.update((rows) => {
      const next = rows.filter((row) => row.node !== event.node);
      next.push({ node: event.node, status: event.status, message: event.message });
      return next;
    });
  }

  private finishRun(runId: string): void {
    this.running.set(false);
    this.stopTimer();
    this.api.getReport(runId).subscribe({
      next: (status) => {
        this.current.set(status);
        this.refreshRuns();
      },
    });
  }
}
