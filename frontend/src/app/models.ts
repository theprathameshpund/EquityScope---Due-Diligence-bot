/** API contracts — mirror src/api/schemas.py and src/report/schema.py. */

export interface CreateReportResponse {
  run_id: string;
  status: string;
}

export interface ProgressEvent {
  run_id: string;
  node: string;
  status: 'start' | 'end' | 'error' | 'warning' | 'done';
  message: string;
  tokens_used: number;
  cost_usd: number;
  ts: string;
}

export interface Claim {
  claim_id: string;
  text: string;
  citation_chunk_ids: string[];
  metric_ids: string[];
  verification_status: string;
  section: string;
}

export interface MetricValue {
  metric_id: string;
  name: string;
  value: number;
  unit: string;
  period: string;
  inputs: Record<string, number>;
  formula: string;
}

export interface RiskEntry {
  title: string;
  severity: 'low' | 'medium' | 'high';
  likelihood: 'low' | 'medium' | 'high';
  claims: Claim[];
}

export interface ReportMetadata {
  run_id: string;
  generated_at: string;
  duration_s: number;
  tokens_used: number;
  cost_usd: number;
  model_versions: Record<string, string>;
  warnings: string[];
}

export interface DDReport {
  company: { name: string; ticker: string; cik: string };
  executive_summary: Claim[];
  business_overview: Claim[];
  financial_health: { table: { metrics: MetricValue[] }; commentary: Claim[] };
  risk_matrix: RiskEntry[];
  recent_developments: Claim[];
  red_flags: Claim[];
  data_gaps: string[];
  metadata: ReportMetadata;
}

export interface EvidenceChunk {
  chunk_id: string;
  text: string;
  source_url: string;
  form_type: string;
  fiscal_period: string;
  section: string;
}

export interface ReportStatus {
  run_id: string;
  status: 'queued' | 'running' | 'done' | 'failed' | string;
  company: string;
  report: DDReport | null;
  markdown: string | null;
  evidence: EvidenceChunk[] | null;
  error: string | null;
}

export interface RunSummary {
  run_id: string;
  status: string;
  company: string;
  updated_at: string;
  cost_usd: number | null;
  tokens_used: number | null;
}

export interface Health {
  status: string;
  qdrant: boolean;
  postgres: boolean;
  redis: boolean;
}
