import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { Bell, BellRing, Plus, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import type { AlertRule } from '../types/api'
import { Btn, Card, Spinner } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'

/**
 * 价格预警（v1.1 粘性三件套 A）：规则 CRUD + 触发事件时间线。
 * 触发判定在后端调度器（仅交易时段 + 单标的 10 分钟限频，docs/V1.1_PLAN.md A 节）；
 * 桌面壳下触发走托盘气泡，本页/顶栏角标是通用入口。
 */

type Kind = 'fund' | 'stock'

const KIND_LABEL: Record<Kind, string> = { fund: '基金（按盘中估值涨跌幅）', stock: '股票（按现价）' }
const OP_LABEL: Record<string, string> = { above: '高于', below: '低于' }
const UNIT: Record<Kind, string> = { fund: '%', stock: '元' }

function AlertForm({
  submitting,
  preset,
  onSubmit,
}: {
  submitting: boolean
  preset: { symbol: string; name: string } | null
  onSubmit: (v: {
    kind: Kind
    symbol: string
    name: string
    metric: 'estimate_pct' | 'price'
    op: 'above' | 'below'
    threshold: number
  }) => void
}) {
  const [search] = useSearchParams()
  const [kind, setKind] = useState<Kind>('fund')
  const [symbol, setSymbol] = useState(preset?.symbol ?? search.get('symbol') ?? '')
  const [name, setName] = useState(preset?.name ?? search.get('name') ?? '')
  const [op, setOp] = useState<'above' | 'below'>('above')
  const [threshold, setThreshold] = useState('')

  const num = Number(threshold)
  const valid = symbol.trim() && threshold.trim() && Number.isFinite(num)

  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
        <Plus size={14} className="text-ink-2" /> 新建预警
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {/* 类型分段 */}
        <div className="flex rounded-tile border border-hairline bg-surface-2 p-0.5">
          {(Object.keys(KIND_LABEL) as Kind[]).map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => setKind(k)}
              className={`cursor-pointer rounded-tile px-2.5 py-1 text-[12px] transition-colors ${
                kind === k ? 'bg-surface font-medium text-ink' : 'text-ink-3 hover:text-ink'
              }`}
              style={{ transitionDuration: 'var(--dur)' }}
            >
              {k === 'fund' ? '基金' : '股票'}
            </button>
          ))}
        </div>
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          placeholder={kind === 'fund' ? '基金代码，如 110022' : '股票代码，如 600519'}
          className="mono w-[170px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-ink-3"
        />
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="名称（可选）"
          className="w-[130px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-ink-3"
        />
        <div className="flex rounded-tile border border-hairline bg-surface-2 p-0.5">
          {(['above', 'below'] as const).map((o) => (
            <button
              key={o}
              type="button"
              onClick={() => setOp(o)}
              className={`cursor-pointer rounded-tile px-2.5 py-1 text-[12px] transition-colors ${
                op === o ? 'bg-surface font-medium text-ink' : 'text-ink-3 hover:text-ink'
              }`}
              style={{ transitionDuration: 'var(--dur)' }}
            >
              {OP_LABEL[o]}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1 rounded-tile border border-hairline bg-surface px-2 py-1.5">
          <input
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder={kind === 'fund' ? '如 3 或 -2' : '如 1600'}
            className="mono w-[90px] bg-transparent text-right text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
          <span className="text-[12px] text-ink-3">{UNIT[kind]}</span>
        </div>
        <Btn
          variant="primary"
          disabled={!valid || submitting}
          onClick={() =>
            onSubmit({
              kind,
              symbol: symbol.trim(),
              name: name.trim(),
              metric: kind === 'fund' ? 'estimate_pct' : 'price',
              op,
              threshold: num,
            })
          }
        >
          {submitting ? <Spinner size={12} /> : <Bell size={13} />} 创建
        </Btn>
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-ink-3">
        {KIND_LABEL[kind]} · 仅交易时段轮询（每标的至少间隔 10 分钟） · 触发后同一自然日不重复提醒
      </p>
    </Card>
  )
}

function RuleRow({ rule }: { rule: AlertRule }) {
  const qc = useQueryClient()
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['alerts'] })
    void qc.invalidateQueries({ queryKey: ['alert-events'] })
  }
  const patchMut = useMutation({
    mutationFn: (enabled: boolean) => api.alerts.patch(rule.id, enabled),
    onSuccess: invalidate,
  })
  const delMut = useMutation({
    mutationFn: () => api.alerts.remove(rule.id),
    onSuccess: invalidate,
  })
  const unit = UNIT[rule.kind]
  const enabled = !!rule.enabled

  return (
    <div className="hairline-b flex items-center gap-2.5 px-3 py-2 last:border-b-0">
      <button
        type="button"
        title={enabled ? '暂停' : '启用'}
        onClick={() => void patchMut.mutate(!enabled)}
        className={`relative h-4.5 w-8 shrink-0 cursor-pointer rounded-full border transition-colors ${
          enabled ? 'border-hairline-strong' : 'border-hairline'
        }`}
        style={{ background: enabled ? 'var(--accent-soft)' : 'var(--surface-2)' }}
        aria-pressed={enabled}
      >
        <span
          className="absolute top-0.5 size-3 rounded-full transition-all"
          style={{
            left: enabled ? 17 : 3,
            background: enabled ? 'var(--accent)' : 'var(--text-3)',
            transitionDuration: 'var(--dur)',
          }}
        />
      </button>
      <span className="truncate text-[12.5px] text-ink">{rule.name || rule.symbol}</span>
      <span className="mono shrink-0 text-[12px] text-ink-2">{rule.symbol}</span>
      <span className="shrink-0 text-[12px] text-ink-2">
        {OP_LABEL[rule.op]} <span className="mono">{rule.threshold}</span>
        {unit}
      </span>
      <div className="flex-1" />
      {rule.last_triggered_date ? (
        <span className="hidden shrink-0 text-[11px] text-ink-3 sm:inline">
          上次触发 {rule.last_triggered_date}
        </span>
      ) : null}
      <button
        type="button"
        title="删除"
        onClick={() => {
          if (window.confirm(`删除预警 ${rule.name || rule.symbol}？`)) void delMut.mutate()
        }}
        className="cursor-pointer rounded-tile p-1 text-ink-3 hover:bg-surface-2 hover:text-fall"
      >
        <Trash2 size={13} />
      </button>
    </div>
  )
}

export function AlertsPage() {
  const qc = useQueryClient()
  const [search] = useSearchParams()
  const preset = search.get('symbol')
    ? { symbol: search.get('symbol') ?? '', name: search.get('name') ?? '' }
    : null

  const rulesQ = useQuery({ queryKey: ['alerts'], queryFn: api.alerts.list })
  const eventsQ = useQuery({ queryKey: ['alert-events'], queryFn: api.alerts.events })

  const createMut = useMutation({
    mutationFn: api.alerts.create,
    onSuccess: (r) => {
      if (!r.ok) {
        window.alert(r.error ?? '创建失败')
        return
      }
      void qc.invalidateQueries({ queryKey: ['alerts'] })
    },
  })

  const markReadMut = useMutation({
    mutationFn: api.alerts.markRead,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['alert-events'] }),
  })

  const rules = rulesQ.data?.alerts ?? []
  const events = eventsQ.data?.events ?? []
  const unread = eventsQ.data?.unread ?? 0

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="价格预警"
        desc="设定触发条件，桌面壳托盘会在触发时提醒你"
      />

      <AlertForm
        submitting={createMut.isPending}
        preset={preset}
        onSubmit={(v) => void createMut.mutate(v)}
      />

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Bell size={14} className="text-ink-2" /> 预警规则
          <span className="text-[11px] font-normal text-ink-3">（{rules.length} 条）</span>
        </div>
        {rulesQ.isFetching && !rulesQ.data ? (
          <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
            <Spinner size={12} /> 加载中…
          </div>
        ) : rules.length ? (
          <div className="mt-1 -mx-3">
            {rules.map((r) => (
              <RuleRow key={r.id} rule={r} />
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[12.5px] text-ink-3">
            还没有预警规则——从上面的表单创建，或到「我的持仓」每行点 🔔 快速设置。
          </p>
        )}
      </Card>

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <BellRing size={14} className="text-ink-2" /> 触发记录
          {unread > 0 ? (
            <>
              <span className="rounded-tile px-1.5 py-0.5 text-[11px]" style={{ background: 'var(--accent-soft)', color: 'var(--accent)' }}>
                {unread} 条未读
              </span>
              <Btn onClick={() => void markReadMut.mutate()}>全部已读</Btn>
            </>
          ) : null}
        </div>
        {events.length ? (
          <div className="mt-2 flex flex-col gap-1.5">
            {events.slice(0, 20).map((e) => (
              <div
                key={e.id}
                className={`rounded-tile border px-3 py-2 text-[12.5px] leading-relaxed ${
                  e.read ? 'border-hairline text-ink-2' : 'border-hairline-strong text-ink'
                }`}
                style={!e.read ? { background: 'var(--accent-soft)' } : undefined}
              >
                {e.message}
                <span className="ml-2 text-[11px] text-ink-3">{e.created_at}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="mt-2 text-[12.5px] text-ink-3">暂无触发记录——触发后这里会出现时间线。</p>
        )}
      </Card>
    </section>
  )
}
