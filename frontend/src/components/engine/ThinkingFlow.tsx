import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Typewriter } from './Typewriter'

/**
 * 思考流（reasoning 块）：等宽小字 + 打字机 + 可折叠。
 * 对应 SSE reasoning 事件——模型原生思考链，逐块流式渲染。
 */
export function ThinkingFlow({
  text,
  streaming,
  defaultOpen = false,
}: {
  text: string
  streaming: boolean
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (!text) return null
  return (
    <div className="rounded-tile border border-hairline bg-bg px-3 py-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full cursor-pointer items-center gap-1 text-[11px] text-ink-3 select-none"
      >
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        思考过程{streaming ? '（进行中）' : `（${text.length} 字）`}
      </button>
      {open ? (
        <div className="mono mt-1.5 max-h-56 overflow-y-auto text-[12px] leading-relaxed text-ink-2">
          <Typewriter text={text} instant={!streaming} />
        </div>
      ) : null}
    </div>
  )
}