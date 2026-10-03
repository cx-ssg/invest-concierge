import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, Search, Star, Trash2 } from 'lucide-react'
import { api } from '../lib/api'
import { fmtMoney, fmtNum, fmtPct } from '../lib/format'
import { Btn, Card, Num, Spinner, Tabs } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'
import { Empty } from '../components/ui/primitives'

/**
 * 自选股（H5 · P3）：股票轨的「自选 / 持仓」两个 tab。
 *
 * 接线说明：`watchlist` / `stock_holdings` 两张表与 CRUD 早已在 data/database（Streamlit
 * 时代的库层），本页复用它们 + 个股行情（get_stock_info，东财不可达时自带腾讯兜底）；
 * 组件复用现有 primitives（Tabs/Num/Btn/Card），不另起一套表格或数据通路。
 */

const TAB_LIST = [
  { key: 'watchlist', label: '⭐ 自选股' },
  { key: 'holdings', label: '📋 持仓股票' },
]

function tone(v: number | null | undefined): 'rise' | 'fall' | 'flat' {
  if (v == null || v === 0) return 'flat'
  return v > 0 ? 'rise' : 'fall'
}

/** 自选 tab：搜索加入 + 列表（实时行情，取不到显示 "--"） */
function WatchlistTab() {
  const qc = useQueryClient()
  const [keyword, setKeyword] = useState('')
  const [submitted, setSubmitted] = useState('')

  const listQ = useQuery({ queryKey: ['watchlist'], queryFn: api.watchlist.list })
  const searchQ = useQuery({
    queryKey: ['watchlist-search', submitted],
    queryFn: () => api.watchlist.search(submitted),
    enabled: submitted !== '',
    retry: false,
  })

  const addMut = useMutation({
    mutationFn: (v: { code: string; name: string }) => api.watchlist.add(v),
    onSuccess: (r) => {
      if (!r.ok) window.alert(r.error ?? '加入自选失败')
      void qc.invalidateQueries({ queryKey: ['watchlist'] })
    },
    onError: (e) => window.alert(`加入自选失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const delMut = useMutation({
    mutationFn: (code: string) => api.watchlist.remove(code),
    onSuccess: (r) => {
      if (!r.ok) window.alert(r.error ?? '移除失败')
      void qc.invalidateQueries({ queryKey: ['watchlist'] })
    },
  })

  const items = listQ.data?.items ?? []
  const results = searchQ.data?.results ?? []

  return (
    <div className="flex flex-col gap-3">
      {/* 搜索加入 */}
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex w-[220px] items-center gap-1.5 rounded-tile border border-hairline bg-surface px-2 py-1.5">
            <Search size={13} className="shrink-0 text-ink-3" />
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') setSubmitted(keyword.trim())
              }}
              placeholder="股票代码或名称，如 600519 / 茅台"
              className="w-full bg-transparent text-[12.5px] text-ink outline-none placeholder:text-ink-3"
            />
          </div>
          <Btn variant="primary" onClick={() => setSubmitted(keyword.trim())} disabled={!keyword.trim()}>
            <Search size={13} /> 搜索
          </Btn>
          <Btn onClick={() => void listQ.refetch()} title="刷新行情">
            <RefreshCw size={13} className={listQ.isFetching ? 'animate-spin' : undefined} /> 刷新
          </Btn>
        </div>

        {submitted ? (
          searchQ.isFetching ? (
            <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
              <Spinner size={12} /> 搜索中…
            </div>
          ) : results.length ? (
            <div className="mt-2 flex flex-col gap-1">
              {results.map((r) => (
                <div key={r.code} className="hairline-b flex items-center gap-2 px-1 py-1 last:border-b-0">
                  <span className="text-[12.5px] text-ink">{r.name}</span>
                  <span className="mono text-[12px] text-ink-2">{r.code}</span>
                  <div className="flex-1" />
                  <Btn onClick={() => void addMut.mutate({ code: r.code, name: r.name })}>
                    <Plus size={12} /> 加入自选
                  </Btn>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-2 text-[12px] text-ink-3">
              未搜到「{submitted}」——换个关键词，或确认后端与数据源可用。
            </p>
          )
        ) : null}
      </Card>

      {/* 自选列表 */}
      <Card className="p-3">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Star size={14} className="text-ink-2" /> 自选列表
          <span className="text-[11px] font-normal text-ink-3">（{items.length} 只 · 行情缓存 60 秒）</span>
        </div>
        {listQ.isLoading ? (
          <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
            <Spinner size={12} /> 加载自选与行情…
          </div>
        ) : items.length ? (
          <div className="mt-2 overflow-x-auto rounded-tile border border-hairline">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr style={{ background: 'var(--surface-2)' }}>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">名称</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">代码</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">市场</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">现价</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">涨跌幅</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((w) => (
                  <tr key={w.code} className="hairline-b last:border-b-0 hover:bg-surface">
                    <td className="px-2.5 py-1.5 text-ink">{w.name || '--'}</td>
                    <td className="mono px-2.5 py-1.5 text-ink-2">{w.code}</td>
                    <td className="px-2.5 py-1.5 text-ink-2">{w.market || '--'}</td>
                    <td className="px-2.5 py-1.5 text-right">
                      {w.price != null ? <Num value={fmtNum(w.price)} tone={tone(w.change)} /> : <span className="text-ink-3">--</span>}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      {w.change_percent != null ? (
                        <Num
                          value={`${w.change_percent >= 0 ? '+' : ''}${fmtPct(w.change_percent)}`}
                          tone={tone(w.change_percent)}
                        />
                      ) : (
                        <span className="text-ink-3">--</span>
                      )}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      <button
                        type="button"
                        title="移出自选"
                        onClick={() => {
                          if (window.confirm(`将 ${w.name || w.code} 移出自选？`)) void delMut.mutate(w.code)
                        }}
                        className="cursor-pointer rounded-tile p-1 text-ink-3 hover:bg-surface-2 hover:text-fall"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>自选还是空的——用上面的搜索框按代码或名称加入（行情由后端逐只补齐）。</Empty>
        )}
      </Card>
    </div>
  )
}

/** 持仓 tab：录入（加权平均成本）+ 列表（市值/浮动盈亏） */
function HoldingsTab() {
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [quantity, setQuantity] = useState('')
  const [costPrice, setCostPrice] = useState('')

  const listQ = useQuery({ queryKey: ['stock-holdings'], queryFn: api.watchlist.holdings })

  const addMut = useMutation({
    mutationFn: (v: { code: string; name: string; quantity: number; cost_price: number }) =>
      api.watchlist.addHolding(v),
    onSuccess: (r) => {
      if (!r.ok) {
        window.alert(r.error ?? '写入持仓失败')
        return
      }
      setCode('')
      setName('')
      setQuantity('')
      setCostPrice('')
      void qc.invalidateQueries({ queryKey: ['stock-holdings'] })
    },
    onError: (e) => window.alert(`写入持仓失败：${e instanceof Error ? e.message : String(e)}`),
  })

  const delMut = useMutation({
    mutationFn: (c: string) => api.watchlist.removeHolding(c),
    onSuccess: (r) => {
      if (!r.ok) window.alert(r.error ?? '删除失败')
      void qc.invalidateQueries({ queryKey: ['stock-holdings'] })
    },
  })

  const num = Number(quantity)
  const cost = Number(costPrice)
  const valid = /^\d{6}$/.test(code.trim()) && num > 0 && cost > 0
  const items = listQ.data?.items ?? []

  return (
    <div className="flex flex-col gap-3">
      <Card className="p-3">
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            placeholder="代码，如 600519"
            inputMode="numeric"
            className="mono w-[150px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="名称（可选）"
            className="w-[130px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
          <input
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            placeholder="股数，如 100"
            className="mono w-[110px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-right text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
          <input
            value={costPrice}
            onChange={(e) => setCostPrice(e.target.value)}
            placeholder="成本价，如 1250"
            className="mono w-[120px] rounded-tile border border-hairline bg-surface px-2 py-1.5 text-right text-[12.5px] text-ink outline-none placeholder:text-ink-3"
          />
          <Btn
            variant="primary"
            disabled={!valid || addMut.isPending}
            onClick={() =>
              void addMut.mutate({ code: code.trim(), name: name.trim(), quantity: num, cost_price: cost })
            }
          >
            {addMut.isPending ? <Spinner size={12} /> : <Plus size={13} />} 记录持仓
          </Btn>
          <Btn onClick={() => void listQ.refetch()} title="刷新行情">
            <RefreshCw size={13} className={listQ.isFetching ? 'animate-spin' : undefined} /> 刷新
          </Btn>
        </div>
        <p className="mt-2 text-[11.5px] text-ink-3">
          同一代码重复记录按<strong>加权平均成本</strong>合并（库层既有语义）；市值/盈亏按最新行情计算。
        </p>
      </Card>

      <Card className="p-3">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          📋 持仓列表
          <span className="text-[11px] font-normal text-ink-3">（{items.length} 只）</span>
        </div>
        {listQ.isLoading ? (
          <div className="mt-2 flex items-center gap-2 text-[12.5px] text-ink-2">
            <Spinner size={12} /> 加载持仓与行情…
          </div>
        ) : items.length ? (
          <div className="mt-2 overflow-x-auto rounded-tile border border-hairline">
            <table className="w-full border-collapse text-[12px]">
              <thead>
                <tr style={{ background: 'var(--surface-2)' }}>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">名称</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-left font-medium text-ink">代码</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">股数</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">成本价</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">现价</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">市值</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">浮动盈亏</th>
                  <th className="border-b border-hairline px-2.5 py-1.5 text-right font-medium text-ink">操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((h) => (
                  <tr key={h.code} className="hairline-b last:border-b-0 hover:bg-surface">
                    <td className="px-2.5 py-1.5 text-ink">{h.name || '--'}</td>
                    <td className="mono px-2.5 py-1.5 text-ink-2">{h.code}</td>
                    <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(h.quantity, 0)}</td>
                    <td className="num px-2.5 py-1.5 text-right text-ink-2">{fmtNum(h.cost_price)}</td>
                    <td className="px-2.5 py-1.5 text-right">
                      {h.price != null ? <Num value={fmtNum(h.price)} /> : <span className="text-ink-3">--</span>}
                    </td>
                    <td className="num px-2.5 py-1.5 text-right text-ink-2">
                      {h.market_value != null ? `¥${fmtMoney(h.market_value)}` : '--'}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      {h.pnl != null ? (
                        <Num
                          value={`${h.pnl >= 0 ? '+' : ''}${fmtMoney(h.pnl)}${h.pnl_percent != null ? ` (${h.pnl_percent >= 0 ? '+' : ''}${fmtPct(h.pnl_percent)})` : ''}`}
                          tone={tone(h.pnl)}
                        />
                      ) : (
                        <span className="text-ink-3">--</span>
                      )}
                    </td>
                    <td className="px-2.5 py-1.5 text-right">
                      <button
                        type="button"
                        title="删除持仓"
                        onClick={() => {
                          if (window.confirm(`删除持仓 ${h.name || h.code}？`)) void delMut.mutate(h.code)
                        }}
                        className="cursor-pointer rounded-tile p-1 text-ink-3 hover:bg-surface-2 hover:text-fall"
                      >
                        <Trash2 size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty>还没有股票持仓——用上面的表单记录（行情不可得时现价/市值显示 "--"）。</Empty>
        )}
      </Card>
    </div>
  )
}

export function WatchlistPage() {
  const [tab, setTab] = useState('watchlist')
  return (
    <section className="flex min-w-0 flex-1 flex-col gap-3">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="自选股"
        desc="盯住的票与持仓一页管理：实时行情、涨跌、市值与浮动盈亏"
      />
      <Tabs tabs={TAB_LIST} active={tab} onChange={setTab} />
      {tab === 'watchlist' ? <WatchlistTab /> : null}
      {tab === 'holdings' ? <HoldingsTab /> : null}
    </section>
  )
}
