import { useEffect, useRef, useState } from 'react'
import { chatStream } from '../../lib/api'
import type { SSEEvent, ToolTraceEntry } from '../../types/api'
import type { ToolStep } from '../../components/engine/ToolTimeline'

export type RunStatus = 'idle' | 'streaming' | 'done' | 'error' | 'cancelled'

export interface AgentRunPhase {
  status: RunStatus
  /** 模型原生思考链（reasoning 事件逐块累积） */
  reasoning: string
  /** 组织最终回答（writing 事件） */
  writing: string
  /** 工具链时间线（tool_start/tool_end 维护） */
  toolSteps: ToolStep[]
  /** 最终回答（done.content，经打字机流式渲染） */
  content: string
  /** 本运行落库的会话 id（done 事件带回） */
  sessionId: number | null
  toolTrace: ToolTraceEntry[] | null
  error: string | null
}

const IDLE: AgentRunPhase = {
  status: 'idle',
  reasoning: '',
  writing: '',
  toolSteps: [],
  content: '',
  sessionId: null,
  toolTrace: null,
  error: null,
}

function applyEvent(p: AgentRunPhase, ev: SSEEvent): AgentRunPhase {
  switch (ev.type) {
    case 'status':
      return p.status === 'idle' ? { ...p, status: 'streaming' } : p
    case 'reasoning':
      if (!ev.text) return p
      return { ...p, reasoning: p.reasoning ? `${p.reasoning}\n${ev.text}` : ev.text }
    case 'writing':
      return { ...p, writing: ev.text || '组织最终回答' }
    case 'tool':
      // 字符串回退事件（无 tool_start 时）；结构化路径下 tool_start 已覆盖，忽略
      return p
    case 'tool_start': {
      return {
        ...p,
        toolSteps: [...p.toolSteps, { name: ev.name, arguments: ev.arguments, state: 'running' as const }],
      }
    }
    case 'tool_end': {
      // 按「最后一个同名 running 行」定态（同工具多次调用按顺序落位）
      let idx = -1
      for (let i = p.toolSteps.length - 1; i >= 0; i--) {
        if (p.toolSteps[i].name === ev.name && p.toolSteps[i].state === 'running') {
          idx = i
          break
        }
      }
      if (idx < 0) return p
      const steps = p.toolSteps.slice()
      steps[idx] = {
        ...steps[idx],
        state: ev.ok ? 'done' : 'error',
        elapsedMs: ev.elapsed_ms,
      }
      return { ...p, toolSteps: steps }
    }
    case 'done':
      return {
        ...p,
        status: 'done',
        content: ev.content ?? p.content,
        sessionId: ev.session_id ?? p.sessionId,
        toolTrace: ev.tool_trace ?? p.toolTrace,
      }
    case 'error':
      return { ...p, status: 'error', error: ev.message || '未知错误' }
    default:
      return p
  }
}

export interface StartOptions {
  sessionId?: number | null
  context?: string[]
  /** done 事件回调（外层可用做查询失效/切会话） */
  onDone?: (sessionId: number | null) => void
}

/**
 * SSE Agent 运行流：发一条任务 → 消费 /api/agent/chat/stream 事件 →
 * 维护 reasoning / toolSteps / content 状态。
 * 页面（AI Chat / 诊断 AI tab）各自实例化；abort() 取消（服务端 ClientDisconnect）。
 */
export function useAgentRun() {
  const [phase, setPhase] = useState<AgentRunPhase>(IDLE)
  const [runId, setRunId] = useState(0)
  const abortRef = useRef<AbortController | null>(null)
  const runIdRef = useRef(0)

  // 卸载即中断（防止离页后继续拉流）
  useEffect(() => {
    return () => {
      runIdRef.current += 1
      abortRef.current?.abort()
    }
  }, [])

  function markCancelled() {
    setPhase((p) => {
      if (p.status !== 'streaming') return p
      return {
        ...p,
        status: 'cancelled',
        error: null,
        toolSteps: p.toolSteps.map((s) =>
          s.state === 'running' ? { ...s, state: 'error' as const, elapsedMs: undefined } : s,
        ),
      }
    })
  }

  function abort() {
    abortRef.current?.abort()
    markCancelled()
  }

  function reset() {
    runIdRef.current += 1
    setRunId(runIdRef.current)
    abortRef.current?.abort()
    setPhase(IDLE)
  }

  async function start(task: string, opts?: StartOptions) {
    const text = (task ?? '').trim()
    if (!text) return
    abortRef.current?.abort()
    const id = ++runIdRef.current
    setRunId(id)
    const ctrl = new AbortController()
    abortRef.current = ctrl

    setPhase({
      ...IDLE,
      status: 'streaming',
      sessionId: opts?.sessionId ?? null,
    })

    try {
      for await (const ev of chatStream(
        { task: text, session_id: opts?.sessionId ?? null, context: opts?.context },
        ctrl.signal,
      )) {
        if (id !== runIdRef.current) return // 已被新 run / reset 取代
        setPhase((p) => applyEvent(p, ev))
        if (ev.type === 'done') opts?.onDone?.(ev.session_id)
      }
      // 服务端正常收流但缺 done（极端）：兜底置完成，避免永久转圈
      if (id === runIdRef.current) {
        setPhase((p) => (p.status === 'streaming' ? { ...p, status: 'done' } : p))
      }
    } catch (e) {
      if (id !== runIdRef.current) return
      if (ctrl.signal.aborted) {
        // 用户取消：running 行置 ✕「已取消」
        markCancelled()
      } else {
        setPhase((p) => ({
          ...p,
          status: 'error',
          error: e instanceof Error ? e.message : String(e),
        }))
      }
    }
  }

  return { phase, start, abort, reset, runId }
}