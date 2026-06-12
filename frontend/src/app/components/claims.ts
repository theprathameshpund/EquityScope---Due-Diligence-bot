import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Claim, EvidenceChunk } from '../models';

/** Verified findings with expandable source citations (editorial style). */
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
          <div class="row">
            <span class="check" title="Verified against cited sources">✓</span>
            <p class="text">{{ claim.text }}</p>
          </div>
          @if (claim.citation_chunk_ids.length || claim.metric_ids.length) {
            <details class="cite">
              <summary>
                evidence ({{ claim.citation_chunk_ids.length + claim.metric_ids.length }})
              </summary>
              @for (chunkId of claim.citation_chunk_ids; track chunkId) {
                @if (chunk(chunkId); as found) {
                  <div class="chunk">
                    <div class="chunk-head">
                      <span class="form-badge">{{ found.form_type }}</span>
                      <strong>{{ found.section }}</strong>
                      <span class="period">{{ found.fiscal_period }}</span>
                      <a [href]="found.source_url" target="_blank" rel="noopener">View on EDGAR ↗</a>
                    </div>
                    <blockquote>{{ found.text.slice(0, 1200) }}</blockquote>
                  </div>
                } @else {
                  <div class="chunk missing">
                    <code>{{ chunkId }}</code> — source text not stored with this run
                  </div>
                }
              }
              @for (metricId of claim.metric_ids; track metricId) {
                <div class="metric-ref">
                  Computed deterministically from SEC XBRL — metric <code>{{ metricId }}</code>
                </div>
              }
            </details>
          }
        </li>
      }
    </ul>
  `,
  styles: `
    .empty { color: var(--ink-3); font-style: italic; font-size: 0.88rem; }
    .claims { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.6rem; }
    .claims li {
      background: var(--surface-2);
      border-radius: var(--radius-sm);
      padding: 0.8rem 1rem;
    }
    .row { display: flex; gap: 0.65rem; align-items: flex-start; }
    .check {
      flex-shrink: 0;
      width: 19px; height: 19px;
      display: grid; place-items: center;
      border-radius: 50%;
      background: var(--green-bg);
      color: var(--green);
      font-size: 0.68rem; font-weight: 800;
      margin-top: 0.2rem;
    }
    .text { margin: 0; line-height: 1.6; font-size: 0.93rem; color: var(--ink); }

    .cite { margin: 0.55rem 0 0 1.7rem; }
    .cite summary {
      cursor: pointer; color: var(--brand-2); font-size: 0.79rem;
      user-select: none;
    }
    .cite summary:hover { text-decoration: underline; }

    .chunk { margin: 0.6rem 0 0; padding: 0.75rem 0.9rem; background: var(--surface); border-radius: 8px; box-shadow: var(--shadow-rest); }
    .chunk-head { display: flex; gap: 0.7rem; align-items: baseline; font-size: 0.82rem; flex-wrap: wrap; }
    .form-badge {
      background: var(--brand-soft); color: var(--brand);
      border-radius: 5px; padding: 0.06rem 0.45rem;
      font-size: 0.7rem; font-weight: 700; font-family: var(--mono);
    }
    .chunk-head strong { color: var(--ink); }
    .period { color: var(--ink-3); font-family: var(--mono); font-size: 0.74rem; }
    .chunk-head a { margin-left: auto; font-size: 0.78rem; }
    blockquote {
      margin: 0.55rem 0 0; padding: 0 0 0 0.85rem;
      border-left: 2px solid var(--gold);
      white-space: pre-wrap;
      font-size: 0.8rem; color: var(--ink-2); line-height: 1.6;
      max-height: 14rem; overflow-y: auto;
    }
    .chunk.missing { color: var(--ink-3); font-size: 0.82rem; }
    .metric-ref { font-size: 0.82rem; color: var(--ink-2); margin-top: 0.55rem; }
  `,
})
export class ClaimsComponent {
  readonly claims = input.required<Claim[]>();
  readonly evidence = input<Record<string, EvidenceChunk>>({});

  chunk(id: string): EvidenceChunk | undefined {
    return this.evidence()[id];
  }
}
