import { useEffect, useState } from 'react'

export interface MenuItem {
  key: string
  label: string
  danger?: boolean
  onClick: () => void
}

/**
 * 右键菜单（会话栏用）：点击任意处关闭；Esc 关闭；超出视口时贴边回缩。
 * 定位由调用方传入 clientX/clientY，组件内部换算为 fixed 坐标。
 */
export function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number
  y: number
  items: MenuItem[]
  onClose: () => void
}) {
  const [pos, setPos] = useState({ left: x, top: y })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const onDown = (e: MouseEvent) => {
      const el = document.getElementById('ctx-menu')
      if (el && !el.contains(e.target as Node)) onClose()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mousedown', onDown)
    // 贴边回缩：菜单位置超出视口时拉回
    requestAnimationFrame(() => {
      const el = document.getElementById('ctx-menu')
      if (!el) return
      const rect = el.getBoundingClientRect()
      if (rect.right > window.innerWidth - 8 || rect.bottom > window.innerHeight - 8) {
        setPos({
          left: Math.max(8, Math.min(x, window.innerWidth - rect.width - 8)),
          top: Math.max(8, Math.min(y, window.innerHeight - rect.height - 8)),
        })
      }
    })
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mousedown', onDown)
    }
  }, [onClose, x, y])

  return (
    <div
      id="ctx-menu"
      role="menu"
      className="fixed z-50 min-w-[140px] rounded-tile border border-hairline bg-surface-2 py-1"
      style={{ left: pos.left, top: pos.top }}
    >
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          role="menuitem"
          onClick={() => {
            onClose()
            item.onClick()
          }}
          className={`block w-full cursor-pointer px-3 py-1.5 text-left text-[12px] transition-colors hover:bg-surface ${
            item.danger ? 'text-fall' : 'text-ink-2'
          }`}
          style={{ transitionDuration: 'var(--dur)' }}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}