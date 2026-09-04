import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { api } from '../lib/api'
import { fmtMoney } from '../lib/format'
import { Btn, Card, Kicker, Num, Spinner } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'
import { HoldingsTable } from '../features/holdings/HoldingsTable'
import { FundForm } from '../features/holdings/FundForm'
import type { FundIn, FundHolding } from '../types/api'

/** 资产总览：指标行（真实数据前端聚合）+ 最近持仓（可快速增删） */

/** 由持仓列表聚合指标：市值/今日盈亏/累计盈亏（净值缺失的基金不计入，显示降级 --） */
function aggregate(funds: FundHolding[] | undefined) {
  if (!funds?.length) return { marketValue: null, todayPnl: null, totalPnl: null, time: null }
  let mv = 0
  let mvKnown = false
  let todayPnl = 0
  for (const f of funds) {
    const shares = Number(f.hold_shares) || 0
    if (f.dwjz != null && Number(f.dwjz) > 0) {
      const v = shares * Number(f.dwjz)
      mv += v
      mvKnown = true
      const r = Number(f.gszzl)
      if (Number.isFinite(r) && r !== 0) {
        // 今日盈亏 ≈ 市值 - 昨收市值（gszzl 为涨跌幅，反推昨收）
        todayPnl += v - v / (1 + r / 100)
      }
    }
  }
  const invest = funds.reduce((s, f) => s + (Number(f.amount) || 0), 0)
  const gztimes = (funds ?? [])
    .map((f) => f.gztime ?? '')
    .filter(Boolean)
    .sort()
  const time = gztimes[gztimes.length - 1] ?? null
  return {
    marketValue: mvKnown ? mv : null,
    todayPnl: mvKnown ? todayPnl : null,
    totalPnl: mvKnown ? mv - invest : null,
    time,
  }
}

export function DashboardPage() {
  const qc = useQueryClient()
  const [formMode, setFormMode] = useState<null | 'add'>(null)
  const [deletingCode, setDeletingCode] = useState<string | null>(null)

  const { data: summary } = useQuery({
    queryKey: ['summary'],
    queryFn: api.summary,
    staleTime: 15_000,
  })
  const { data: funds, isFetching } = useQuery({
    queryKey: ['holdings'],
    queryFn: api.holdings.list,
    staleTime: 15_000,
  })

  const agg = aggregate(funds)

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['holdings'] })
    void qc.invalidateQueries({ queryKey: ['summary'] })
  }
  const addMut = useMutation({
    mutationFn: (v: FundIn) => api.holdings.add(v),
    onSuccess: (r) => {
      if (!r.ok) window.alert(r.error ?? '添加失败')
      else setFormMode(null)
      invalidate()
    },
  })
  const delMut = useMutation({
    mutationFn: (code: string) => api.holdings.remove(code),
    onSuccess: () => {
      setDeletingCode(null)
      invalidate()
    },
  })

  function onDelete(code: string) {
    if (!window.confirm(`确认删除持仓 ${code}？`)) return
    setDeletingCode(code)
    void delMut.mutate(code)
  }

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-4">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="资产总览"
        desc="查看账户资产、投入金额与组合占比"
        actions={
          formMode == null ? (
            <Btn variant="primary" onClick={() => setFormMode('add')}>
              <Plus size={13} /> 新增持仓
            </Btn>
          ) : null
        }
      />

      {/* 指标行（5 列 · 1px 分隔 · 真实数据聚合，缺失降级 --） */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-hairline bg-hairline md:grid-cols-5">
        <MetricCell label="总资产（投入）" value={`¥${fmtMoney(summary?.total_invest ?? '--')}`} />
        <MetricCell
          label="持仓市值"
          value={agg.marketValue != null ? `¥${fmtMoney(agg.marketValue)}` : '--'}
        />
        <MetricCell
          label="今日盈亏"
          value={agg.todayPnl != null ? `${agg.todayPnl >= 0 ? '+' : ''}${fmtMoney(agg.todayPnl)}` : '--'}
          tone={agg.todayPnl != null ? (agg.todayPnl > 0 ? 'rise' : agg.todayPnl < 0 ? 'fall' : 'flat') : undefined}
        />
        <MetricCell
          label="累计盈亏"
          value={agg.totalPnl != null ? `${agg.totalPnl >= 0 ? '+' : ''}${fmtMoney(agg.totalPnl)}` : '--'}
          tone={agg.totalPnl != null ? (agg.totalPnl > 0 ? 'rise' : agg.totalPnl < 0 ? 'fall' : 'flat') : undefined}
        />
        <MetricCell label="数据时间" value={agg.time ?? '未同步'} small />
      </div>

      <div className="flex items-center justify-between">
        <Kicker>持仓明细（概览视图）</Kicker>
      </div>

      {formMode ? (
        <FundForm
          mode="add"
          initial={null}
          submitting={addMut.isPending}
          onSubmit={(v) => void addMut.mutate(v)}
          onCancel={() => setFormMode(null)}
        />
      ) : null}

      {isFetching && !funds ? (
        <Card className="flex items-center gap-2 p-4 text-[13px] text-ink-2">
          <Spinner size={14} /> 加载持仓…
        </Card>
      ) : null}
      {funds ? (
        <HoldingsTable funds={funds} onEdit={() => undefined} onDelete={onDelete} deletingCode={deletingCode} compact />
      ) : null}
    </section>
  )
}

/** 指标单元格：标签 12px --text-3 + 数字 20px/600 等宽（UI_POLISH_PLAN §2.4） */
function MetricCell({
  label,
  value,
  tone,
  small = false,
}: {
  label: string
  value: string
  tone?: 'rise' | 'fall' | 'flat'
  small?: boolean
}) {
  return (
    <div className="bg-surface px-4 py-4">
      <div className="text-[12px] text-ink-3">{label}</div>
      <div className={`num mt-1 ${small ? 'text-[14px]' : 'text-[20px] font-semibold'} text-ink`}>
        {tone ? <Num value={value} tone={tone} /> : value}
      </div>
    </div>
  )
}
