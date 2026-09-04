import { useEffect } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { BarChart3, ChartLine, MessageSquare, Notebook, Settings, Wallet } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import type { NavTrack } from '../../types/api'
import { Kicker, Num } from '../../components/ui/primitives'
import { api } from '../../lib/api'
import { pagePath } from '../../lib/nav'
import { useUiStore, type Track } from '../../stores/ui'

/** 页面 key → 图标（live 6 页） */
const PAGE_ICONS: Record<string, React.ComponentType<{ size?: number }>> = {
  dashboard: BarChart3,
  portfolio: Wallet,
  diary: Notebook,
  stock_diagnosis: ChartLine,
  ai_chat: MessageSquare,
  settings: Settings,
}

/** /api/nav 不可用时的本地兜底（与 services/nav_service.PAGE_META live 集逐条一致） */
const FALLBACK_TRACKS: NavTrack[] = [
  {
    track: 'fund',
    label: '📊 基金',
    pages: [
      { key: 'dashboard', label: '📊 资产总览' },
      { key: 'portfolio', label: '💼 我的持仓' },
      { key: 'diary', label: '📝 投资日记' },
    ],
  },
  {
    track: 'stock',
    label: '📈 股票',
    pages: [{ key: 'stock_diagnosis', label: '🩺 综合诊断' }],
  },
  {
    track: 'common',
    label: '⚙️ 通用',
    pages: [
      { key: 'ai_chat', label: '💬 AI 对话' },
      { key: 'settings', label: '⚙️ 系统设置' },
    ],
  },
]

/** 轨道首个 live 页（切换轨道时的落地页，避免落到无路由的 /fund/ 之类） */
const TRACK_HOME: Record<Track, string> = {
  fund: '/fund/dashboard',
  stock: '/stock/stock_diagnosis',
}

export function Sidebar() {
  const track = useUiStore((s) => s.track)
  const setTrack = useUiStore((s) => s.setTrack)
  const navigate = useNavigate()
  const location = useLocation()
  const { data: nav } = useQuery({ queryKey: ['nav'], queryFn: api.nav, staleTime: 5 * 60_000 })
  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    refetchInterval: 30_000,
  })
  const { data: summary } = useQuery({
    queryKey: ['summary'],
    queryFn: api.summary,
    refetchInterval: 60_000,
  })

  // 深链同步：/fund|stock 前缀与 UI 轨道对齐（useParams 在 React Router v7 下
  // 类型为泛型 Params<string>，无法按具体键赋值，且侧栏不在 Route 内拿不到参数，
  // 改为读 location.pathname + useEffect，不在渲染期 setState）
  useEffect(() => {
    const m = location.pathname.match(/^\/(fund|stock)\//)
    if (m && m[1] !== track) setTrack(m[1] as Track)
  }, [location.pathname, track, setTrack])

  const tracks = (nav?.tracks?.length ? nav.tracks : FALLBACK_TRACKS) as NavTrack[]
  const current = track === 'fund' ? tracks[0] : tracks[1]
  const commonTrack = tracks[2]
  const indices = (status?.data_source?.indices ?? []).slice(0, 3)

  const navCls = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 rounded-tile px-2 py-1.5 text-[13px] transition-colors ${
      isActive
        ? 'bg-surface-2 font-medium text-ink'
        : 'text-ink-2 hover:bg-surface hover:text-ink'
    }`

  return (
    <aside
      className="flex w-[200px] shrink-0 flex-col gap-3 overflow-y-auto p-2"
      style={{ background: 'var(--bg-sidebar)' }}
    >
      {/* 轨道切换（基金/股票） */}
      <div className="flex rounded-tile border border-hairline bg-surface p-0.5">
        {(['fund', 'stock'] as Track[]).map((t) => {
          const label = t === 'fund' ? '基金' : '股票'
          const active = track === t
          return (
            <button
              key={t}
              type="button"
              onClick={() => {
                setTrack(t)
                navigate(TRACK_HOME[t])
              }}
              className={`flex-1 cursor-pointer rounded-tile py-1 text-[12px] transition-colors ${
                active ? 'bg-surface-2 font-medium text-ink' : 'text-ink-3 hover:text-ink'
              }`}
              style={{ transitionDuration: 'var(--dur)' }}
            >
              {label}
            </button>
          )
        })}
      </div>

      {/* 当前轨 live 导航 */}
      <div className="flex flex-col gap-1">
        <Kicker>{current?.label?.replace(/^[^\u4e00-\u9fa5]+/, '') || '导航'}</Kicker>
        {(current?.pages ?? []).map((p) => {
          const Icon = PAGE_ICONS[p.key]
          return (
            <NavLink key={p.key} to={pagePath(track, p.key)} end className={navCls}>
              {Icon ? <Icon size={15} /> : null}
              <span>{p.label.replace(/^[^\u4e00-\u9fa5]+/, '')}</span>
            </NavLink>
          )
        })}
      </div>

      {/* 两轨通用 */}
      <div className="flex flex-col gap-1">
        <Kicker>通用</Kicker>
        {(commonTrack?.pages ?? []).map((p) => {
          const Icon = PAGE_ICONS[p.key]
          return (
            <NavLink key={p.key} to={pagePath(track, p.key)} end className={navCls}>
              {Icon ? <Icon size={15} /> : null}
              <span>{p.label.replace(/^[^\u4e00-\u9fa5]+/, '')}</span>
            </NavLink>
          )
        })}
      </div>

      {/* 持仓迷你 */}
      <div className="mt-auto flex flex-col gap-2">
        {summary ? (
          <div className="rounded-tile border border-hairline bg-surface px-2 py-1.5">
            <div className="text-[11px] text-ink-3">持仓概览</div>
            <div className="flex items-baseline justify-between">
              <span className="text-[11px] text-ink-2">{summary.fund_count} 只</span>
              <Num value={`¥${Math.round(summary.total_invest).toLocaleString()}`} />
            </div>
          </div>
        ) : null}

        {/* 大盘 3 行（8s 超时弱网降级：无数据就整块不显示） */}
        {indices.length > 0 ? (
          <div className="rounded-tile border border-hairline bg-surface px-2 py-1.5">
            <div className="mb-1 text-[11px] text-ink-3">大盘指数</div>
            {indices.map((ix) => (
              <div key={ix.code} className="flex items-baseline justify-between leading-5">
                <span className="truncate text-[11px] text-ink-2">{ix.name}</span>
                <span className="flex items-baseline gap-1.5">
                  <Num value={ix.price} />
                  <Num
                    value={`${ix.change_percent >= 0 ? '+' : ''}${ix.change_percent.toFixed(2)}%`}
                    tone={ix.change_percent > 0 ? 'rise' : ix.change_percent < 0 ? 'fall' : 'flat'}
                  />
                </span>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </aside>
  )
}
