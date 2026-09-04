import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, Search } from 'lucide-react'
import { api } from '../lib/api'
import type { FundHolding, FundIn } from '../types/api'
import { HoldingsTable } from '../features/holdings/HoldingsTable'
import { FundForm } from '../features/holdings/FundForm'
import { Btn, Card, Spinner } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'

/** 我的持仓：筛选（全部/盈利/亏损）+ 搜索 + 增删改（UI_POLISH_PLAN §2.5） */
export function PortfolioPage() {
  const qc = useQueryClient()
  const [formMode, setFormMode] = useState<'add' | 'edit' | null>(null)
  const [editing, setEditing] = useState<FundHolding | null>(null)
  const [deletingCode, setDeletingCode] = useState<string | null>(null)
  const [filter, setFilter] = useState<'all' | 'gain' | 'loss'>('all')
  const [search, setSearch] = useState('')

  const { data: funds, isFetching, isError } = useQuery({
    queryKey: ['holdings'],
    queryFn: api.holdings.list,
    staleTime: 15_000,
  })

  // 筛选 + 搜索（代码/名称模糊，大小写不敏感）
  const visible = useMemo(() => {
    let list = funds ?? []
    if (filter !== 'all') {
      list = list.filter((f) => {
        if (f.gszzl == null) return false
        return filter === 'gain' ? f.gszzl > 0 : f.gszzl < 0
      })
    }
    const kw = search.trim().toLowerCase()
    if (kw) {
      list = list.filter(
        (f) => f.code.toLowerCase().includes(kw) || (f.name ?? '').toLowerCase().includes(kw),
      )
    }
    return list
  }, [funds, filter, search])

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

  const FILTERS: { key: 'all' | 'gain' | 'loss'; label: string }[] = [
    { key: 'all', label: '全部' },
    { key: 'gain', label: '盈利' },
    { key: 'loss', label: '亏损' },
  ]

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="我的持仓"
        desc="管理持仓标的、成本与实时盈亏"
        actions={
          formMode == null ? (
            <>
              <Btn
                onClick={() => void qc.invalidateQueries({ queryKey: ['holdings'] })}
                title="刷新持仓与最新净值"
              >
                <RefreshCw size={13} className={isFetching ? 'animate-spin' : undefined} /> 刷新
              </Btn>
              <Btn variant="primary" onClick={() => setFormMode('add')}>
                <Plus size={13} /> 添加持仓
              </Btn>
            </>
          ) : null
        }
      />

      {/* 筛选工具条（40px：分段筛选 + 搜索，UI_POLISH_PLAN §2.5） */}
      <div className="flex h-10 items-center gap-3">
        <div className="flex rounded-tile border border-hairline bg-surface-2 p-0.5">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`cursor-pointer rounded-tile px-3 py-1 text-[12px] transition-colors ${
                filter === f.key ? 'bg-surface font-medium text-ink' : 'text-ink-3 hover:text-ink'
              }`}
              style={{ transitionDuration: 'var(--dur)' }}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex w-[220px] items-center gap-1.5 rounded-tile border border-hairline bg-surface px-2 py-1.5">
          <Search size={13} className="shrink-0 text-ink-3" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索证券代码或名称"
            className="w-full bg-transparent text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
        </div>
        <div className="flex-1" />
        <span className="text-[11px] text-ink-3">
          {visible.length === (funds ?? []).length
            ? `共 ${(funds ?? []).length} 只`
            : `${visible.length} / ${(funds ?? []).length} 只`}
        </span>
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
          持仓列表加载失败——页面降级展示，请启动后端后重试。
        </Card>
      ) : null}

      {funds ? <HoldingsTable funds={visible} onEdit={onEdit} onDelete={onDelete} deletingCode={deletingCode} /> : null}
    </section>
  )
}
