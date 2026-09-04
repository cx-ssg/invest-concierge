import { Moon, Settings, Sun } from 'lucide-react'
import { StatusDot } from '../../components/ui/primitives'
import { useUiStore } from '../../stores/ui'

/** 顶栏：品牌左 + 引擎状态点 + 主题切换 + 设置（38px，发丝线下边） */
export function TitleBar() {
  const theme = useUiStore((s) => s.theme)
  const toggleTheme = useUiStore((s) => s.toggleTheme)

  return (
    <header
      className="hairline-b flex h-[38px] shrink-0 items-center gap-3 px-3 select-none"
      style={{ background: 'var(--bg-sidebar)' }}
    >
      <div className="flex items-center gap-2">
        <span className="mono text-[13px] font-semibold tracking-wide text-ink">
          invest-concierge
        </span>
        <span className="text-[11px] text-ink-3">A股投研工作台</span>
      </div>

      <div className="flex-1" />

      {/* 引擎状态点（数据源：M1 由 /api/status 驱动，占位 ready） */}
      <span className="flex items-center gap-1.5 text-[11px] text-ink-2">
        <StatusDot state="ready" />
        引擎就绪
      </span>

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
        className="flex size-7 cursor-pointer items-center justify-center rounded-tile border border-hairline text-ink-2"
      >
        <Settings size={14} />
      </button>
    </header>
  )
}
