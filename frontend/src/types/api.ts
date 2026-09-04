/** 后端 API 类型（/api 契约，与 services 层字段对齐） */

export interface HealthPayload {
  status: string
  api_key_configured: boolean
  version: string
  ts: string
}

export interface IndexQuote {
  name: string
  code: string
  price: number
  change: number
  change_percent: number
}

export interface StatusPayload {
  engine: {
    state: string // ready | off
    api_key_configured: boolean
    chat_model: string
    reasoner_model: string
  }
  data_source: {
    ok: boolean
    indices: IndexQuote[]
    sentiment: string
  }
  last_refresh: string
  version: string
}

export interface NavPage {
  key: string
  label: string
}

export interface NavTrack {
  track: string // fund | stock | common
  label: string
  pages: NavPage[]
}

export interface NavPayload {
  tracks: NavTrack[]
}

export interface SummaryPayload {
  ok: boolean
  fund_count: number
  total_invest: number
  currency: string
}

// ==================== Agent / 会话 / SSE ====================

export interface AgentConfigPayload {
  api_key_configured: boolean
  chat_model: string
  reasoner_model: string
}

export interface SessionSummary {
  id: number
  title: string
  summary: string
  pinned: number | boolean
  archived: number | boolean
  created_at: string
  updated_at: string
}

export interface SessionMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ToolTraceEntry {
  name: string
  arguments: Record<string, unknown>
  output: string
}

/** SSE 事件（services/agent_service.stream_events 协议，FRONTEND_PLAN §5.1） */
export type SSEEvent =
  | { type: 'status'; state: string }
  | { type: 'reasoning'; text: string }
  | { type: 'writing'; text: string }
  | { type: 'tool'; text: string }
  | { type: 'tool_start'; name: string; arguments: Record<string, unknown> }
  | { type: 'tool_end'; name: string; ok: boolean; elapsed_ms: number }
  | { type: 'done'; session_id: number | null; content: string; tool_trace: ToolTraceEntry[] }
  | { type: 'error'; message: string }

export interface ChatBody {
  task: string
  session_id?: number | null
  context?: string[]
}

// ==================== 持仓 / 日记 ====================

export interface FundHolding {
  name: string
  code: string
  amount: number
  cost_nav: number
  hold_shares: number
  dwjz?: number | null
  gszzl?: number | null
  gztime?: string
}

export interface FundIn {
  code: string
  name?: string
  amount: number
  cost_nav: number
  hold_shares: number
  note?: string
}

export interface DiaryEntry {
  id: number
  date: string
  fund_code: string
  fund_name: string
  action: string
  amount: number
  note: string
}

export interface DiaryIn {
  date?: string
  fund_code?: string
  fund_name?: string
  action?: string
  amount?: number
  note?: string
}

export interface ApiOk {
  ok: boolean
  error?: string
}

// ==================== 设置 ====================

export interface SettingsPayload {
  ok: boolean
  api_key_configured: boolean
  version: string
  demo_mode_available: boolean
}

// ==================== 诊断（build_diagnosis_payload 6 引擎） ====================

export interface StockInfo {
  name?: string
  code?: string
  price?: number | null
  change?: number | null
  change_percent?: number | null
  [k: string]: unknown
}

export interface Fundamentals {
  roe?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  revenue_growth?: number | null
  profit_growth?: number | null
  pe?: number | null
  pb?: number | null
  dividend_rate?: number | null
  market_cap?: number | null
  [k: string]: unknown
}

export interface FundamentalScore {
  total_score?: number | null
  stars?: number | null
  suggestion?: string
  suggestion_color?: string
  details?: Record<string, FundamentalDim>
  [k: string]: unknown
}

export interface FundamentalDim {
  score?: number
  max_score?: number
  /** items = [item_name, value, item_score, desc] 元组列表 */
  items?: [string, number | null, number, string][]
  [k: string]: unknown
}

export interface AdvRisks {
  advantages?: string[]
  risks?: string[]
  summary?: string
}

export interface LatestFinancials {
  revenue?: number | null
  cost?: number | null
  net_profit?: number | null
  deduct_profit?: number | null
  gross_margin?: number | null
  net_margin?: number | null
  roe?: number | null
  eps?: number | null
  debt_ratio?: number | null
  total_assets?: number | null
  total_liab?: number | null
  equity?: number | null
  operating_cf?: number | null
  cash_equivalents?: number | null
  inventory?: number | null
  accounts_receivable?: number | null
  interest_debt_ratio?: number | null
  cf_to_profit?: number | null
  revenue_growth_rate?: number | null
  profit_growth_rate?: number | null
  [k: string]: unknown
}

export interface Financials {
  latest?: LatestFinancials
  history?: {
    labels?: string[]
    revenue?: number[]
    net_profit?: number[]
    deduct_profit?: number[]
    gross_margin?: (number | null)[]
    net_margin?: (number | null)[]
    roe?: number[]
    eps?: (number | null)[]
    debt_ratio?: (number | null)[]
    operating_cf?: number[]
    [k: string]: unknown
  }
  growth_rates?: {
    revenue_growth?: (number | null)[]
    net_profit_growth?: (number | null)[]
    [k: string]: unknown
  }
}

export interface MinefieldResultItem {
  name?: string
  level?: string
  icon?: string
  is_risk?: boolean
  data_available?: boolean
  detail?: string
  explanation?: string
  suggestion?: string
}

export interface RiskRating {
  level?: string
  level_color?: string
  summary?: string
  high_count?: number
  medium_count?: number
  low_count?: number
  total_risks?: number
  safe_count?: number
  total_checked?: number
}

export interface MinefieldAdvice {
  advice?: string
  focus_items?: string[]
}

export interface MoatScores {
  total_score?: number | null
  level?: string
  level_color?: string
  summary?: string
  scores?: Record<string, number | null>
  details?: Record<string, MoatDim>
  strengths?: { name?: string; score?: number | null; reason?: string }[]
  weaknesses?: { name?: string; score?: number | null; reason?: string }[]
  advice?: string
}

export interface MoatDim {
  data_items?: string[]
  reasons?: string[]
}

export interface ValuationResult {
  valid?: boolean
  method_name?: string
  applicable?: string
  fair_price?: number
  deviation?: number | null
  pe_percentile?: number | null
  formula?: string
  description?: string
  reason?: string
  [k: string]: unknown
}

export interface Valuation {
  price?: number | null
  valuation_status?: string
  valuation_color?: string
  suggestion?: string
  avg_fair_price?: number | null
  median_fair_price?: number | null
  price_ratio?: number | null
  margin_of_safety?: number | null
  buy_price?: number | null
  sell_price?: number | null
  valid_methods?: number
  total_methods?: number
  results?: ValuationResult[]
  [k: string]: unknown
}

export interface Percentile {
  pe?: number | null
  pb?: number | null
  n?: number
  latest_date?: string
}

/** GET /api/stocks/{code}/diagnosis —— build_diagnosis_payload 的 JSON 化形态 */
export interface DiagnosisPayload {
  ok: boolean
  code: string
  stock_info?: StockInfo | null
  fundamentals?: Fundamentals | null
  fundamental_score?: FundamentalScore | null
  adv_risks?: AdvRisks | null
  reports?: { error?: string | null; [k: string]: unknown } | null
  financials?: Financials | null
  minefield_results?: MinefieldResultItem[] | null
  risk_rating?: RiskRating | null
  risk_items?: MinefieldResultItem[] | null
  safe_items?: MinefieldResultItem[] | null
  minefield_advice?: MinefieldAdvice | null
  moat_scores?: MoatScores | null
  valuation?: Valuation | null
  percentile?: Percentile | null
  errors?: string[]
  error?: string
}