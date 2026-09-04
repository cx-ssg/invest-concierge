import { Moon, Settings, Sun } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { StatusDot } from '../../components/ui/primitives'
import { api } from '../../lib/api'
import { useUiStore } from '../../stores/ui'

/** 顶栏：品牌左 + 引擎状态点（真数据）+ 主题切换 + 设置（44px，发丝线下边） */
export function TitleBar() {
  const theme = useUiStore((s) => s.theme)
  const toggleTheme = useUiStore((s) => s.toggleTheme)
  const navigate = useNavigate()
  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    refetchInterval: 30_000,
    retry: 1,
  })

  const engineReady = status?.engine.api_key_configured ?? false

  return (
    <header
      className="hairline-b flex h-[44px] shrink-0 items-center gap-3 px-3 select-none"
      style={{ background: 'var(--bg-sidebar)' }}
    >
      <div className="flex items-center gap-2">
        <span className="mono text-[13px] font-semibold tracking-wide text-ink">
          invest-concierge
        </span>
        <span className="text-[11px] text-ink-3">A股投研工作台</span>
      </div>

      <div className="flex-1" />

      {/* 引擎状态点：/api/status 每 30s 驱动（未配 Key 显示引导态） */}
      <button
        type="button"
        aria-label="引擎状态"
        onClick={() => navigate('/settings')}
        className="flex cursor-pointer items-center gap-1.5 rounded-tile px-1 py-0.5 text-[11px] text-ink-2 transition-colors hover:text-ink"
        style={{ transitionDuration: 'var(--dur)' }}
      >
        <StatusDot state={engineReady ? 'ready' : 'off'} />
        {engineReady ? '引擎就绪' : '引擎未配置'}
      </button>

      <button
        type="button"
        aria-label="切换浅色/深色主题"
        onClick={toggleTheme}
        className="flex size-7 cursor-pointer items-center justify-center rounded-tile border border-hairline text-ink-2 transition-colors"
        style={{ transitionDuration: 'var(--dur)' }}
        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--surface-2)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
      >
        {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
      </button>

      <button
        type="button"
        aria-label="设置"
        title="系统设置"
        onClick={() => navigate('/settings')}
        className="flex size-7 cursor-pointer items-center justify-center rounded-tile border border-hairline text-ink-2 transition-colors"
        style={{ transitionDuration: 'var(--dur)' }}
        onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--surface-2)')}
        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
      >
        <Settings size={14} />
      </button>
    </header>
  )
}
