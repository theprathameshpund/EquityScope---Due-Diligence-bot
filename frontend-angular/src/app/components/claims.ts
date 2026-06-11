import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Claim, EvidenceChunk } from '../models';

/** A list of verified claims with expandable source citations. */
@Component({
  selector: 'app-claims',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (!claims().length) {
      <p class="empty">No verified content for this section.</p>
    }
    <ul class="claims">
      @for (claim of claims(); track claim.claim_id) {
        <li>
          <span class="text">{{ claim.text }}</span>
          @if (claim.citation_chunk_ids.length || claim.metric_ids.length) {
            <details class="cite">
              <summary>
                Sources ({{ claim.citation_chunk_ids.length + claim.metric_ids.length }})
              </summary>
              @for (chunkId of claim.citation_chunk_ids; track chunkId) {
                @if (chunk(chunkId); as found) {
                  <div class="chunk">
                    <div class="chunk-head">
                      <strong>{{ found.form_type }} — {{ found.section }}</strong>
                      <span class="period">{{ found.fiscal_period }}</span>
                      <a [href]="found.source_url" target="_blank" rel="noopener">EDGAR ↗</a>
                    </div>
                    <pre>{{ found.text.slice(0, 1200) }}</pre>
                  </div>
                } @else {
                  <div class="chunk missing"><code>{{ chunkId }}</code> (source text not loaded)</div>
                }
              }
              @for (metricId of claim.metric_ids; track metricId) {
                <div class="metric-ref">Computed metric: <code>{{ metricId }}</code></div>
              }
            </details>
          }
        </li>
      }
    </ul>
  `,
  styles: `
    .empty { color: var(--muted); font-style: italic; }
    .claims { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.7rem; }
    .claims li { background: var(--panel-2); border-radius: 8px; padding: 0.7rem 0.9rem; }
    .text { line-height: 1.5; }
    .cite { margin-top: 0.5rem; }
    .cite summary { cursor: pointer; color: var(--accent); font-size: 0.85rem; }
    .chunk { margin: 0.6rem 0; padding: 0.6rem; background: var(--panel); border-radius: 6px; }
    .chunk-head { display: flex; gap: 0.8rem; align-items: baseline; font-size: 0.85rem; }
    .chunk-head a { color: var(--accent); }
    .period { color: var(--muted); }
    .chunk pre { white-space: pre-wrap; font-size: 0.8rem; color: var(--muted); margin: 0.4rem 0 0; }
    .chunk.missing { color: var(--muted); font-size: 0.85rem; }
    .metric-ref { font-size: 0.85rem; color: var(--muted); margin-top: 0.4rem; }
  `,
})
export class ClaimsComponent {
  readonly claims = input.required<Claim[]>();
  readonly evidence = input<Record<string, EvidenceChunk>>({});

  chunk(id: string): EvidenceChunk | undefined {
    return this.evidence()[id];
  }
}
