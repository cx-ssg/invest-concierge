import { useEffect, useState } from 'react'

/**
 * 打字机：文本追加时逐字/逐块揭示，末尾光标脉冲。
 * SSE 事件是分轮分块推送（reasoning 整块 / done.content 整段），
 * 打字机把"块"渲染成"流式"观感（对齐 UI_REWRITE_DESIGN §2.4 逐字渲染）。
 * instant=true（流结束）时直接渲染全部，避免无限等待。
 */
export function Typewriter({ text, instant = false }: { text: string; instant?: boolean }) {
  const [shown, setShown] = useState(0)

  useEffect(() => {
    if (instant) return // 直接全量渲染，不启动动画
    // 剩余越长揭示越快（2000 字块 ≈ 2s 内完成）
    const step = Math.max(3, Math.ceil(text.length / 120))
    const timer = setInterval(() => {
      setShown((s) => {
        if (s >= text.length) {
          clearInterval(timer)
          return s
        }
        return Math.min(text.length, s + step)
      })
    }, 16)
    return () => clearInterval(timer)
  }, [text, instant])

  if (instant) {
    return <span className="whitespace-pre-wrap break-words">{text}</span>
  }
  const done = shown >= text.length
  return (
    <span className="whitespace-pre-wrap break-words">
      {text.slice(0, shown)}
      {!done ? <span className="engine-caret" aria-hidden="true" /> : null}
    </span>
  )
}