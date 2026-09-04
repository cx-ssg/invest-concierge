import { useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { fmtNum } from '../../lib/format'

/** 小节标题（对应旧页面 #### 层级） */
export function Section({ children }: { children: ReactNode }) {
  return <div className="mt-3 mb-1 text-[12px] font-medium text-ink">{children}</div>
}

/** 指标卡网格（对齐 st.metric 的 label/value 两行式） */
export function MetricGrid({
  items,
}: {
  items: { label: string; value: string; tone?: 'rise' | 'fall' | 'flat' }[]
}) {
  return (
    <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
      {items.map((it) => (
        <div key={it.label} className="rounded-tile border border-hairline bg-bg px-3 py-2">
          <div className="text-[11px] text-ink-3">{it.label}</div>
          <div
            className={`num mt-0.5 text-[16px] font-semibold ${
              it.tone === 'rise' ? 'text-rise' : it.tone === 'fall' ? 'text-fall' : 'text-ink'
            }`}
          >
            {it.value}
          </div>
        </div>
      ))}
    </div>
  )
}

/** 可折叠块（旧 expander 语义） */
export function Collapse({
  title,
  children,
  defaultOpen = false,
}: {
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-tile border border-hairline bg-bg">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full cursor-pointer items-center gap-1.5 px-2.5 py-1.5 text-left text-[12.5px] text-ink-2 select-none hover:text-ink"
      >
        {open ? <ChevronDown size={13} className="shrink-0 text-ink-3" /> : <ChevronRight size={13} className="shrink-0 text-ink-3" />}
        <span className="min-w-0">{title}</span>
      </button>
      {open ? <div className="px-3 pb-2.5">{children}</div> : null}
    </div>
  )
}

/** 数据表格（发丝线分隔，数字右对齐） */
export function InfoTable({
  headers,
  rows,
}: {
  headers: string[]
  rows: string[][]
}) {
  if (!rows.length) return <div className="px-1 py-2 text-[12px] text-ink-3">暂无数据</div>
  return (
    <div className="overflow-x-auto rounded-tile border border-hairline">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            {headers.map((h) => (
              <th key={h} className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="hairline-b last:border-b-0">
              {r.map((c, j) => (
                <td key={j} className={`px-2.5 py-1.5 text-ink-2 ${j > 0 ? 'num text-right' : ''}`}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 列表点（- 语义） */
export function DotList({ items, tone }: { items: string[]; tone?: 'rise' | 'fall' | 'flat' }) {
  if (!items.length) return <div className="px-1 text-[12px] text-ink-3">暂无</div>
  return (
    <ul className="flex flex-col gap-1">
      {items.map((it, i) => (
        <li
          key={i}
          className={`px-1 text-[12.5px] leading-relaxed ${
            tone === 'rise' ? 'text-rise' : tone === 'fall' ? 'text-fall' : 'text-ink-2'
          }`}
        >
          {tone === 'rise' ? '🟢 ' : tone === 'fall' ? '🔴 ' : ''}
          {it}
        </li>
      ))}
    </ul>
  )
}

/** 评分条（0..max，accent 填充，克制动效） */
export function ScoreBar({ label, score, max, note }: { label: string; score: number; max: number; note?: string }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (score / max) * 100)) : 0
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 shrink-0 truncate text-[12px] text-ink-2">{label}</span>
      <div className="h-1.5 min-w-0 flex-1 rounded-full border border-hairline bg-bg">
        <div
          className="h-full rounded-full"
          style={{ width: `${pct}%`, background: 'var(--accent)', opacity: 0.7 }}
        />
      </div>
      <span className="num w-12 shrink-0 text-right text-[11px] text-ink-3">
        {fmtNum(score, 0)}/{max}
      </span>
      {note ? <span className="w-40 truncate text-[11px] text-ink-3">{note}</span> : null}
    </div>
  )
}