/** 财报趋势/健康分析（客户端复刻 data/financial_report.py 的 analyze_* 纯函数，保口径一致） */
import type { Financials } from '../../types/api'
import { toNum } from '../../lib/format'

export type HealthLevel = 'safe' | 'normal' | 'warning' | 'danger'

export interface HealthDetail {
  name: string
  value: number
  desc: string
  level: HealthLevel
}

export function analyzeGrowthTrend(
  growthRates: Financials['growth_rates'] | undefined,
): { trend: string; desc: string } {
  const rev = (growthRates?.revenue_growth ?? []).map(toNum)
  const np = (growthRates?.net_profit_growth ?? []).map(toNum)
  if (!rev.length && !np.length) return { trend: '数据不足', desc: '暂无足够的连续数据来判断成长趋势' }

  const analysis: string[] = []
  const push = (arr: (number | null)[]) => {
    if (arr.length >= 2) {
      const recent = arr[arr.length - 1] ?? 0
      const prev = arr[arr.length - 2] ?? 0
      if (recent > 0 && prev > 0) {
        if (recent > prev * 1.2) analysis.push(`加速增长（${prev.toFixed(1)}%→${recent.toFixed(1)}%）`)
        else if (recent > prev * 0.8) analysis.push(`稳定增长（${prev.toFixed(1)}%~${recent.toFixed(1)}%）`)
        else analysis.push(`减速增长（${prev.toFixed(1)}%→${recent.toFixed(1)}%）`)
      } else if (recent > 0 && prev <= 0) analysis.push(`扭亏为盈（${prev.toFixed(1)}%→${recent.toFixed(1)}%）`)
      else if (recent <= 0 && prev > 0) analysis.push(`由增转降（${prev.toFixed(1)}%→${recent.toFixed(1)}%）`)
      else analysis.push(`持续负增长（${prev.toFixed(1)}%~${recent.toFixed(1)}%）`)
    }
  }
  push(rev as (number | null)[])
  push(np as (number | null)[])
  if (!analysis.length) return { trend: '数据不足', desc: '暂无足够的连续数据来判断成长趋势' }

  const all = [...(rev as (number | null)[]), ...(np as (number | null)[])].filter(
    (g): g is number => g != null,
  )
  const allPositive = all.every((g) => g > 0)
  const allNegative = all.every((g) => g < 0)
  const trend = allPositive ? '正向增长' : allNegative ? '持续下滑' : '波动较大'
  return { trend, desc: analysis.join('；') }
}

export function analyzeProfitTrend(
  history: Financials['history'] | undefined,
): { trend: string; desc: string } {
  const gms = (history?.gross_margin ?? []).map(toNum)
  const nms = (history?.net_margin ?? []).map(toNum)
  const roes = (history?.roe ?? []).map(toNum)
  if (!gms.length && !nms.length) return { trend: '数据不足', desc: '暂无足够的连续数据来判断盈利趋势' }

  const analysis: string[] = []
  const pushTrend = (label: string, arr: (number | null)[]) => {
    const valid = arr.filter((v): v is number => v != null)
    if (valid.length >= 2) {
      if (valid[valid.length - 1] > valid[0] * 1.05) analysis.push(`${label}提升（${valid[0].toFixed(1)}%→${valid[valid.length - 1].toFixed(1)}%）`)
      else if (valid[valid.length - 1] < valid[0] * 0.95) analysis.push(`${label}下降（${valid[0].toFixed(1)}%→${valid[valid.length - 1].toFixed(1)}%）`)
      else analysis.push(`${label}基本稳定（${valid[0].toFixed(1)}%~${valid[valid.length - 1].toFixed(1)}%）`)
    }
  }
  pushTrend('毛利率', gms)
  pushTrend('净利率', nms)
  pushTrend('ROE', roes)
  if (!analysis.length) return { trend: '数据不足', desc: '暂无足够的连续数据来判断盈利趋势' }

  const improving = analysis.filter((a) => a.includes('提升')).length
  const declining = analysis.filter((a) => a.includes('下降')).length
  const trend =
    improving >= 2 ? '盈利能力提升' : declining >= 2 ? '盈利能力下降' : improving >= 1 && declining >= 1 ? '盈利能力波动' : '盈利能力稳定'
  return { trend, desc: analysis.join('；') }
}

export function analyzeFinancialHealth(
  financials: Financials | undefined,
): { health: string; details: HealthDetail[]; risks: string[] } {
  const details: HealthDetail[] = []
  const risks: string[] = []
  const latest = financials?.latest ?? {}
  const toNumV = (v: unknown) => toNum(v)

  const push = (name: string, value: number, desc: string, level: HealthLevel) =>
    details.push({ name, value, desc, level })

  // 1. 资产负债率
  const debtRatio = toNumV(latest.debt_ratio)
  if (debtRatio != null) {
    if (debtRatio < 30) push('资产负债率', debtRatio, '较低，财务杠杆保守', 'safe')
    else if (debtRatio < 50) push('资产负债率', debtRatio, '适中，财务结构合理', 'normal')
    else if (debtRatio < 70) {
      push('资产负债率', debtRatio, '偏高，需关注偿债能力', 'warning')
    } else {
      push('资产负债率', debtRatio, '过高，财务风险较大', 'danger')
      risks.push(`资产负债率过高（${debtRatio.toFixed(1)}%）`)
    }
  }

  // 2. 有息负债率
  const idr = toNumV(latest.interest_debt_ratio)
  if (idr != null) {
    if (idr < 10) push('有息负债率', idr, '很低，几乎没有有息负债', 'safe')
    else if (idr < 30) push('有息负债率', idr, '适中，债务负担可控', 'normal')
    else if (idr < 50) {
      push('有息负债率', idr, '偏高，利息支出压力较大', 'warning')
    } else {
      push('有息负债率', idr, '过高，债务风险较大', 'danger')
      risks.push(`有息负债率过高（${idr.toFixed(1)}%）`)
    }
  }

  // 3. 现金流
  const cfToProfit = toNumV(latest.cf_to_profit)
  const operatingCf = toNumV(latest.operating_cf)
  const netProfit = toNumV(latest.net_profit)
  if (cfToProfit != null) {
    if (cfToProfit > 100) push('经营现金流/净利润', cfToProfit, '现金流充裕，盈利质量好', 'safe')
    else if (cfToProfit > 50) push('经营现金流/净利润', cfToProfit, '现金流正常，盈利质量一般', 'normal')
    else if (cfToProfit > 0) push('经营现金流/净利润', cfToProfit, '现金流偏弱，盈利质量需关注', 'warning')
    else {
      push('经营现金流/净利润', cfToProfit, '现金流为负，盈利质量差', 'danger')
      risks.push('经营现金流为负，盈利质量差')
    }
  } else if (operatingCf != null && netProfit != null) {
    if (operatingCf > 0 && netProfit > 0) push('经营现金流', operatingCf, '经营现金流为正，但无法计算比率', 'normal')
    else if (operatingCf < 0 && netProfit > 0) {
      push('经营现金流', operatingCf, '经营现金流为负，利润含金量低', 'warning')
      risks.push('经营现金流为负，利润含金量低')
    }
  }

  // 4. 应收账款/营收
  const ar = toNumV(latest.accounts_receivable)
  const revenue = toNumV(latest.revenue)
  if (ar != null && revenue != null && revenue !== 0) {
    const ratio = (ar / revenue) * 100
    if (ratio < 10) push('应收账款/营收', ratio, '应收账款占比低，回款能力强', 'safe')
    else if (ratio < 30) push('应收账款/营收', ratio, '应收账款占比适中', 'normal')
    else if (ratio < 50) {
      push('应收账款/营收', ratio, '应收账款占比较高，回款风险增加', 'warning')
    } else {
      push('应收账款/营收', ratio, '应收账款占比过高，坏账风险大', 'danger')
      risks.push(`应收账款占比过高（${ratio.toFixed(1)}%）`)
    }
  }

  // 5. 存货/营收
  const inv = toNumV(latest.inventory)
  if (inv != null && revenue != null && revenue !== 0) {
    const ratio = (inv / revenue) * 100
    if (ratio < 15) push('存货/营收', ratio, '存货占比低，周转良好', 'safe')
    else if (ratio < 30) push('存货/营收', ratio, '存货占比适中', 'normal')
    else if (ratio < 50) {
      push('存货/营收', ratio, '存货占比较高，需关注周转', 'warning')
    } else {
      push('存货/营收', ratio, '存货占比过高，跌价风险大', 'danger')
      risks.push(`存货占比过高（${ratio.toFixed(1)}%）`)
    }
  }

  const dangerCount = details.filter((d) => d.level === 'danger').length
  const warningCount = details.filter((d) => d.level === 'warning').length
  const health = dangerCount >= 2 ? '风险较高' : dangerCount >= 1 || warningCount >= 3 ? '存在风险' : warningCount >= 1 ? '一般' : '健康'
  return { health, details, risks }
}