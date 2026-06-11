import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import {
  CreateReportResponse,
  Health,
  ProgressEvent,
  ReportStatus,
  RunSummary,
} from './models';

/** All backend access. Dev server proxies /api and /healthz to FastAPI. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);

  createReport(company: string, focus: string): Observable<CreateReportResponse> {
    return this.http.post<CreateReportResponse>('/api/reports', { company, focus });
  }

  getReport(runId: string): Observable<ReportStatus> {
    return this.http.get<ReportStatus>(`/api/reports/${runId}`);
  }

  listRuns(): Observable<{ runs: RunSummary[] }> {
    return this.http.get<{ runs: RunSummary[] }>('/api/reports');
  }

  health(): Observable<Health> {
    return this.http.get<Health>('/healthz');
  }

  /**
   * Subscribe to the run's SSE progress stream.
   * Returns a cleanup function that closes the connection.
   */
  streamEvents(
    runId: string,
    onEvent: (event: ProgressEvent) => void,
    onEnd: () => void,
  ): () => void {
    const source = new EventSource(`/api/reports/${runId}/events`);
    source.addEventListener('progress', (raw) => {
      try {
        const event = JSON.parse((raw as MessageEvent).data) as ProgressEvent;
        onEvent(event);
        if (event.node === 'run' && (event.status === 'done' || event.status === 'error')) {
          source.close();
          onEnd();
        }
      } catch {
        /* malformed event — ignore */
      }
    });
    source.onerror = () => {
      // Server closed the stream (run finished) or connection dropped.
      if (source.readyState === EventSource.CLOSED) {
        onEnd();
      }
    };
    return () => source.close();
  }
}
