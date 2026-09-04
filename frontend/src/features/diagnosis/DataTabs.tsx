import type { DiagnosisPayload } from '../../types/api'
import { fmtNum, fmtPct, fmtYi, toNum } from '../../lib/format'
import {
  analyzeFinancialHealth,
  analyzeGrowthTrend,
  analyzeProfitTrend,
} from './financialAnalysis'
import {
  Collapse,
  DotList,
  InfoTable,
  MetricGrid,
  ScoreBar,
  Section,
} from './ui'
import { Card, RatingBanner } from '../../components/ui/primitives'

/** Tab 基本信息面（对标 _render_fundamental_tab） */
export function FundamentalTab({ d }: { d: DiagnosisPayload }) {
  const fundRaw = d.fundamentals ?? {}
  const fs = d.fundamental_score
  const adv = d.adv_risks
  const latest = d.financials?.latest ?? {}

  if (!fundRaw && !fs) {
    return <Card className="p-3 text-[13px] text-ink-2">暂无基本面数据（引擎已降级）</Card>
  }

  return (
    <div className="flex flex-col gap-2">
      <MetricGrid
        items={[
          { label: '营收（最新期）', value: fmtYi(latest.revenue) },
          { label: '净利润（最新期）', value: fmtYi(latest.net_profit) },
          { label: 'ROE', value: fmtPct(fundRaw.roe) },
          { label: '毛利率', value: fmtPct(fundRaw.gross_margin) },
        ]}
      />
      <p className="px-1 text-[11px] text-ink-3">
        口径：营收/净利取财报引擎最新一期；ROE/毛利率/增长率取基本面引擎最新年度（或近 3 年平均）摘要。
      </p>

      {fs ? (
        <>
          <RatingBanner
            label="基本面综合评分"
            value={`${fmtNum(fs.total_score, 0)}/100${fs.stars ? `　${'⭐'.repeat(fs.stars)}` : ''}`}
            color={fs.suggestion_color}
          />
          {fs.suggestion ? <p className="px-1 text-[13px] text-ink-2">{fs.suggestion}</p> : null}
          {Object.entries(fs.details ?? {}).map(([dimName, dim]) => (
            <Collapse key={dimName} title={`${dimName}：${fmtNum(dim.score, 0)}/${dim.max_score ?? 0}`}>
              <div className="flex flex-col gap-1.5">
                {(dim.items ?? []).map(([itemName, value, itemScore, desc], i) => (
                  <div key={i} className="rounded-tile bg-surface px-2 py-1.5">
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="text-[12px] font-medium text-ink">{itemName}</span>
                      <span className="num text-[11px] text-ink-3">
                        {fmtNum(value)}（{itemScore}/10）
                      </span>
                    </div>
                    <div className="text-[11.5px] text-ink-3">{desc}</div>
                  </div>
                ))}
                <ScoreBar
                  label="维度得分率"
                  score={dim.score ?? 0}
                  max={dim.max_score ?? 0}
                  note={dim.max_score && dim.max_score > 0 ? `${(((dim.score ?? 0) / dim.max_score) * 100).toFixed(0)}%` : undefined}
                />
              </div>
            </Collapse>
          ))}
        </>
      ) : null}

      {adv ? (
        <>
          <Section>优势</Section>
          <DotList tone="rise" items={adv.advantages ?? []} />
          <Section>风险</Section>
          <DotList tone="fall" items={adv.risks ?? []} />
          {adv.summary ? (
            <p className="rounded-tile border border-hairline bg-bg px-2.5 py-2 text-[12.5px] text-ink-2">
              <span className="text-ink-3">一句话总结：</span>
              <strong>{adv.summary}</strong>
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  )
}

/** Tab 排雷（对标 _render_minefield_tab） */
export function MinefieldTab({ d }: { d: DiagnosisPayload }) {
  const rr = d.risk_rating
  const riskItems = d.risk_items ?? []
  const safeItems = d.safe_items ?? []
  const missing = (d.minefield_results ?? []).filter((r) => !r.data_available)
  const advice = d.minefield_advice

  if (!d.minefield_results) {
    return <Card className="p-3 text-[13px] text-ink-2">暂无排雷数据（引擎已降级）</Card>
  }

  return (
    <div className="flex flex-col gap-2">
      {rr ? (
        <>
          <RatingBanner label="风险评级" value={rr.level ?? '--'} color={rr.level_color} sub={rr.summary} />
          <MetricGrid
            items={[
              { label: '高危', value: `${rr.high_count ?? 0}` },
              { label: '中危', value: `${rr.medium_count ?? 0}` },
              { label: '低危', value: `${rr.low_count ?? 0}` },
              {
                label: '已检查/触发',
                value: `${rr.total_checked ?? 0}/${rr.total_risks ?? 0}`,
              },
            ]}
          />
        </>
      ) : null}

      <Section>触发风险项（{riskItems.length}）</Section>
      {riskItems.length ? (
        <div className="flex flex-col gap-1.5">
          {riskItems.map((item, i) => (
            <Collapse key={i} title={`${item.icon ?? '⚠️'} ${item.name ?? ''}（${item.level ?? ''}风险）`}>
              <div className="flex flex-col gap-1 text-[12px] text-ink-2">
                {item.detail ? <div>数据：{item.detail}</div> : null}
                {item.explanation ? <div>说明：{item.explanation}</div> : null}
                {item.suggestion ? <div className="text-ink-3">建议：{item.suggestion}</div> : null}
              </div>
            </Collapse>
          ))}
        </div>
      ) : (
        <div className="px-1 text-[12.5px] text-rise">未触发任何风险项 ✅</div>
      )}

      {safeItems.length ? (
        <>
          <Section>安全项（{safeItems.length}）</Section>
          <Collapse title={`查看 ${safeItems.length} 项安全项明细`}>
            <DotList items={safeItems.map((s) => `${s.name ?? ''}：${s.detail ?? '--'}`)} />
          </Collapse>
        </>
      ) : null}

      {missing.length ? (
        <>
          <Section>数据缺失项（{missing.length}）</Section>
          <DotList items={missing.map((m) => `${m.name ?? ''}：${m.detail ?? '数据缺失'}`)} />
        </>
      ) : null}

      {advice ? (
        <>
          <Section>综合建议</Section>
          <div className="rounded-tile border border-hairline bg-bg px-2.5 py-2">
            <p className="text-[12.5px] whitespace-pre-wrap leading-relaxed text-ink-2">{advice.advice}</p>
            {(() => {
              const focusItems = advice.focus_items ?? []
              return focusItems.length ? (
                <ul className="mt-1 flex flex-col gap-0.5 text-[12px] text-ink-3">
                  {focusItems.map((f, i) => (
                    <li key={i}>{f}</li>
                  ))}
                </ul>
              ) : null
            })()}
          </div>
        </>
      ) : null}
    </div>
  )
}

/** Tab 护城河（对标 _render_moat_tab） */
export function MoatTab({ d }: { d: DiagnosisPayload }) {
  const ms = d.moat_scores
  if (!ms) {
    return <Card className="p-3 text-[13px] text-ink-2">暂无护城河数据（引擎已降级：行业对比 20 家较慢或数据源不可达）</Card>
  }

  const sorted = Object.entries(ms.scores ?? {}).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))

  return (
    <div className="flex flex-col gap-2">
      <RatingBanner
        label={`护城河总分 · ${ms.level ?? '--'}`}
        value={`${fmtNum(ms.total_score, 0)}/100`}
        color={ms.level_color}
        sub={ms.summary}
      />
      <div className="flex flex-col gap-1.5">
        {sorted.map(([name, score]) => {
          const detail = ms.details?.[name]
          return (
            <Collapse key={name} title={`${name}：${fmtNum(score, 0)}/10`}>
              <div className="flex flex-col gap-1">
                {(detail?.data_items ?? []).map((di, i) => (
                  <div key={i} className="text-[12px] text-ink-2">- {di}</div>
                ))}
                {(detail?.reasons ?? []).map((r, i) => (
                  <div key={i} className="border-l-2 border-hairline-strong pl-2 text-[12px] text-ink-3">
                    {r}
                  </div>
                ))}
              </div>
            </Collapse>
          )
        })}
      </div>

      <Section>优势（分项 ≥7）</Section>
      {ms.strengths?.length ? (
        <DotList items={ms.strengths.map((s) => `${s.name}（${fmtNum(s.score, 0)}/10）：${s.reason ?? ''}`)} />
      ) : (
        <div className="px-1 text-[12px] text-ink-3">无分项达到 7 分</div>
      )}

      <Section>不足（分项 &lt;4）</Section>
      {ms.weaknesses?.length ? (
        <DotList items={ms.weaknesses.map((w) => `${w.name}（${fmtNum(w.score, 0)}/10）：${w.reason ?? ''}`)} />
      ) : (
        <div className="px-1 text-[12px] text-ink-3">无分项低于 4 分</div>
      )}

      {ms.advice ? (
        <>
          <Section>建议</Section>
          <p className="px-1 text-[12.5px] leading-relaxed text-ink-2">{ms.advice}</p>
        </>
      ) : null}
    </div>
  )
}

/** 估值状态 → 颜色（低估绿 / 合理琥珀 / 高估橙红） */
function valueColor(status?: string): string {
  return (
    { 严重低估: '#2DBE64', 低估: '#2DBE64', 合理: '#E8B34B', 高估: '#FF922B', 严重高估: '#EF4444' } as Record<string, string>
  )[status ?? ''] ?? 'var(--text-3)'
}

/** Tab 估值（对标 _render_valuation_tab） */
export function ValuationTab({ d }: { d: DiagnosisPayload }) {
  const val = d.valuation
  const percentile = d.percentile
  const hasPct =
    percentile && (percentile.pe != null || percentile.pb != null)

  return (
    <div className="flex flex-col gap-2">
      {val ? (
        <>
          <MetricGrid
            items={[
              { label: '当前价格', value: `¥${fmtNum(val.price)}` },
              { label: '综合合理价', value: `¥${fmtNum(val.avg_fair_price)}` },
              {
                label: '价格/合理价',
                value: `${fmtNum(val.price_ratio, 1)}%`,
                tone: (val.price_ratio ?? 0) > 100 ? 'fall' : 'rise',
              },
              { label: '有效估值方法', value: `${val.valid_methods ?? 0}/${val.total_methods ?? 5}` },
            ]}
          />
          <RatingBanner
            label="估值结论"
            value={`${val.valuation_status ?? '--'}　（安全边际 ${fmtNum(val.margin_of_safety)}%）`}
            color={valueColor(val.valuation_status)}
            sub={`买入参考价 ¥${fmtNum(val.buy_price)} / 卖出参考价 ¥${fmtNum(val.sell_price)}；行情 60s 缓存，估值与东财同源、可能有延迟。`}
          />
        </>
      ) : (
        <Card className="p-3 text-[13px] text-ink-2">
          数据不足，无法估值（可能停牌或无行情数据）：估值引擎依赖实时行情 PE/PB，行情接口不可用时降级，以下是历史分位补算。
        </Card>
      )}

      <Section>历史分位（近 5 年，页面补算）</Section>
      {hasPct ? (
        <>
          <MetricGrid
            items={[
              { label: 'PE(TTM) 历史分位', value: percentile.pe != null ? `${percentile.pe}%` : '--' },
              { label: 'PB 历史分位', value: percentile.pb != null ? `${percentile.pb}%` : '--' },
            ]}
          />
          <p className="px-1 text-[11px] text-ink-3">
            近 5 年 {percentile?.n ?? 0} 个交易样本；数据截止 {percentile?.latest_date ?? '--'}，延迟 1 日。
            当前值百分位 = (≤现值的样本数 / 总数)。
          </p>
        </>
      ) : (
        <p className="px-1 text-[11px] text-ink-3">
          历史分位获取失败，以下各方法中的 PE 百分位为「相对合理 PE」口径（当前 PE ÷ 合理 PE × 100），非历史分位。
        </p>
      )}

      {val ? (
        <>
          <Section>五种估值方法</Section>
          {val.results?.length ? (
            <div className="flex flex-col gap-1.5">
              {val.results.map((r, i) => (
                <Collapse
                  key={i}
                  title={`${r.method_name ?? ''}｜合理价 ¥${fmtNum(r.fair_price)}${
                    r.deviation != null ? `（偏离 ${fmtNum(r.deviation)}%）` : ''
                  }`}
                >
                  <div className="flex flex-col gap-1 text-[12px] text-ink-2">
                    {r.applicable ? <div>适用范围：{r.applicable}</div> : null}
                    {r.formula ? <div className="mono whitespace-pre-wrap text-[11.5px] text-ink-3">公式：{r.formula}</div> : null}
                    {r.description ? <div>说明：{r.description}</div> : null}
                    {r.pe_percentile != null ? (
                      <div className="text-ink-3">PE 百分位（相对合理 PE 口径）：{fmtNum(r.pe_percentile)}%</div>
                    ) : null}
                  </div>
                </Collapse>
              ))}
            </div>
          ) : (
            <div className="px-1 text-[12px] text-ink-3">暂无有效估值方法结果（数据不足）</div>
          )}
        </>
      ) : null}
    </div>
  )
}

/** Tab 财报三表（对标 _render_reports_tab；勾稽/趋势/健康分析在客户端复刻计算） */
export function ReportsTab({ d }: { d: DiagnosisPayload }) {
  const reports = d.reports
  const financials = d.financials
  if (!reports || reports.error || !financials) {
    const msg = reports?.error ? `（${reports.error}）` : ''
    return <Card className="p-3 text-[13px] text-ink-2">暂无财报数据{msg}（引擎已降级）</Card>
  }

  const latest = financials.latest ?? {}
  const history = financials.history ?? {}
  const labels = history.labels ?? []

  const coreRows: [string, string][] = [
    ['营业收入', fmtYi(latest.revenue)],
    ['净利润', fmtYi(latest.net_profit)],
    ['扣非净利润', fmtYi(latest.deduct_profit)],
    ['毛利率', fmtPct(latest.gross_margin)],
    ['净利率', fmtPct(latest.net_margin)],
    ['ROE', fmtPct(latest.roe)],
    ['每股收益 EPS', fmtNum(latest.eps, 4)],
    ['资产负债率', fmtPct(latest.debt_ratio)],
    ['经营现金流', fmtYi(latest.operating_cf)],
    ['货币资金', fmtYi(latest.cash_equivalents)],
    ['存货', fmtYi(latest.inventory)],
    ['应收账款', fmtYi(latest.accounts_receivable)],
  ]

  // 勾稽检查：资产 = 负债 + 权益
  const ta = toNum(latest.total_assets)
  const tl = toNum(latest.total_liab)
  const eq = toNum(latest.equity)
  let balance: { ok: boolean; text: string } | null = null
  if (ta != null && tl != null && eq != null && ta !== 0) {
    const diff = ta - (tl + eq)
    const ratio = (Math.abs(diff) / Math.abs(ta)) * 100
    balance =
      ratio < 1
        ? { ok: true, text: `✅ 勾稽通过：资产 = 负债 + 权益（差异 ${ratio.toFixed(2)}%，属报告口径舍入误差）` }
        : { ok: false, text: `⚠️ 勾稽存在差异：资产 -（负债+权益）= ${fmtYi(diff)}（${ratio.toFixed(2)}%），可能系少数股东权益等报告口径差异所致` }
  }

  // 近 5 年趋势
  const revList = history.revenue ?? []
  const npList = history.net_profit ?? []
  const gmList = (history.gross_margin ?? []).map(toNum)
  const roeList = (history.roe ?? []).map(toNum)
  const maxLen = Math.max(labels.length, revList.length, npList.length, gmList.length, roeList.length)
  const trendRows: string[][] = []
  for (let i = 0; i < maxLen; i++) {
    const lbl = labels[i] ?? `第${i + 1}期`
    const rv = revList[i] ? revList[i] / 100000000 : null
    const np = npList[i] ? npList[i] / 100000000 : null
    trendRows.push([
      String(lbl),
      rv != null ? `${rv.toFixed(1)}亿` : '--',
      np != null ? `${np.toFixed(1)}亿` : '--',
      fmtPct(gmList[i]),
      fmtPct(roeList[i]),
    ])
  }

  const growth = analyzeGrowthTrend(financials.growth_rates)
  const profit = analyzeProfitTrend(history)
  const health = analyzeFinancialHealth(financials)
  const healthColors = { 健康: '#10B981', 一般: '#3B82F6', 存在风险: '#FBBF24', 风险较高: '#EF4444' } as Record<string, string>
  const healthIcons = { safe: '🟢', normal: '🔵', warning: '🟡', danger: '🔴' } as Record<string, string>

  return (
    <div className="flex flex-col gap-2">
      {labels[0] && labels[0] !== '0' ? (
        <p className="px-1 text-[11px] text-ink-3">最新报告期：{labels[0]}（财务摘要「按年度」口径）</p>
      ) : null}

      <Section>最新一期核心指标</Section>
      <InfoTable headers={['指标', '数值']} rows={coreRows} />

      <Section>勾稽检查</Section>
      {balance ? (
        <p className={`px-1 text-[12.5px] ${balance.ok ? 'text-rise' : 'text-ink-2'}`}>{balance.text}</p>
      ) : (
        <p className="px-1 text-[11.5px] text-ink-3">勾稽检查需资产/负债/权益三项齐全（当前数据缺失）</p>
      )}

      <Section>近 5 年趋势（营收/净利 · 毛利率/ROE）</Section>
      {trendRows.length ? (
        <InfoTable headers={['报告期', '营收(亿)', '净利(亿)', '毛利率', 'ROE']} rows={trendRows} />
      ) : (
        <div className="px-1 text-[12px] text-ink-3">暂无趋势数据</div>
      )}

      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <div className="rounded-tile border border-hairline bg-bg px-2.5 py-2">
          <div className="text-[12px] font-medium text-ink">📈 成长趋势</div>
          <div className="mt-0.5 text-[12.5px] text-ink-2">{growth.trend}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">{growth.desc}</div>
        </div>
        <div className="rounded-tile border border-hairline bg-bg px-2.5 py-2">
          <div className="text-[12px] font-medium text-ink">💰 盈利趋势</div>
          <div className="mt-0.5 text-[12.5px] text-ink-2">{profit.trend}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">{profit.desc}</div>
        </div>
        <div className="rounded-tile border border-hairline bg-bg px-2.5 py-2">
          <div className="text-[12px] font-medium text-ink">🛡️ 财务健康</div>
          <div className="mt-0.5 text-[12.5px]" style={{ color: healthColors[health.health] ?? 'var(--text-2)' }}>
            {health.health}
          </div>
          <div className="mt-0.5 flex flex-col gap-0.5 text-[11px] text-ink-3">
            {health.details.slice(0, 2).map((det) => (
              <span key={det.name}>
                {healthIcons[det.level] ?? '⚪'} {det.name}：{fmtNum(det.value)}
              </span>
            ))}
          </div>
        </div>
      </div>

      {health.details.length ? (
        <>
          <Section>财务健康明细</Section>
          <div className="flex flex-col gap-1">
            {health.details.map((det) => (
              <div key={det.name} className="flex items-baseline gap-2 px-1 text-[12px] text-ink-2">
                <span>{healthIcons[det.level] ?? '⚪'}</span>
                <span className="w-32 shrink-0 font-medium text-ink">{det.name}</span>
                <span className="num">{fmtNum(det.value)}</span>
                <span className="min-w-0 flex-1 text-ink-3">— {det.desc}</span>
              </div>
            ))}
          </div>
          {health.risks.length ? (
            <p className="px-1 text-[12px] text-fall">风险提示：{health.risks.join('；')}</p>
          ) : null}
        </>
      ) : null}

      {/* 汇报用时注记 */}
      {d.errors?.length ? (
        <p className="px-1 text-[11px] text-ink-3">部分引擎获取失败（{d.errors.length} 项），详见页顶降级提示。</p>
      ) : null}
    </div>
  )
}