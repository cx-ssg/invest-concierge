/** 诊断 → Agent 追问上下文构造（客户端复刻 pages/stock_diagnosis.py 的 _build_stock_data） */
import type { DiagnosisPayload } from '../../types/api'
import { fmtNum, fmtPct, fmtYi, toNum } from '../../lib/format'

/** 去掉等级文案里的 emoji 前缀 */
function plainLevel(level?: string): string {
  if (!level) return '--'
  return level.replace(/^[✅🟢🟡🟠🔴]+\s*/u, '') || level
}

/** 元 → 亿 字符串（对齐 _fmt_num(market_cap, 0)） */
function marketCapText(marketCap: unknown): string {
  const n = toNum(marketCap)
  return n != null ? `${Number(n.toFixed(0)).toLocaleString('zh-CN')}亿` : '--'
}

/**
 * 构造 multi_agent_stock_analysis 同款 stock_data（全部字符串标量），
 * 作为 SSE chat 的 context（AI 追问与 AI 辩论共用）。
 */
export function buildStockData(d: DiagnosisPayload): Record<string, string> {
  const fundRaw = d.fundamentals ?? {}
  const rr = d.risk_rating ?? {}
  const ms = d.moat_scores ?? {}
  const val = d.valuation ?? {}
  const latest = d.financials?.latest ?? {}
  const stockInfo = d.stock_info ?? {}
  const percentile = d.percentile

  const price = toNum(stockInfo.price)
  const pePct =
    percentile && percentile.pe != null
      ? `PE 历史分位 ${percentile.pe}%（近5年）`
      : '历史分位获取失败，PE 为相对合理 PE 口径'

  return {
    现价: `¥${fmtNum(price)} / 市值 ${marketCapText(fundRaw.market_cap)}（行情 60s 缓存）`,
    基本面: `ROE ${fmtPct(fundRaw.roe)} / 毛利率 ${fmtPct(fundRaw.gross_margin)} / 净利率 ${fmtPct(
      fundRaw.net_margin,
    )} / 营收增速 ${fmtPct(fundRaw.revenue_growth)} / 净利增速 ${fmtPct(fundRaw.profit_growth)}`,
    估值: `PE ${fmtNum(fundRaw.pe)} / PB ${fmtNum(fundRaw.pb)} / 股息率 ${fmtPct(
      fundRaw.dividend_rate,
    )} / 综合估值: ${val.valuation_status ?? '--'}（合理价 ¥${fmtNum(val.avg_fair_price)}）；${pePct}`,
    排雷: `已查 ${rr.total_checked ?? '--'} 项，触发 ${rr.total_risks ?? 0} 项，风险评级: ${plainLevel(
      rr.level,
    )}`,
    护城河: `总分 ${ms.total_score ?? '--'}/100（${plainLevel(ms.level)}），最强: ${
      ms.strengths?.[0]?.name ?? '--'
    }`,
    财报: `营收 ${fmtYi(latest.revenue)} / 净利 ${fmtYi(latest.net_profit)} / 负债率 ${fmtPct(
      latest.debt_ratio,
    )} / 经营现金流 ${fmtYi(latest.operating_cf)}`,
  }
}

/** context 消息（对齐 _run_diag_follow_up 的拼装：股票 + 诊断数据 JSON + 记忆） */
export function buildDiagnosisContext(d: DiagnosisPayload, name: string): string[] {
  const code = d.code
  const stockData = buildStockData(d)
  return [
    `当前股票：${name ? `${name}（${code}）` : code}`,
    '当前诊断数据（页面 5 大引擎已拉取，AI 也可按需再调工具核实）：',
    JSON.stringify(stockData, null, 0),
  ]
}