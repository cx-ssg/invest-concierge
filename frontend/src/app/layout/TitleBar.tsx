import { Copy, Minus, Moon, Settings, Square, Sun, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { StatusDot } from '../../components/ui/primitives'
import { api } from '../../lib/api'
import { useUiStore } from '../../stores/ui'

/** 桌面壳探测：pywebview 注入 window.pywebview 桥（浏览器模式无此对象） */
function isPywebviewShell(): boolean {
  return typeof window !== 'undefined' && 'pywebview' in window
}

/** Electron 壳探测（SHELL_UPGRADE ③）：preload contextBridge 注入 window.desktopAPI。
 *  Electron 的注入发生在页面脚本之前（contextIsolation 桥随文档创建就绪），
 *  与 pywebview 的"加载后注入"时序不同——挂载时同步探测即可，无需事件驱动。 */
function isElectronShell(): boolean {
  return typeof window !== 'undefined' && 'desktopAPI' in window
}

/** frameless 探测：两种壳统一走 URL ?shell=frameless 门控。
 *  - pywebview：默认原生边框模式同样有桥，只看桥会把死按钮画到系统标题栏旁
 *  - Electron：main.cjs 无条件 frameless，URL 必带 shell 参数
 *  共同前提：探到壳桥（pywebview 或 desktopAPI 之一）才认。 */
function isFramelessShell(): boolean {
  if (typeof window === 'undefined') return false
  if (!isPywebviewShell() && !isElectronShell()) return false
  return new URLSearchParams(window.location.search).get('shell') === 'frameless'
}

/** 窗口控制：双壳适配。
 *  - Electron（优先）：desktopAPI IPC——close 走主进程 close 事件=隐藏到托盘；
 *    maximize 返回切换后的态（true=已最大化）供图标同步
 *  - pywebview：桥（launcher js_api 注入）调窗口原生方法；失败静默。
 *    destroy 走 closing 事件链：有托盘=收进托盘、--no-tray=直接退出。 */
async function winMinimize(): Promise<void> {
  try {
    if (isElectronShell()) await window.desktopAPI!.minimize()
    else void window.pywebview?.api?.minimize?.()
  } catch {
    /* noop */
  }
}
async function winToggleMaximize(): Promise<boolean> {
  try {
    if (isElectronShell()) return (await window.desktopAPI!.maximize()) === true
    await window.pywebview?.api?.maximize?.()
    return true
  } catch {
    return false
  }
}
async function winRestore(): Promise<void> {
  try {
    if (isElectronShell()) await window.desktopAPI!.restore()
    else void window.pywebview?.api?.restore?.()
  } catch {
    /* noop */
  }
}
async function winClose(): Promise<void> {
  try {
    if (isElectronShell()) await window.desktopAPI!.close()
    else void window.pywebview?.api?.destroy?.()
  } catch {
    /* noop */
  }
}

/** 顶栏：品牌左 + 引擎状态点（真数据）+ 主题切换 + 设置（44px，发丝线下边）。
 *  桌面 frameless 模式追加：中段拖动区 + 右上三枚窗口按钮。
 *  拖动区类 .pywebview-drag-region 只在 pywebview easy_drag 生效；Electron 的
 *  拖动由 CSS -webkit-app-region:承担（见下方 style）。 */
export function TitleBar() {
  const theme = useUiStore((s) => s.theme)
  const toggleTheme = useUiStore((s) => s.toggleTheme)
  const navigate = useNavigate()
  // pywebview 桥在页面加载完成后才注入（NavigationCompleted → inject_pywebview），
  // React 挂载时刻 window.pywebview 尚不存在——探测必须是事件驱动的：
  // 注入完成会派发 pywebviewready CustomEvent（api.js finish 脚本），届时置真重渲染。
  // Electron desktopAPI 挂载时同步就绪（见 isElectronShell 注释），初始化即真。
  const [shellReady, setShellReady] = useState(() => isElectronShell() || isPywebviewShell())
  useEffect(() => {
    if (shellReady) return
    const onReady = () => setShellReady(true)
    window.addEventListener('pywebviewready', onReady)
    // 兜底轮询：万一事件早于本监听器已派发（理论上不会，注入晚于 React 挂载）
    const timer = window.setInterval(() => {
      if (isPywebviewShell()) {
        window.clearInterval(timer)
        setShellReady(true)
      }
    }, 300)
    const stop = window.setTimeout(() => window.clearInterval(timer), 10_000)
    return () => {
      window.removeEventListener('pywebviewready', onReady)
      window.clearInterval(timer)
      window.clearTimeout(stop)
    }
  }, [shellReady])
  const electron = isElectronShell()
  const frameless = shellReady && isFramelessShell()
  const [maximized, setMaximized] = useState(false)
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
      style={{
        background: 'var(--bg-sidebar)',
        // Electron frameless 拖动区：CSS app-region（pywebview easy_drag 不认此属性但无害）
        ...(electron && frameless ? ({ WebkitAppRegion: 'drag' } as never) : {}),
      }}
    >
      <div className="flex items-center gap-2">
        <span className="mono text-[13px] font-semibold tracking-wide text-ink">
          invest-concierge
        </span>
        <span className="text-[11px] text-ink-3">A股投研工作台</span>
      </div>

      {/* frameless 拖动区：pywebview easy_drag 只认 .pywebview-drag-region
          （DIRECT_TARGET_ONLY=True——区域内点按钮不会带窗口跑）；
          浏览器模式无副作用（类存在但不生效） */}
      <div
        className="pywebview-drag-region min-w-0 flex-1"
        style={{ height: '100%' }}
      />

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

      {/* 桌面 frameless：右上三枚窗口按钮（浏览器/原生边框模式自动隐藏）。
          Electron 模式下按钮须脱离 drag 区（WebkitAppRegion=no-drag）才可点击 */}
      {frameless ? (
        <div
          className="ml-1 flex items-center"
          data-window-controls
          style={electron ? ({ WebkitAppRegion: 'no-drag' } as never) : undefined}
        >
          <button
            type="button"
            aria-label="最小化"
            onClick={() => void winMinimize()}
            className="flex size-8 cursor-pointer items-center justify-center text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            style={{ transitionDuration: 'var(--dur)' }}
          >
            <Minus size={13} />
          </button>
          <button
            type="button"
            aria-label={maximized ? '还原窗口' : '最大化窗口'}
            title={maximized ? '还原' : '最大化'}
            onClick={async () => {
              if (electron) {
                const nowMax = await winToggleMaximize()
                setMaximized(nowMax)
              } else if (maximized) {
                await winRestore()
                setMaximized(false)
              } else {
                await winToggleMaximize()
                setMaximized(true)
              }
            }}
            className="flex size-8 cursor-pointer items-center justify-center text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
            style={{ transitionDuration: 'var(--dur)' }}
          >
            {maximized ? <Copy size={11} /> : <Square size={11} />}
          </button>
          <button
            type="button"
            aria-label="关闭（有托盘时最小化到托盘）"
            onClick={() => void winClose()}
            className="flex size-8 cursor-pointer items-center justify-center text-ink-2 transition-colors hover:bg-fall hover:text-ink"
            style={{ transitionDuration: 'var(--dur)' }}
          >
            <X size={14} />
          </button>
        </div>
      ) : null}
    </header>
  )
}
