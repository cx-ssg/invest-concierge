import { Pencil, Trash2 } from 'lucide-react'
import type { FundHolding } from '../../types/api'
import { fmtMoney, fmtNum, fmtPct } from '../../lib/format'
import { Num } from '../../components/ui/primitives'

/**
 * 持仓表（资产总览 / 我的持仓共用）：金额/份额等宽右对齐，
 * 实时字段（dwjz/gszzl）尽力而为，缺失降级 "--"。
 */
export function HoldingsTable({
  funds,
  onEdit,
  onDelete,
  deletingCode,
  compact = false,
}: {
  funds: FundHolding[]
  onEdit: (code: string) => void
  onDelete: (code: string) => void
  deletingCode?: string | null
  compact?: boolean
}) {
  return (
    <div className="overflow-x-auto rounded-tile border border-hairline">
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr style={{ background: 'var(--surface-2)' }}>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">名称</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">代码</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">投入金额</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">成本净值</th>
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">持有份额</th>
            {!compact ? (
              <>
                <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">最新净值</th>
                <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">估算涨幅</th>
              </>
            ) : null}
            <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">操作</th>
          </tr>
        </thead>
        <tbody>
          {funds.map((f) => (
            <tr key={f.code} className="hairline-b last:border-b-0 hover:bg-surface">
              <td className="px-2.5 py-1.5 text-ink">{f.name || '--'}</td>
              <td className="mono px-2.5 py-1.5 text-ink-2">{f.code}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">¥{fmtMoney(f.amount)}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(f.cost_nav, 4)}</td>
              <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(f.hold_shares, 2)}</td>
              {!compact ? (
                <>
                  <td className="num px-2.5 py-1.5 text-right text-ink-2">
                    {f.dwjz != null ? fmtNum(f.dwjz, 4) : '--'}
                  </td>
                  <td className="num px-2.5 py-1.5 text-right">
                    {f.gszzl != null ? (
                      <Num value={fmtPct(f.gszzl)} tone={f.gszzl > 0 ? 'rise' : f.gszzl < 0 ? 'fall' : 'flat'} />
                    ) : (
                      <span className="text-ink-3">--</span>
                    )}
                  </td>
                </>
              ) : null}
              <td className="px-2.5 py-1.5 text-right">
                <span className="inline-flex items-center gap-1">
                  {!compact ? (
                    <button
                      type="button"
                      title="编辑"
                      onClick={() => onEdit(f.code)}
                      className="cursor-pointer rounded-tile p-1 text-ink-3 hover:bg-surface-2 hover:text-ink"
                    >
                      <Pencil size={13} />
                    </button>
                  ) : null}
                  <button
                    type="button"
                    title="删除"
                    disabled={deletingCode === f.code}
                    onClick={() => onDelete(f.code)}
                    className="cursor-pointer rounded-tile p-1 text-ink-3 hover:bg-surface-2 hover:text-fall disabled:opacity-40"
                  >
                    <Trash2 size={13} />
                  </button>
                </span>
              </td>
            </tr>
          ))}
          {!funds.length ? (
            <tr>
              <td colSpan={compact ? 6 : 8} className="px-2.5 py-4 text-center text-ink-3">
                暂无持仓，点「新增持仓」录入
              </td>
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  )
}