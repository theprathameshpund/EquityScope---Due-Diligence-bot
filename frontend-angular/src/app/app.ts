import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from './api.service';
import { ReportViewComponent } from './components/report-view';
import { RunsListComponent } from './components/runs-list';
import { TimelineComponent, TimelineRow } from './components/timeline';
import { Health, ProgressEvent, ReportStatus, RunSummary } from './models';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, TimelineComponent, ReportViewComponent, RunsListComponent],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App implements OnInit, OnDestroy {
  private readonly api = inject(ApiService);
  private closeStream: (() => void) | null = null;

  readonly company = signal('');
  readonly focus = signal('');
  readonly formError = signal('');

  readonly health = signal<Health | null>(null);
  readonly runs = signal<RunSummary[]>([]);
  readonly currentRunId = signal<string | null>(null);
  readonly running = signal(false);
  readonly timeline = signal<TimelineRow[]>([]);
  readonly tokens = signal(0);
  readonly cost = signal(0);
  readonly current = signal<ReportStatus | null>(null);

  ngOnInit(): void {
    this.api.health().subscribe({
      next: (health) => this.health.set(health),
      error: () => this.health.set(null),
    });
    this.refreshRuns();
  }

  ngOnDestroy(): void {
    this.closeStream?.();
  }

  refreshRuns(): void {
    this.api.listRuns().subscribe({
      next: (response) => this.runs.set(response.runs),
      error: () => this.runs.set([]),
    });
  }

  startRun(): void {
    const company = this.company().trim();
    if (!company) {
      this.formError.set('Type a company name or ticker first (e.g. NVDA).');
      return;
    }
    this.formError.set('');
    this.current.set(null);
    this.timeline.set([]);
    this.tokens.set(0);
    this.cost.set(0);
    this.running.set(true);

    this.api.createReport(company, this.focus().trim()).subscribe({
      next: ({ run_id }) => {
        this.currentRunId.set(run_id);
        this.attachStream(run_id);
        this.refreshRuns();
      },
      error: (err) => {
        this.running.set(false);
        this.formError.set(`Failed to start run: ${err?.error?.detail ?? err.message ?? err}`);
      },
    });
  }

  openRun(runId: string): void {
    this.closeStream?.();
    this.currentRunId.set(runId);
    this.timeline.set([]);
    this.current.set(null);
    this.api.getReport(runId).subscribe({
      next: (status) => {
        if (status.status === 'running' || status.status === 'queued') {
          this.running.set(true);
          this.attachStream(runId);
        } else {
          this.running.set(false);
          this.current.set(status);
        }
      },
    });
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
    this.api.getReport(runId).subscribe({
      next: (status) => {
        this.current.set(status);
        this.refreshRuns();
      },
    });
  }
}
