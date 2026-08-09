import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { Claim, EvidenceChunk } from '../models';
import { AppIcon } from './icon';

/** Verified findings with expandable source citations (editorial style). */
@Component({
  selector: 'app-claims',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppIcon],
  template: `
    @if (!claims().length) {
      <p class="empty">Insufficient data for this section.</p>
    }
    <ul class="claims">
      @for (claim of claims(); track claim.claim_id) {
        <li>
          <div class="row">
            <span class="check" title="Verified against cited sources">
              <app-icon name="check" [size]="11" />
            </span>
            <p class="text">{{ claim.text }}</p>
          </div>
          @if (claim.citation_chunk_ids.length || claim.metric_ids.length) {
            <details class="cite">
              <summary>
                Evidence ({{ claim.citation_chunk_ids.length + claim.metric_ids.length }})
              </summary>
              @for (chunkId of claim.citation_chunk_ids; track chunkId) {
                @if (chunk(chunkId); as found) {
                  <div class="chunk">
                    <div class="chunk-head">
                      <span class="form-badge">{{ found.form_type }}</span>
                      <strong>{{ found.section }}</strong>
                      <span class="period">{{ found.fiscal_period }}</span>
                      <a [href]="found.source_url" target="_blank" rel="noopener">
                        EDGAR <app-icon name="external" [size]="11" />
                      </a>
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
                  <app-icon name="activity" [size]="12" />
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
    .empty { color: var(--ink-3); font-style: italic; font-size: 0.86rem; }
    .claims { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.55rem; }
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
      margin-top: 0.18rem;
    }
    .text { margin: 0; line-height: 1.6; font-size: 0.92rem; color: var(--ink); max-width: 72ch; }

    .cite { margin: 0.5rem 0 0 1.7rem; }
    .cite summary {
      cursor: pointer; color: var(--brand-2); font-size: 0.78rem;
      user-select: none; width: fit-content;
    }
    .cite summary:hover { text-decoration: underline; }

    .chunk {
      margin: 0.55rem 0 0; padding: 0.75rem 0.9rem;
      background: var(--surface); border-radius: 8px;
      box-shadow: var(--shadow-rest);
    }
    .chunk-head { display: flex; gap: 0.7rem; align-items: center; font-size: 0.8rem; flex-wrap: wrap; }
    .form-badge {
      background: var(--brand-soft); color: var(--brand);
      border-radius: 5px; padding: 0.06rem 0.45rem;
      font-size: 0.68rem; font-weight: 650; font-family: var(--mono);
    }
    :host-context([data-theme="dark"]) .form-badge { color: var(--brand-2); }
    .chunk-head strong { color: var(--ink); font-weight: 600; }
    .period { color: var(--ink-3); font-family: var(--mono); font-size: 0.72rem; }
    .chunk-head a {
      margin-left: auto; font-size: 0.76rem;
      display: inline-flex; align-items: center; gap: 0.3rem;
    }
    blockquote {
      margin: 0.55rem 0 0; padding: 0 0 0 0.85rem;
      border-left: 2px solid var(--gold);
      white-space: pre-wrap;
      font-size: 0.79rem; color: var(--ink-2); line-height: 1.6;
      max-height: 14rem; overflow-y: auto;
    }
    .chunk.missing { color: var(--ink-3); font-size: 0.8rem; }
    .metric-ref {
      display: flex; align-items: center; gap: 0.45rem;
      font-size: 0.8rem; color: var(--ink-2); margin-top: 0.55rem;
    }
  `,
})
export class ClaimsComponent {
  readonly claims = input.required<Claim[]>();
  readonly evidence = input<Record<string, EvidenceChunk>>({});

  chunk(id: string): EvidenceChunk | undefined {
    return this.evidence()[id];
  }
}
