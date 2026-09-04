import { useState } from 'react'
import { X } from 'lucide-react'
import type { FundHolding, FundIn } from '../../types/api'
import { Btn, Field, TextInput } from '../../components/ui/primitives'

export interface FundFormValue {
  code: string
  name: string
  amount: string
  cost_nav: string
  hold_shares: string
  note: string
}

/**
 * 持仓表单（新增 / 编辑共用）：提交时做后端同款校验（正数/必填）。
 * 切换编辑目标由父级换 key 重挂载，天然重置表单（无 effect 干预）。
 */
export function FundForm({
  mode,
  initial,
  submitting,
  onSubmit,
  onCancel,
}: {
  mode: 'add' | 'edit'
  initial?: FundHolding | null
  submitting: boolean
  onSubmit: (v: FundIn) => void
  onCancel: () => void
}) {
  const [v, setV] = useState<FundFormValue>({
    code: initial?.code ?? '',
    name: initial?.name ?? '',
    amount: initial ? String(initial.amount) : '',
    cost_nav: initial ? String(initial.cost_nav) : '',
    hold_shares: initial ? String(initial.hold_shares) : '',
    note: '',
  })
  const [err, setErr] = useState<string | null>(null)

  const set = (k: keyof FundFormValue) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setV((old) => ({ ...old, [k]: e.target.value }))

  function submit() {
    const code = v.code.trim()
    const amount = Number(v.amount)
    const costNav = Number(v.cost_nav)
    const shares = Number(v.hold_shares)
    if (!code) return setErr('基金代码不能为空')
    if (![amount, costNav, shares].every(Number.isFinite) || amount <= 0 || costNav <= 0 || shares <= 0) {
      return setErr('金额 / 净值 / 份额必须是正数')
    }
    setErr(null)
    onSubmit({
      code,
      name: v.name.trim(),
      amount,
      cost_nav: costNav,
      hold_shares: shares,
      note: v.note.trim(),
    })
  }

  return (
    <div className="rounded-tile border border-hairline bg-bg p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="text-[12.5px] font-medium text-ink">
          {mode === 'add' ? '新增持仓' : `编辑 ${initial?.code ?? ''}`}
        </div>
        <button type="button" aria-label="关闭表单" onClick={onCancel} className="cursor-pointer text-ink-3 hover:text-ink">
          <X size={14} />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3">
        <Field label="基金代码 *">
          <TextInput
            value={v.code}
            onChange={set('code')}
            placeholder="如 110022"
            disabled={mode === 'edit'}
            className={mode === 'edit' ? 'opacity-60' : ''}
          />
        </Field>
        <Field label="基金名称">
          <TextInput value={v.name} onChange={set('name')} placeholder="可选" />
        </Field>
        <Field label="投入金额（元）*">
          <TextInput value={v.amount} onChange={set('amount')} inputMode="decimal" placeholder="1000.00" />
        </Field>
        <Field label="成本净值 *">
          <TextInput value={v.cost_nav} onChange={set('cost_nav')} inputMode="decimal" placeholder="1.5000" />
        </Field>
        <Field label="持有份额 *">
          <TextInput value={v.hold_shares} onChange={set('hold_shares')} inputMode="decimal" placeholder="666.66" />
        </Field>
        <Field label="备注">
          <TextInput value={v.note} onChange={set('note')} placeholder="可选" />
        </Field>
      </div>
      {err ? <p className="mt-2 text-[12px] text-fall">{err}</p> : null}
      <div className="mt-2 flex gap-2">
        <Btn variant="primary" onClick={submit} disabled={submitting}>
          {submitting ? '提交中…' : mode === 'add' ? '保存（写入 SQLite）' : '保存修改'}
        </Btn>
        <Btn onClick={onCancel}>取消</Btn>
      </div>
    </div>
  )
}