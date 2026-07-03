/** API contracts â€” mirror src/api/schemas.py and src/report/schema.py. */

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
  mitigation: string;
  monitoring_metrics: string[];
  claims: Claim[];
}

export interface PeerMultiple {
  ticker: string;
  pe_ttm: number | null;
  price_to_sales: number | null;
  ev_to_ebitda: number | null;
  price_to_book: number | null;
  sector: string;
}

export interface ValuationSection {
  price: number | null;
  market_cap: number | null;
  pe_ttm: number | null;
  forward_pe: number | null;
  ev_to_ebitda: number | null;
  price_to_sales: number | null;
  price_to_book: number | null;
  beta: number | null;
  target_mean: number | null;
  target_high: number | null;
  target_low: number | null;
  recommendation: string;
  recommendation_mean: number | null;
  num_analysts: number;
  short_percent_float: number | null;
  short_ratio: number | null;
  shares_short: number | null;
  float_shares: number | null;
  short_interest_date: string;
  dividend_yield: number | null;
  payout_ratio: number | null;
  peers: PeerMultiple[];
  commentary: Claim[];
}

export interface EarningsQualitySection {
  accruals_ratio: number | null;
  cash_conversion: number | null;
  quality_label: string;
  flags: string[];
  commentary: Claim[];
}

export interface InsiderTransaction {
  name: string;
  title: string;
  transaction_type: string;
  shares: number;
  value: number | null;
  date: string;
}

export interface InsiderActivitySection {
  available: boolean;
  sentiment: string;
  net_shares: number;
  net_value: number;
  transactions: InsiderTransaction[];
  commentary: Claim[];
}

export interface ScorecardDimension {
  name: string;
  score: number;
  rationale: string;
  metric_ids: string[];
}

export interface InvestmentScorecardSection {
  available: boolean;
  dimensions: ScorecardDimension[];
  composite_score: number;
  composite_label: string;
}

export interface InstitutionalExecutiveSummary {
  investment_rating: string;
  confidence_score: number;
  investment_horizon: string;
  key_bull_thesis: string[];
  key_bear_thesis: string[];
  top_catalysts: string[];
  top_risks: string[];
  expected_return_range: string;
}

export interface QualitativeAnalysisSection {
  score: number | null;
  summary: string[];
  data_unavailable: string[];
}

export interface ValuationCase {
  name: string;
  intrinsic_value: number | null;
  expected_return_pct: number | null;
  assumptions: string[];
  status: string;
}

export interface DCFAnalysisSection {
  base_case: ValuationCase;
  bull_case: ValuationCase;
  bear_case: ValuationCase;
  reverse_dcf: string;
  margin_of_safety: string;
}

export interface InvestmentThesisSection {
  bull_case: string[];
  base_case: string[];
  bear_case: string[];
  probability_weighted_outcome: string;
  monitoring_metrics: string[];
  upgrade_triggers: string[];
  downgrade_triggers: string[];
  exit_triggers: string[];
}

export interface ReportQualityChecks {
  claim_verification: string;
  rating_scale: string;
  source_policy: string;
  stale_data_policy: string;
  unavailable_policy: string;
  final_scorecard: Record<string, number | string>;
}

export interface ReportMetadata {
  run_id: string;
  generated_at: string;
  duration_s: number;
  tokens_used: number;
  cost_usd: number;
  model_versions: Record<string, string>;
  warnings: string[];
  partial: boolean;
}

export interface DDReport {
  company: { name: string; ticker: string; cik: string; sector: string; industry: string };
  institutional_summary: InstitutionalExecutiveSummary;
  executive_summary: Claim[];
  business_overview: Claim[];
  business_quality: QualitativeAnalysisSection;
  management_analysis: QualitativeAnalysisSection;
  segment_analysis: QualitativeAnalysisSection;
  financial_health: { table: { metrics: MetricValue[] }; commentary: Claim[] };
  valuation: ValuationSection;
  dcf_analysis: DCFAnalysisSection;
  earnings_quality: EarningsQualitySection;
  industry_analysis: QualitativeAnalysisSection;
  insider_activity: InsiderActivitySection;
  scorecard: InvestmentScorecardSection;
  risk_matrix: RiskEntry[];
  recent_developments: Claim[];
  red_flags: Claim[];
  investment_thesis: InvestmentThesisSection;
  management_questions: string[];
  quality_checks: ReportQualityChecks;
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

