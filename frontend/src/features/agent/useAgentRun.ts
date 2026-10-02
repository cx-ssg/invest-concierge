import { useEffect, useRef, useState } from 'react'
import { chatStream } from '../../lib/api'
import type { JudgeItem, RetrievalSource, SSEEvent, ToolTraceEntry } from '../../types/api'
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
  /** 记忆显性化（v1.1）：memory_used 事件带来的注入来源（holdings/history），无注入为空 */
  memorySources: string[]
  /**
   * A2 引用回跳：`retrieve_docs` 的 tool_end 带来的检索来源。
   * 多次检索**按 chunk_id 去重并保留首次出现顺序** —— 编号 `[n]` 与数组下标 1-based 对应。
   */
  sources: RetrievalSource[]
  /**
   * B1 判官：`chunk_id → 结论`（`evidence_judged` 事件，仅 weak 档触发、`done` 之后才到）。
   * 没有结论的卡片**不标注**（`checked=false` 时 items 为空 ⇒ 保持沉默，不编造"未确认"）。
   */
  judge: Record<number, JudgeItem>
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
  memorySources: [],
  sources: [],
  judge: {},
  content: '',
  sessionId: null,
  toolTrace: null,
  error: null,
}

/** A2：把一次检索事件的 sources 并入已有列表（按 chunk_id 去重，保留首次出现顺序）。
 *  无 chunk_id 的条目无法判重（真实返回恒有该字段），按出现顺序追加，不静默丢弃。 */
function mergeSources(prev: RetrievalSource[], incoming?: RetrievalSource[]): RetrievalSource[] {
  if (!incoming?.length) return prev
  const seen = new Set(prev.map((s) => s.chunk_id))
  const out = prev.slice()
  for (const s of incoming) {
    if (s.chunk_id != null) {
      if (seen.has(s.chunk_id)) continue
      seen.add(s.chunk_id)
    }
    out.push(s)
  }
  return out
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
      // A2：来源与工具行定态互不依赖 —— 事件缺 tool_start 时也要留下来源（不丢引用凭据）
      const merged: AgentRunPhase = { ...p, sources: mergeSources(p.sources, ev.sources) }
      // 按「最后一个同名 running 行」定态（同工具多次调用按顺序落位）
      let idx = -1
      for (let i = merged.toolSteps.length - 1; i >= 0; i--) {
        if (merged.toolSteps[i].name === ev.name && merged.toolSteps[i].state === 'running') {
          idx = i
          break
        }
      }
      if (idx < 0) return merged
      const steps = merged.toolSteps.slice()
      steps[idx] = {
        ...steps[idx],
        state: ev.ok ? 'done' : 'error',
        elapsedMs: ev.elapsed_ms,
      }
      return { ...merged, toolSteps: steps }
    }
    case 'memory_used':
      // v1.1 记忆显性化：服务端只在真实注入时发；无注入不发（不撒谎）
      return { ...p, memorySources: ev.sources?.length ? ev.sources : p.memorySources }
    case 'evidence_judged': {
      // B1 判官：`done` 之后到达；按 chunk_id 合并（同一次运行可能有多轮检索）。
      // `items` 为空（checked=false：超时/无 LLM/解析失败）⇒ 不做任何标注。
      const items = ev.items ?? []
      if (!items.length) return p
      const judge = { ...p.judge }
      for (const it of items) {
        if (it.chunk_id != null) judge[it.chunk_id] = it
      }
      return { ...p, judge }
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