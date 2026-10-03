import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '../lib/api'
import { fmtNum, fmtPct } from '../lib/format'
import { Btn, Card, Num, Spinner, Tabs } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'
import { MetricGrid, Section } from '../features/diagnosis/ui'

/**
 * 市场行情（H5 · P2，依据 docs/AGENT_TOOLS_PLAN.md §P2）：
 * 指数 / 板块 / 情绪 / 资金 / 估值 五 tab——行情是"开着看"的，所以独立成页。
 * 数据源与 Agent 工具同一批引擎（/api/market/*）；`ok=false` 时如实显示"数据不可得"，
 * 绝不用假数据/占位填充（与 agent 工具的"数据不可得"守则一致）。
 */

const TAB_LIST = [
  { key: 'index', label: '指数' },
  { key: 'sectors', label: '板块' },
  { key: 'sentiment', label: '情绪' },
  { key: 'moneyflow', label: '资金' },
  { key: 'valuation', label: '估值' },
]

function tone(v: number | null | undefined): 'rise' | 'fall' | 'flat' {
  if (v == null || v === 0) return 'flat'
  return v > 0 ? 'rise' : 'fall'
}

/** 降级卡：数据源不可达时只说明事实 + 给重试，不填充假数据 */
function Unavailable({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <Card className="p-4">
      <div className="text-[13px] text-ink-2">{message}</div>
      <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
        数据源暂不可达（弱网 / 被代理拦截）。本页只展示真实数据，不做占位编造——稍后重试即可。
      </p>
      <div className="mt-2">
        <Btn onClick={onRetry}>
          <RefreshCw size={13} /> 重试
        </Btn>
      </div>
    </Card>
  )
}

function Loading({ label }: { label: string }) {
  return (
    <Card className="flex items-center gap-2 p-4 text-[13px] text-ink-2">
      <Spinner size={14} /> {label}
    </Card>
  )
}

/** 通用行情表：名称 / 代码 / 现价 / 涨跌额 / 涨跌幅（涨红跌绿） */
function QuoteTable({
  rows,
}: {
  rows: { name: string; code: string; price: number; change: number; change_percent: number }[]
}) {
  return (
    <div className="overflow-x-auto rounded-tile border border-hairline">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">名称</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">代码</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">现价</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">涨跌额</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code} className="hairline-b last:border-b-0 hover:bg-surface">
              <td className="px-2.5 py-1.5 text-ink">{r.name}</td>
              <td className="mono px-2.5 py-1.5 text-ink-2">{r.code}</td>
              <td className="px-2.5 py-1.5 text-right">
                <Num value={fmtNum(r.price)} tone={tone(r.change)} />
              </td>
              <td className="px-2.5 py-1.5 text-right">
                <Num value={fmtNum(r.change)} tone={tone(r.change)} />
              </td>
              <td className="px-2.5 py-1.5 text-right">
                <Num
                  value={`${r.change_percent >= 0 ? '+' : ''}${fmtPct(r.change_percent)}`}
                  tone={tone(r.change_percent)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ==================== 五个 tab ====================

function IndexTab() {
  const q = useQuery({ queryKey: ['market-index'], queryFn: ({ signal }) => api.market.index(signal), staleTime: 60_000 })
  if (q.isLoading) return <Loading label="加载指数行情…" />
  if (!q.data?.ok) {
    return <Unavailable message={q.data?.error ?? '指数行情请求失败'} onRetry={() => void q.refetch()} />
  }
  return (
    <div className="flex flex-col gap-2">
      <QuoteTable rows={q.data.indices} />
      <p className="px-1 text-[11px] text-ink-3">
        主要宽基指数（东财不可达时走腾讯行情同源兜底）；行情缓存 60 秒，涨红跌绿。
      </p>
    </div>
  )
}

function SectorsTab() {
  const q = useQuery({ queryKey: ['market-sectors'], queryFn: ({ signal }) => api.market.sectors(signal), staleTime: 5 * 60_000 })
  if (q.isLoading) return <Loading label="加载板块行情…" />
  if (!q.data?.ok) {
    return <Unavailable message={q.data?.error ?? '板块行情请求失败'} onRetry={() => void q.refetch()} />
  }
  return (
    <div className="overflow-x-auto rounded-tile border border-hairline">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">板块</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">代码</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">涨跌幅</th>
          </tr>
        </thead>
        <tbody>
          {q.data.sectors.map((s) => (
            <tr key={s.code || s.name} className="hairline-b last:border-b-0 hover:bg-surface">
              <td className="px-2.5 py-1.5 text-ink">{s.name}</td>
              <td className="mono px-2.5 py-1.5 text-ink-2">{s.code || '--'}</td>
              <td className="px-2.5 py-1.5 text-right">
                <Num value={`${s.change >= 0 ? '+' : ''}${fmtPct(s.change)}`} tone={tone(s.change)} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SentimentTab() {
  const q = useQuery({ queryKey: ['market-sentiment'], queryFn: ({ signal }) => api.market.sentiment(signal), staleTime: 5 * 60_000 })
  if (q.isLoading) return <Loading label="加载市场情绪…" />
  if (!q.data?.ok || !q.data.sentiment) {
    return <Unavailable message={q.data?.error ?? '市场情绪请求失败'} onRetry={() => void q.refetch()} />
  }
  const s = q.data.sentiment
  const lud = s.limit_up_down
  const b = s.breadth
  return (
    <div className="flex flex-col gap-2">
      <MetricGrid
        items={[
          { label: '涨停家数', value: lud ? String(lud.limit_up) : '--', tone: 'rise' },
          { label: '跌停家数', value: lud ? String(lud.limit_down) : '--', tone: 'fall' },
          { label: '最高连板', value: s.board_height != null ? `${s.board_height} 板` : '--' },
          { label: '上涨占比', value: b ? fmtPct(b.up_ratio, 1) : '--' },
        ]}
      />
      {b ? (
        <MetricGrid
          items={[
            { label: '上涨家数', value: String(b.up_count), tone: 'rise' },
            { label: '下跌家数', value: String(b.down_count), tone: 'fall' },
            { label: '总标的', value: String(b.total) },
            { label: '赚钱效应', value: fmtPct(b.up_ratio, 1), tone: tone(b.up_ratio - 50) },
          ]}
        />
      ) : null}
      {!lud || !b ? (
        <p className="px-1 text-[11.5px] text-ink-3">
          部分指标不可得（东财涨停/全市场快照被拦截）——上面为 "--" 的项即未取到真实值，不做估算。
        </p>
      ) : null}
      {s.errors.length ? (
        <div className="rounded-tile border border-hairline bg-bg px-3 py-2 text-[11.5px] text-ink-3">
          {s.errors.map((e, i) => (
            <div key={i}>- {e}</div>
          ))}
        </div>
      ) : null}
    </div>
  )
}

function MoneyflowTab() {
  const q = useQuery({ queryKey: ['market-moneyflow'], queryFn: ({ signal }) => api.market.moneyflow(signal), staleTime: 5 * 60_000 })
  if (q.isLoading) return <Loading label="加载资金流向…" />
  if (!q.data?.ok) {
    return <Unavailable message={q.data?.error ?? '资金流向请求失败'} onRetry={() => void q.refetch()} />
  }
  const m = q.data.moneyflow
  const yi = (v: number | null | undefined) => (v == null ? '--' : `${v >= 0 ? '+' : ''}${fmtNum(v)} 亿`)
  return (
    <div className="flex flex-col gap-3">
      {m ? (
        <>
          <MetricGrid
            items={[
              { label: '主力净流入', value: yi(m.main_flow), tone: tone(m.main_flow) },
              { label: '超大单', value: yi(m.super_large_flow), tone: tone(m.super_large_flow) },
              { label: '大单', value: yi(m.large_flow), tone: tone(m.large_flow) },
              { label: '中单', value: yi(m.medium_flow), tone: tone(m.medium_flow) },
            ]}
          />
          <MetricGrid
            items={[
              { label: '小单', value: yi(m.small_flow), tone: tone(m.small_flow) },
              { label: '北向资金', value: yi(m.north_flow), tone: tone(m.north_flow) },
              { label: '南向资金', value: yi(m.south_flow), tone: tone(m.south_flow) },
              { label: '更新时间', value: m.update_time || '--' },
            ]}
          />
        </>
      ) : null}
      <Section>行业板块主力资金（前 10）</Section>
      {q.data.sectors.length ? (
        <div className="overflow-x-auto rounded-tile border border-hairline">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr style={{ background: 'var(--surface-2)' }}>
                <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">板块</th>
                <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">涨跌幅</th>
                <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">主力净流入</th>
                <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">净占比</th>
              </tr>
            </thead>
            <tbody>
              {q.data.sectors.map((s) => (
                <tr key={s.name} className="hairline-b last:border-b-0 hover:bg-surface">
                  <td className="px-2.5 py-1.5 text-ink">{s.name}</td>
                  <td className="px-2.5 py-1.5 text-right">
                    <Num value={`${s.change >= 0 ? '+' : ''}${fmtPct(s.change)}`} tone={tone(s.change)} />
                  </td>
                  <td className="px-2.5 py-1.5 text-right">
                    <Num value={yi(s.main_flow)} tone={tone(s.main_flow)} />
                  </td>
                  <td className="px-2.5 py-1.5 text-right">
                    <Num value={fmtPct(s.main_flow_ratio)} tone={tone(s.main_flow_ratio)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="px-1 text-[12px] text-ink-3">板块资金榜不可得（数据源被拦截），上方大盘口径为真实值。</p>
      )}
    </div>
  )
}

function ValuationTab() {
  const q = useQuery({ queryKey: ['market-valuation'], queryFn: ({ signal }) => api.market.valuation(signal), staleTime: 5 * 60_000 })
  if (q.isLoading) return <Loading label="加载指数估值…" />
  if (!q.data?.ok) {
    return <Unavailable message={q.data?.error ?? '指数估值请求失败'} onRetry={() => void q.refetch()} />
  }
  const toneOfEva = (t: string) => (t === '低估' ? 'text-rise' : t === '高估' ? 'text-fall' : 'text-ink-2')
  return (
    <div className="overflow-x-auto rounded-tile border border-hairline">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">指数</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">PE</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">PE 分位</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">PB</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">PB 分位</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">估值状态</th>
          </tr>
        </thead>
        <tbody>
          {q.data.valuation.map((v) => (
            <tr key={v.code} className="hairline-b last:border-b-0 hover:bg-surface">
              <td className="px-2.5 py-1.5 text-ink">{v.name}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(v.pe)}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtPct(v.pe_percentile, 1)}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(v.pb)}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtPct(v.pb_percentile, 1)}</td>
              <td className={`px-2.5 py-1.5 ${toneOfEva(v.eva_type)}`}>{v.eva_type}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 市场行情页：五 tab 各自独立取数（切到哪个才拉哪个，弱网不互相拖累） */
export function MarketPage() {
  const [tab, setTab] = useState('index')

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="市场行情"
        desc="指数 / 板块 / 情绪 / 资金 / 估值 一屏速览（数据源与 AI 对话里的工具同一批引擎）"
      />

      <Tabs tabs={TAB_LIST} active={tab} onChange={setTab} />

      {tab === 'index' ? <IndexTab /> : null}
      {tab === 'sectors' ? <SectorsTab /> : null}
      {tab === 'sentiment' ? <SentimentTab /> : null}
      {tab === 'moneyflow' ? <MoneyflowTab /> : null}
      {tab === 'valuation' ? <ValuationTab /> : null}
    </section>
  )
}
