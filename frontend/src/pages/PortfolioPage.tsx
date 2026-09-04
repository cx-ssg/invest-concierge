import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { api } from '../lib/api'
import type { FundHolding, FundIn } from '../types/api'
import { HoldingsTable } from '../features/holdings/HoldingsTable'
import { FundForm } from '../features/holdings/FundForm'
import { Btn, Card, Spinner } from '../components/ui/primitives'

/** 我的持仓（M2）：列表 / 新增 / 编辑 / 删除 —— 全部写后端 SQLite */
export function PortfolioPage() {
  const qc = useQueryClient()
  const [formMode, setFormMode] = useState<'add' | 'edit' | null>(null)
  const [editing, setEditing] = useState<FundHolding | null>(null)
  const [deletingCode, setDeletingCode] = useState<string | null>(null)

  const { data: funds, isFetching, isError } = useQuery({
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
      if (!r.ok) {
        window.alert(r.error ?? '添加失败')
        return
      }
      setFormMode(null)
      invalidate()
    },
    onError: (e) => window.alert(`添加失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const updateMut = useMutation({
    mutationFn: (v: { code: string; body: Partial<FundIn> }) => api.holdings.update(v.code, v.body),
    onSuccess: (r) => {
      if (!r.ok) {
        window.alert(r.error ?? '更新失败')
        return
      }
      setFormMode(null)
      setEditing(null)
      invalidate()
    },
    onError: (e) => window.alert(`更新失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const delMut = useMutation({
    mutationFn: (code: string) => api.holdings.remove(code),
    onSuccess: (r) => {
      setDeletingCode(null)
      if (!r.ok) window.alert(r.error ?? '删除失败')
      invalidate()
    },
    onError: (e) => {
      setDeletingCode(null)
      window.alert(`删除失败：${e instanceof Error ? e.message : String(e)}`)
    },
  })

  function onDelete(code: string) {
    if (!window.confirm(`确认删除持仓 ${code}？`)) return
    setDeletingCode(code)
    void delMut.mutate(code)
  }

  function onEdit(code: string) {
    const f = funds?.find((x) => x.code === code)
    if (!f) return
    setEditing(f)
    setFormMode('edit')
  }

  function onSubmit(v: FundIn) {
    if (formMode === 'edit' && editing) {
      void updateMut.mutate({ code: editing.code, body: v })
    } else {
      void addMut.mutate(v)
    }
  }

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <div className="flex items-end justify-between gap-2">
        <div className="flex flex-col gap-1">
          <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">INVEST CONCIERGE</div>
          <h1 className="px-1 text-lg font-semibold text-ink">我的持仓</h1>
          <p className="px-1 text-[13px] text-ink-2">基金持仓列表与盈亏（增删改直接写 SQLite）</p>
        </div>
        {formMode == null ? (
          <Btn variant="primary" onClick={() => setFormMode('add')}>
            <Plus size={13} /> 新增持仓
          </Btn>
        ) : null}
      </div>

      {formMode ? (
        <FundForm
          key={formMode === 'edit' ? `edit-${editing?.code ?? ''}` : 'add'}
          mode={formMode}
          initial={formMode === 'edit' ? editing : null}
          submitting={addMut.isPending || updateMut.isPending}
          onSubmit={onSubmit}
          onCancel={() => {
            setFormMode(null)
            setEditing(null)
          }}
        />
      ) : null}

      {isFetching && !funds ? (
        <Card className="flex items-center gap-2 p-4 text-[13px] text-ink-2">
          <Spinner size={14} /> 加载持仓…
        </Card>
      ) : null}

      {isError && !funds ? (
        <Card className="p-4 text-[13px] text-ink-2">
          持仓列表加载失败（后端不可达？）—— 页面降级展示，请启动 FastAPI 后重试。
        </Card>
      ) : null}

      {funds ? (
        <HoldingsTable
          funds={funds}
          onEdit={onEdit}
          onDelete={onDelete}
          deletingCode={deletingCode}
        />
      ) : null}
    </section>
  )
}