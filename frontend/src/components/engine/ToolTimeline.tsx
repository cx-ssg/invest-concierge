import { Check, X } from 'lucide-react'
import { fmtMs } from '../../lib/format'

/**
 * Agent 工具链时间线（M1 初型）
 *
 * 数据契约 = utils/agent_core.structured_progress（2026-09-03 实读）：
 *   tool_start {name, arguments} → 行置 running（● 呼吸）
 *   tool_end   {name, ok, elapsed_ms} → 定态 done(✓) / error(✕) + 耗时
 *   pending 为尚未触发的工具行（灰 ●）
 * 状态机：pending(灰●) → running(呼吸●) → done(✓) / error(✕)
 * 行 = 状态字形 + name(args)(mono 截断) + 右 elapsed_ms(tabular)；发丝线分隔，零装饰。
 */

export type ToolStepState = 'pending' | 'running' | 'done' | 'error'

export interface ToolStep {
  name: string
  arguments?: Record<string, unknown>
  state: ToolStepState
  elapsedMs?: number
}

/** "name(arg1=v1, arg2=v2)" 摘要（对齐 agent_core 的 tool 进度文案，取自前 2 参数） */
function argsSummary(args?: Record<string, unknown>): string {
  if (!args) return ''
  return Object.entries(args)
    .slice(0, 2)
    .map(([k, v]) => `${k}=${String(v)}`)
    .join(', ')
}

function StateGlyph({ state }: { state: ToolStepState }) {
  if (state === 'done') {
    // 完成：✓（M1 黑白画布，中性色）
    return <Check size={12} className="shrink-0 text-ink-2" aria-label="完成" />
  }
  if (state === 'error') {
    // 失败：✕（语义色——A股语境里绿=坏，复用唯一保留的语义色）
    return <X size={12} className="shrink-0 text-fall" aria-label="失败" />
  }
  // pending / running：6px 圆点（running 呼吸脉冲，accent 占位=当前画布主色）
  const running = state === 'running'
  return (
    <span
      className="inline-block size-[6px] shrink-0 rounded-full"
      style={{
        background: running ? 'var(--accent)' : 'var(--text-3)',
        opacity: running ? undefined : 0.5,
        animation: running ? 'engine-pulse 1.6s var(--ease) infinite' : undefined,
      }}
      aria-label={running ? '执行中' : '等待'}
    />
  )
}

export function ToolTimeline({ steps }: { steps: ToolStep[] }) {
  return (
    <div className="flex flex-col">
      {steps.map((step, i) => (
        <div
          key={i}
          className="hairline-b flex items-center gap-2 px-2 py-1.5 text-[12px] last:border-b-0"
        >
          <StateGlyph state={step.state} />
          <span className="mono min-w-0 truncate text-ink-2">
            {step.name}({argsSummary(step.arguments)})
          </span>
          <div className="flex-1" />
          {step.elapsedMs != null ? (
            <span className="num shrink-0 text-[11px] text-ink-3">{fmtMs(step.elapsedMs)}</span>
          ) : null}
        </div>
      ))}
    </div>
  )
}