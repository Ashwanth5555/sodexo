import axios from 'axios'

export const api = axios.create({ baseURL: '/api' })

export function setAuthToken(token: string) {
  api.defaults.headers.common.Authorization = `Bearer ${token}`
}

export function clearAuthToken() {
  delete api.defaults.headers.common.Authorization
}

// A 401 from any endpoint means the session is gone (expired or revoked).
// Broadcast it so AuthProvider can sign out once, centrally, rather than every
// caller having to handle it. /auth/me is excluded: the provider already
// handles that response itself during the initial token check.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error?.config?.url ?? ''
    if (error?.response?.status === 401 && !url.includes('/auth/')) {
      window.dispatchEvent(new Event('sodexo:unauthorized'))
    }
    return Promise.reject(error)
  },
)

export interface IngestionBatch {
  source_system: string
  source_endpoint: string
  status: string
  records_received: number
  records_loaded: number
  records_rejected: number
  error_message: string | null
}

export async function runIngestion() {
  const { data } = await api.post<{ started_at: string; finished_at: string; batches: IngestionBatch[] }>('/ingestion/run')
  return data
}

export interface UploadDataset {
  key: string
  label: string
  source_system: string
  required_columns: string[]
  optional_columns: string[]
}

export interface UploadResult {
  dataset: string
  filename: string
  source_system: string
  status: string
  records_received: number
  records_loaded: number
  records_rejected: number
}

export async function fetchUploadDatasets() {
  const { data } = await api.get<{ datasets: UploadDataset[]; accepted_extensions: string[]; max_bytes: number }>('/ingestion/upload/datasets')
  return data
}

export async function uploadDataset(dataset: string, file: File) {
  const form = new FormData()
  form.append('dataset', dataset)
  form.append('file', file)
  const { data } = await api.post<UploadResult>('/ingestion/upload', form)
  return data
}

/** Header-only CSV for a dataset - the exact columns the loader expects. */
export function uploadTemplateUrl(dataset: string) {
  return `/api/ingestion/upload/template/${dataset}`
}

// --- Uploaded datasets: forecast any CSV -----------------------------------

export interface DatasetColumn {
  name: string
  dtype: string
  is_numeric: boolean
  is_date_like: boolean
  null_pct: number
  distinct: number
  samples: string[]
}

export interface DatasetSeriesRef {
  series_key: string
  series_label: string
}

export interface Dataset {
  dataset_id: string
  name: string
  original_filename: string
  status: 'uploaded' | 'configured'
  row_count: number
  date_column: string | null
  target_column: string | null
  group_column: string | null
  frequency: string | null
  aggregation: string
  horizon_periods: number
  exog_columns: string[]
  created_at: string | null
  series: DatasetSeriesRef[]
  column_profile?: DatasetColumn[]
}

export interface Option { value: string; label: string }

export interface DatasetUploadResponse extends Dataset {
  column_profile: DatasetColumn[]
  suggestion: { date_column: string | null; target_column: string | null; group_column: string | null; group_candidates: string[] }
  frequencies: Option[]
  aggregations: Option[]
  preview: Record<string, unknown>[]
}

export interface DatasetMapping {
  name?: string
  date_column: string
  target_column: string
  group_column?: string | null
  frequency: string
  aggregation: string
  horizon_periods: number
  exog_columns: string[]
}

export interface ConfigureResponse extends Dataset {
  series_count: number
  history_periods: number
  history_start: string
  history_end: string
}

export async function fetchDatasets() {
  const { data } = await api.get<{ datasets: Dataset[]; frequencies: Option[]; aggregations: Option[] }>('/datasets')
  return data
}

export async function uploadDatasetFile(file: File) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<DatasetUploadResponse>('/datasets/upload', form)
  return data
}

export async function configureDataset(datasetId: string, mapping: DatasetMapping) {
  const { data } = await api.post<ConfigureResponse>(`/datasets/${datasetId}/configure`, mapping)
  return data
}

export async function trainDataset(datasetId: string) {
  const { data } = await api.post<{ job_id: string; status: string }>(`/datasets/${datasetId}/train`)
  return data
}

export async function deleteDataset(datasetId: string) {
  const { data } = await api.delete<{ deleted: string }>(`/datasets/${datasetId}`)
  return data
}

export async function fetchIngestionStatus() {
  const { data } = await api.get<{ sources: (IngestionBatch & { started_at: string; completed_at: string | null })[] }>('/ingestion/status')
  return data.sources
}

export interface TrainingJobStatus {
  job_id: string
  status: 'RUNNING' | 'COMPLETED' | 'FAILED'
  started_at: string
  completed_at: string | null
  current_series: string | null
  current_model: string | null
  total_steps: number
  completed_steps: number
  error_message: string | null
}

export async function startTraining(windowDays?: number) {
  const { data } = await api.post<{ job_id: string; status: string; window_days: number | null }>('/models/train', null, { params: windowDays ? { window_days: windowDays } : {} })
  return data
}

export async function fetchTrainingStatus() {
  const { data } = await api.get<{ job: TrainingJobStatus | null }>('/models/training-status')
  return data.job
}

export interface ModelRunSummary {
  model_id: string
  model_label: string
  status: 'completed' | 'failed' | 'not_applicable' | 'pending'
  evaluation_mode: string | null
  folds: number | null
  train_rows: number | null
  test_rows: number | null
  mape: number | null
  rmse: number | null
  mae: number | null
  wape: number | null
  bias: number | null
  accuracy: number | null
  rank: number | null
  is_champion: boolean
  runtime_seconds: number | null
  failure_reason: string | null
  trained_at: string | null
}

export interface SeriesLeaderboard {
  series_key: string
  series_label: string
  models: ModelRunSummary[]
}

export async function fetchLeaderboard() {
  const { data } = await api.get<{ job_id: string | null; job_status: string | null; series: SeriesLeaderboard[] }>('/models/leaderboard')
  return data
}

export interface SeriesOption {
  series_key: string
  series_label: string
  /** 'built_in' = canonical schema series, 'uploaded' = from a user's file. */
  source?: 'built_in' | 'uploaded'
  dataset_id?: string
}

export async function fetchSeriesOptions() {
  const { data } = await api.get<{ series: SeriesOption[] }>('/forecast/series')
  return data.series
}

export interface ForecastPoint {
  date: string
  actual: number | null
  predicted: number | null
  lower: number | null
  upper: number | null
  is_future: boolean
  fold: number | null
}

export interface ForecastResponse {
  series_key: string
  model_id: string
  model_label: string
  is_champion: boolean
  /** What this series forecasts and at what grain - differs per uploaded dataset. */
  target_label?: string
  frequency_label?: string
  /** When this series was last trained - series are trained independently. */
  trained_at?: string | null
  evaluation_mode: string | null
  folds: number | null
  train_rows: number | null
  test_rows: number | null
  metrics: { mape: number | null; rmse: number | null; mae: number | null; wape: number | null; bias: number | null; accuracy: number | null }
  points: ForecastPoint[]
}

export async function fetchForecast(seriesKey: string, modelId?: string) {
  const { data } = await api.get<ForecastResponse>('/forecast', { params: { series_key: seriesKey, model_id: modelId } })
  return data
}

export interface InputColumnOption {
  column: string
  raw: string
  enabled: boolean
}

export interface DriverSeries {
  series_key: string
  series_label: string
  mapping: { target_column: string; date_time_grain: string; dimensions: string[]; frequency: string }
  exogenous_drivers_used: string[]
  available_input_columns: InputColumnOption[]
  primary_demand_signal: string
}

export interface DriversResponse {
  series: DriverSeries[]
  not_in_dataset: { name: string; note: string }[]
  note: string
}

export async function fetchDrivers() {
  const { data } = await api.get<DriversResponse>('/forecast/drivers')
  return data
}

export async function setInputColumnEnabled(seriesKey: string, column: string, enabled: boolean) {
  const { data } = await api.post<DriversResponse>('/forecast/drivers/selection', { series_key: seriesKey, column, enabled })
  return data
}

export interface PreprocessingResponse {
  series_key: string
  series_label: string
  total_history_days: number
  training_window_days: number
  resolved_seasonal_period_slots: number | null
  training_window_rows: number
  block_hours: number
  seasonal_period_label: string
  validation_plan: { mode: string; fold_validation_size_days: number; fold_count: number; note: string }
  input_dataset: { rows: number; columns: number }
  preview_columns: string[]
  preview_rows: Record<string, string | number>[]
  forecast_horizon_days: number
}

export async function fetchPreprocessing(seriesKey: string) {
  const { data } = await api.get<PreprocessingResponse>('/forecast/preprocessing', { params: { series_key: seriesKey } })
  return data
}

export interface EdaResponse {
  series_key: string
  series_label: string
  daily_trend: { date: string; actual: number; trend_7d: number }[]
  by_hour_of_day: Record<string, number>
  by_day_of_week: Record<string, number>
  season_effect: Record<string, number>
  holiday_lift_pct: number | null
  flag_effects: Record<string, { on: number | null; off: number | null; lift_pct: number | null }>
}

export async function fetchEda(seriesKey: string) {
  const { data } = await api.get<EdaResponse>('/forecast/eda', { params: { series_key: seriesKey } })
  return data
}

export interface ConfidencePoint {
  date: string
  forecast: number
  lower: number | null
  upper: number | null
  confidence_level: string
  reasons: string[]
}

export interface ConfidenceResponse {
  series_key: string
  model_id: string
  model_label: string
  points: ConfidencePoint[]
}

export async function fetchConfidence(seriesKey: string, modelId?: string) {
  const { data } = await api.get<ConfidenceResponse>('/forecast/confidence', { params: { series_key: seriesKey, model_id: modelId } })
  return data
}

export interface SensitivityPoint {
  date: string
  baseline_forecast: number | null
  scenario_forecast: number | null
  baseline_headcount: number | null
  scenario_headcount: number | null
  difference: number | null
}

export interface SensitivityResponse {
  series_key: string
  model_id: string
  model_label: string
  scenario: { demand_multiplier_pct: number; promotion: boolean | null; event: boolean | null }
  points: SensitivityPoint[]
}

export async function runSensitivity(seriesKey: string, demandMultiplierPct: number, promotion: boolean | null, event: boolean | null) {
  const { data } = await api.post<SensitivityResponse>('/forecast/sensitivity', {
    series_key: seriesKey, demand_multiplier_pct: demandMultiplierPct, promotion, event,
  })
  return data
}

export interface DriftResponse {
  series_key: string
  model_id: string
  model_label: string
  model_age_days: number | null
  historical_backtest_mape: number | null
  historical_backtest_bias: number | null
  feature_distribution: { signal: string; recent_7d_mean: number | null; prior_30d_mean: number | null; pct_change: number | null }
  demand_signal_name: string
  demand_signal_trend: { date: string; window: 'recent' | 'prior'; value: number }[]
  demand_signal_correlation: number | null
  demand_signal_strength: 'strong' | 'moderate' | 'weak' | 'unknown'
  seasonality_drift: { hour_of_day: number; recent: number | null; prior: number | null }[]
  status: string
}

export async function fetchDrift(seriesKey: string, modelId?: string) {
  const { data } = await api.get<DriftResponse>('/forecast/drift', { params: { series_key: seriesKey, model_id: modelId } })
  return data
}

export interface StaffingImpactPoint {
  date: string
  required_headcount: number
  scheduled_headcount: number
  gap: number
  status: string
}

export interface StaffingImpactResponse {
  series_key: string
  window_start?: string
  window_end?: string
  note: string
  points: StaffingImpactPoint[]
}

export async function fetchStaffingImpact(seriesKey: string, days = 14) {
  const { data } = await api.get<StaffingImpactResponse>('/forecast/staffing-impact', { params: { series_key: seriesKey, days } })
  return data
}

export interface AnalyticsFilters {
  locations: { location_id: string; location_name: string }[]
  departments: { department_id: string; department_name: string; cost_center_id: string }[]
  date_range: { min: string; max: string } | null
}

export async function fetchAnalyticsFilters() {
  const { data } = await api.get<AnalyticsFilters>('/analytics/filters')
  return data
}

export interface AnalyticsSummary {
  error?: string
  window: { start: string; end: string }
  previous_window: { start: string; end: string }
  kpi_deltas: Record<string, number | null>
  kpi_delta_units: Record<string, string>
  kpis: {
    total_labor_cost: number
    total_budget: number | null
    labor_cost_vs_budget_pct: number | null
    scheduled_hours: number
    actual_hours: number
    variance_hours: number
    overtime_hours: number
    overtime_cost: number
    overtime_budget: number | null
    attendance_rate_pct: number | null
    labor_utilization_pct: number | null
  }
  grain: string
  labor_cost: {
    by_location: { location_id: string; location_name: string; cost: number; scheduled_hours: number; actual_hours: number; overtime_hours: number }[]
    by_department: { department_id: string; department_name: string; cost_center_id: string; cost: number; scheduled_hours: number; actual_hours: number; overtime_hours: number }[]
    trend: { date: string; cost: number }[]
    by_location_over_time: Record<string, string | number>[]
    location_names: string[]
    by_cost_center_over_time: Record<string, string | number>[]
    cost_center_ids: string[]
  }
  scheduled_vs_actual: { trend: { date: string; scheduled: number; actual: number; variance: number }[] }
  overtime: {
    trend: { date: string; overtime_hours: number; overtime_cost: number }[]
    by_location: { location_id: string; location_name: string; overtime_hours: number }[]
    by_department: { department_id: string; department_name: string; overtime_hours: number }[]
    employees_near_overtime: string[]
  }
  attendance: {
    breakdown: Record<string, number>
    absenteeism_pct: number | null
    late_pct: number | null
    trend: { date: string; present_pct: number; absent_pct: number; late_pct: number }[]
  }
  efficiency: { trend: { date: string; utilization_pct: number | null; overtime_share_pct: number | null; cost_per_hour: number | null }[] }
  productivity: { location_id: string; department_id: string; signal: string; total_flow: number; total_labor_hours: number; flow_per_labor_hour: number | null }[]
  shift_utilization: { location_id: string; department_id: string; avg_scheduled_hours: number; avg_actual_hours: number; utilization_pct: number }[]
  manager_performance: {
    location_id: string; department_id: string; location_name: string; department_name: string
    overtime_control_pct: number | null; punch_exception_rate_pct: number | null; schedule_adherence_pct: number | null; labor_cost_vs_budget_pct: number | null
    labor_cost: number; actual_hours: number; headcount: number
    score: number | null; rank: number
    score_components: Record<string, number | null>
    metric_values: Record<string, number | null>
    best_metric: string | null
    worst_metric: string | null
    underspend_review: boolean
  }[]
  manager_scales: Record<string, { good: number; poor: number; target: number; higher_is_better: boolean }>
  underspend_review_pct: number
}

export async function fetchAnalyticsSummary(params: { location_id?: string; department_id?: string; start_date?: string; end_date?: string; grain?: string; last_days?: number }) {
  const { data } = await api.get<AnalyticsSummary>('/analytics/summary', { params })
  return data
}

export interface PunchAnomaly {
  id: number
  employee_id: string
  employee_name: string
  location_id: string | null
  location_name: string | null
  department_id: string | null
  department_name: string | null
  shift_date: string
  exception_type: string
  severity: string
  status: string
  problem: string
  expected_value: string
  actual_value: string
  variance: string
  rule_triggered: string
  root_cause: string
  potential_impact: string
  recommended_action: string
}

export interface PunchAnalyticsResponse {
  error?: string
  window: { start: string; end: string }
  kpis: {
    total_punches: number
    exception_rate_pct: number | null
    missed_punches: number
    duplicate_punches: number
    early_clock_ins: number
    late_clock_outs: number
    long_breaks: number
    manual_edits: number
    payroll_exceptions: number
    excess_scheduled_hours_employees: number
    suspicious_punch_patterns: number
  }
  anomalies: PunchAnomaly[]
  grain: string
  trend: { label: string; issues: number; punches?: number; issue_rate_pct?: number }[]
  alerts: { level: 'High' | 'Medium' | 'Low'; title: string; detail: string }[]
  watchlist: {
    employee_id: string; employee_name: string | null
    location_name: string | null; department_name: string | null
    recent: number; earlier: number; trend: string; reason: string
  }[]
}

export async function fetchPunchAnomalies(params: { location_id?: string; department_id?: string; start_date?: string; end_date?: string; grain?: string }) {
  const { data } = await api.get<PunchAnalyticsResponse>('/punch-analytics/anomalies', { params })
  return data
}

export interface Recommendation {
  id: string
  category: string
  priority: 'High' | 'Medium' | 'Low'
  location_department: string
  headline: string
  problem: string
  evidence: string[]
  recommended_action: string
  expected_impact: string
  estimated_savings: number | null
  savings_basis: string | null
  confidence: number
}

export async function fetchRecommendations() {
  const { data } = await api.get<{ recommendations: Recommendation[] }>('/recommendations')
  return data.recommendations
}

export async function askRecommendationQuestion(question: string) {
  const { data } = await api.post<{ question: string; answer: string; recommendations: Recommendation[] }>('/recommendations/ask', { question })
  return data
}

export async function fetchAssistantStatus() {
  const { data } = await api.get<{ llm_configured: boolean }>('/assistant/status')
  return data
}

export interface AssistantTurn {
  role: 'user' | 'assistant'
  content: string
}

export interface AssistantChart {
  type: 'line' | 'bar' | 'donut'
  title: string
  x_key?: string
  series: { key: string; label: string; color?: string }[]
  data: Record<string, string | number | null>[]
}

export interface AssistantScope {
  location_id: string | null
  location_name: string | null
  department_id: string | null
  department_name: string | null
}

export interface AssistantResponse {
  question: string
  answer: string
  tools_used: string[]
  routed_by?: 'model_agentic' | 'keyword'
  llm_used: boolean
  chart?: AssistantChart | null
  scope?: AssistantScope | null
}

/** `history` is the prior turns, oldest first, excluding the question being
 *  asked. Sending it is what lets a follow-up ("why?", "what about the cafe?")
 *  resolve against what was already being discussed. `currentScope` is the
 *  `scope` field from the previous response, round-tripped as-is - its shape
 *  is backend-defined, the frontend only stores and resends it. */
export async function askAssistant(question: string, history: AssistantTurn[] = [], currentScope?: AssistantScope | null) {
  const { data } = await api.post<AssistantResponse>('/assistant/ask', { question, history, current_scope: currentScope ?? null })
  return data
}

export interface DataQualitySource extends IngestionBatch {
  source_system: string
  source_endpoint: string
  started_at: string
  completed_at: string | null
  dataset_label: string
  freshness_hours: number | null
  quality_status: string
}

export interface DataQualitySummary {
  sources: DataQualitySource[]
  summary: { total_sources: number; total_records_received: number; total_records_rejected: number; overall_quality_status: string }
}

export async function fetchDataQualitySummary() {
  const { data } = await api.get<DataQualitySummary>('/data-quality/summary')
  return data
}
