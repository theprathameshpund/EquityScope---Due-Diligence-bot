import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Claim, EvidenceChunk } from '../models';

/** Verified claims rendered as cards with expandable source citations. */
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
                {{ claim.citation_chunk_ids.length + claim.metric_ids.length }}
                source{{ claim.citation_chunk_ids.length + claim.metric_ids.length === 1 ? '' : 's' }}
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
                  🧮 Computed deterministically from SEC XBRL — metric <code>{{ metricId }}</code>
                </div>
              }
            </details>
          }
        </li>
      }
    </ul>
  `,
  styles: `
    .empty { color: var(--faint); font-style: italic; font-size: 0.88rem; }
    .claims { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.65rem; }
    .claims li {
      background: var(--panel-2);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      padding: 0.75rem 0.95rem;
    }
    .row { display: flex; gap: 0.65rem; align-items: flex-start; }
    .check {
      flex-shrink: 0;
      width: 19px; height: 19px;
      display: grid; place-items: center;
      border-radius: 50%;
      background: var(--good-bg);
      color: var(--good);
      font-size: 0.7rem; font-weight: 800;
      margin-top: 0.18rem;
    }
    .text { margin: 0; line-height: 1.55; font-size: 0.93rem; }

    .cite { margin: 0.55rem 0 0 1.7rem; }
    .cite summary {
      cursor: pointer; color: var(--accent); font-size: 0.79rem;
      user-select: none;
    }
    .cite summary:hover { text-decoration: underline; }

    .chunk { margin: 0.6rem 0 0; padding: 0.7rem 0.85rem; background: var(--panel-solid); border-radius: 8px; border: 1px solid var(--border); }
    .chunk-head { display: flex; gap: 0.7rem; align-items: baseline; font-size: 0.82rem; flex-wrap: wrap; }
    .form-badge {
      background: rgba(91, 140, 255, 0.14); color: var(--accent);
      border-radius: 5px; padding: 0.05rem 0.45rem;
      font-size: 0.72rem; font-weight: 700;
    }
    .period { color: var(--faint); }
    .chunk-head a { margin-left: auto; font-size: 0.78rem; }
    blockquote {
      margin: 0.55rem 0 0; padding: 0 0 0 0.8rem;
      border-left: 2px solid var(--panel-3);
      white-space: pre-wrap;
      font-size: 0.8rem; color: var(--muted); line-height: 1.55;
      max-height: 14rem; overflow-y: auto;
    }
    .chunk.missing { color: var(--faint); font-size: 0.82rem; }
    .metric-ref { font-size: 0.82rem; color: var(--muted); margin-top: 0.55rem; }
  `,
})
export class ClaimsComponent {
  readonly claims = input.required<Claim[]>();
  readonly evidence = input<Record<string, EvidenceChunk>>({});

  chunk(id: string): EvidenceChunk | undefined {
    return this.evidence()[id];
  }
}
