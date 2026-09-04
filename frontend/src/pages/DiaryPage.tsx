import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import type { DiaryIn } from '../types/api'
import { fmtMoney } from '../lib/format'
import { Btn, Card, Field, Spinner, TextInput } from '../components/ui/primitives'

function today(): string {
  const d = new Date()
  const pad = (x: number) => String(x).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 投资日记（M2）：列表 / 新增 / 删除 —— 写后端 SQLite */
export function DiaryPage() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState<DiaryIn & { amountText: string }>({
    date: today(),
    fund_code: '',
    fund_name: '',
    action: '',
    amountText: '',
    note: '',
  })

  const { data: entries, isFetching, isError } = useQuery({
    queryKey: ['diary'],
    queryFn: api.diary.list,
    staleTime: 15_000,
  })

  const invalidate = () => void qc.invalidateQueries({ queryKey: ['diary'] })

  const addMut = useMutation({
    mutationFn: (body: DiaryIn) => api.diary.add(body),
    onSuccess: (r) => {
      if (!r.ok) window.alert(r.error ?? '写入失败')
      else {
        setOpen(false)
        setForm({ date: today(), fund_code: '', fund_name: '', action: '', amountText: '', note: '' })
      }
      invalidate()
    },
    onError: (e) => window.alert(`写入失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const delMut = useMutation({
    mutationFn: (id: number) => api.diary.remove(id),
    onSuccess: invalidate,
    onError: (e) => window.alert(`删除失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((old) => ({ ...old, [k]: e.target.value }))

  function submit() {
    const amount = Number(form.amountText || 0)
    addMut.mutate({
      date: form.date || today(),
      fund_code: form.fund_code?.trim() ?? '',
      fund_name: form.fund_name?.trim() ?? '',
      action: form.action?.trim() ?? '',
      amount: Number.isFinite(amount) ? amount : 0,
      note: form.note?.trim() ?? '',
    })
  }

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <div className="flex items-end justify-between gap-2">
        <div className="flex flex-col gap-1">
          <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">INVEST CONCIERGE</div>
          <h1 className="px-1 text-lg font-semibold text-ink">投资日记</h1>
          <p className="px-1 text-[13px] text-ink-2">交易记录与心得（增删直接写 SQLite）</p>
        </div>
        {!open ? (
          <Btn variant="primary" onClick={() => setOpen(true)}>
            <Plus size={13} /> 记一笔
          </Btn>
        ) : null}
      </div>

      {open ? (
        <Card className="p-3">
          <div className="mb-2 text-[12.5px] font-medium text-ink">新增日记</div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
            <Field label="日期">
              <TextInput type="date" value={form.date} onChange={set('date')} />
            </Field>
            <Field label="基金代码">
              <TextInput value={form.fund_code ?? ''} onChange={set('fund_code')} placeholder="如 110022" />
            </Field>
            <Field label="基金名称">
              <TextInput value={form.fund_name ?? ''} onChange={set('fund_name')} placeholder="可选" />
            </Field>
            <Field label="操作">
              <TextInput value={form.action ?? ''} onChange={set('action')} placeholder="买入 / 卖出 / 定投…" />
            </Field>
            <Field label="金额（元）">
              <TextInput value={form.amountText} onChange={set('amountText')} inputMode="decimal" placeholder="0.00" />
            </Field>
            <Field label="备注" className="col-span-2">
              <TextInput value={form.note ?? ''} onChange={set('note')} placeholder="心得 / 计划…" />
            </Field>
          </div>
          <div className="mt-2 flex gap-2">
            <Btn variant="primary" onClick={submit} disabled={addMut.isPending}>
              {addMut.isPending ? '写入中…' : '保存'}
            </Btn>
            <Btn onClick={() => setOpen(false)}>取消</Btn>
          </div>
        </Card>
      ) : null}

      {isFetching && !entries ? (
        <Card className="flex items-center gap-2 p-4 text-[13px] text-ink-2">
          <Spinner size={14} /> 加载日记…
        </Card>
      ) : null}
      {isError && !entries ? (
        <Card className="p-4 text-[13px] text-ink-2">日记加载失败（后端不可达？）—— 页面降级展示。</Card>
      ) : null}

      <div className="flex flex-col gap-1.5">
        {(entries ?? []).map((en) => (
          <div key={en.id} className="group flex items-start gap-3 rounded-tile border border-hairline bg-surface px-3 py-2">
            <div className="num w-24 shrink-0 text-[12px] text-ink-3">{en.date}</div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-2 text-[12.5px]">
                <span className="font-medium text-ink">{en.fund_name || '--'}</span>
                <span className="mono text-ink-3">{en.fund_code || ''}</span>
                {en.action ? <span className="rounded-tile bg-surface-2 px-1.5 py-px text-[11px] text-ink-2">{en.action}</span> : null}
                <span className="num text-ink-2">¥{fmtMoney(en.amount)}</span>
              </div>
              {en.note ? <div className="mt-0.5 text-[12px] text-ink-3">{en.note}</div> : null}
            </div>
            <button
              type="button"
              title="删除日记"
              onClick={() => {
                if (window.confirm(`确认删除 ${en.date} 的这条日记？`)) void delMut.mutate(en.id)
              }}
              className="shrink-0 cursor-pointer rounded-tile p-1 text-ink-3 opacity-0 transition-opacity hover:text-fall group-hover:opacity-100"
            >
              <Trash2 size={13} />
            </button>
          </div>
        ))}
        {!entries?.length && !isFetching ? (
          <Card className="p-4 text-center text-[12.5px] text-ink-3">暂无日记，点「记一笔」开始记录</Card>
        ) : null}
      </div>
    </section>
  )
}