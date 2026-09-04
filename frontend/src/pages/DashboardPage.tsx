import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { api } from '../lib/api'
import { fmtMoney } from '../lib/format'
import { Btn, Card, Kicker, Num, Spinner } from '../components/ui/primitives'
import { HoldingsTable } from '../features/holdings/HoldingsTable'
import { FundForm } from '../features/holdings/FundForm'
import type { FundIn } from '../types/api'

/** 资产总览（M2）：/api/dashboard/summary 指标卡 + 最近持仓（可快速增删） */
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
      <div className="flex flex-col gap-1">
        <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">INVEST CONCIERGE</div>
        <h1 className="px-1 text-lg font-semibold text-ink">资产总览</h1>
        <p className="px-1 text-[13px] text-ink-2">持仓资产、投入与占比总览 /api/dashboard/summary</p>
      </div>

      {/* 指标卡 */}
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Card className="px-4 py-3">
          <Kicker>持仓基金</Kicker>
          <div className="mt-1 text-[20px]">
            <Num value={`${summary?.fund_count ?? '--'} 只`} />
          </div>
        </Card>
        <Card className="px-4 py-3">
          <Kicker>总投入</Kicker>
          <div className="mt-1 text-[20px]">
            <Num value={`¥${fmtMoney(summary?.total_invest ?? '--')}`} />
          </div>
        </Card>
        <Card className="px-4 py-3">
          <Kicker>币种</Kicker>
          <div className="mt-1 text-[20px]">
            <Num value={summary?.currency ?? '--'} />
          </div>
        </Card>
        <Card className="px-4 py-3">
          <Kicker>状态</Kicker>
          <div className="mt-1 text-[20px]">
            <Num value={summary?.ok ? '正常' : '--'} />
          </div>
        </Card>
      </div>

      <div className="flex items-center justify-between">
        <Kicker>持仓明细（概览视图）</Kicker>
        {formMode == null ? (
          <Btn variant="primary" onClick={() => setFormMode('add')}>
            <Plus size={13} /> 新增持仓
          </Btn>
        ) : null}
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