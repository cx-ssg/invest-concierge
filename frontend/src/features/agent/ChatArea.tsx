import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Send, Square } from 'lucide-react'
import { api } from '../../lib/api'
import type { SessionMessage } from '../../types/api'
import { MarkdownContent } from '../../components/engine/MarkdownContent'
import { ThinkingFlow } from '../../components/engine/ThinkingFlow'
import { ToolTimeline } from '../../components/engine/ToolTimeline'
import { useAgentRun, type AgentRunPhase } from './useAgentRun'
import { Btn, Spinner } from '../../components/ui/primitives'

/**
 * AI 对话主区：历史（/api/agent/sessions/{id}/messages）+ 流式新消息
 * （POST /api/agent/chat/stream SSE → 思考流 + 工具时间线 + Markdown）。
 *
 * 会话切换不靠 effect 重置——所有回显/运行视图都以 sessionId 为键做显示门控：
 * - liveUser 只在本会话（live.sessionId === activeId）时显示
 * - run.phase 也只在本会话（phase.sessionId === activeId）时显示
 * 切走后旧流事件仍被消费但不影响当前视图；再次进入时自动隐藏。
 */
export function ChatArea({
  activeId,
  apiKeyConfigured,
  models,
}: {
  activeId: number | null
  apiKeyConfigured: boolean
  models: { chat: string; reasoner: string }
}) {
  const qc = useQueryClient()
  const [input, setInput] = useState('')
  const [live, setLive] = useState<{ sessionId: number | null; user: string } | null>(null)
  const run = useAgentRun()
  const bottomRef = useRef<HTMLDivElement>(null)

  const historyQ = useQuery({
    queryKey: ['session-messages', activeId],
    queryFn: () => (activeId != null ? api.agent.sessionMessages(activeId) : Promise.resolve([])),
    enabled: activeId != null,
    staleTime: 0,
  })

  const phase = run.phase
  const streaming = phase.status === 'streaming'

  // 显示门控：只展示属于当前会话的回显和运行视图
  const liveUser = live && live.sessionId === activeId ? live.user : null
  const runVisible = phase.sessionId === activeId

  // 自动滚底（新内容 / 新工具行 / 新思考块 / 历史加载）
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [phase.content.length, phase.toolSteps.length, phase.reasoning.length, historyQ.data?.length])

  function dedupeHistory(
    list: SessionMessage[],
    liveUserMsg: string | null,
    liveAssistant: string | null,
  ): SessionMessage[] {
    const out = list.slice()
    if (liveUserMsg && out.length && out[out.length - 1].role === 'user' && out[out.length - 1].content === liveUserMsg) out.pop()
    if (liveAssistant && out.length && out[out.length - 1].role === 'assistant' && out[out.length - 1].content === liveAssistant) out.pop()
    return out
  }

  const doneContent = phase.status === 'done' ? phase.content : null
  const history = activeId != null ? dedupeHistory(historyQ.data ?? [], liveUser, doneContent) : []

  async function send() {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    setLive({ sessionId: activeId, user: text })
    run.start(text, {
      sessionId: activeId,
      onDone: (sid) => {
        void qc.invalidateQueries({ queryKey: ['agent-sessions'] })
        if (sid != null) {
          void qc.invalidateQueries({ queryKey: ['session-messages', sid] })
          // 未命名会话 → 用问题前 20 字自动命名（对齐 agent_run 隐式命名口径）
          void api.agent.sessions().then((list) => {
            const s = list.find((x) => x.id === sid)
            if (s && !s.title) {
              void api.agent
                .patchSession(sid, 'rename', text.slice(0, 20))
                .then(() => void qc.invalidateQueries({ queryKey: ['agent-sessions'] }))
            }
          })
        }
      },
    })
    // 新用户气泡出现即滚底
    window.setTimeout(() => bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }), 60)
  }

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* 头部：会话信息 + 模型 */}
      <header className="hairline-b flex h-10 shrink-0 items-center gap-2 px-3">
        <span className="text-[12.5px] font-medium text-ink-2">
          会话{activeId != null ? ` #${activeId}` : '（新）'}
          <span className="ml-2 font-normal text-ink-3">同会话追问自动延续记忆</span>
        </span>
        <div className="flex-1" />
        <span className="mono text-[10.5px] text-ink-3">{models.reasoner}</span>
      </header>

      {/* 消息区（等宽可读列） */}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <div className="mx-auto flex w-full max-w-[780px] flex-col gap-4">
          {!activeId && !liveUser && !runVisible ? (
            <div className="mt-10 flex flex-col items-center gap-2 text-center">
              <div className="text-[15px] font-medium text-ink-2">invest-concierge Agent</div>
              <div className="max-w-[420px] text-[12.5px] leading-relaxed text-ink-3">
                输入问题后，Agent 会规划步骤、自动调用行情 / 诊断 / 排雷 / 估值等工具，
                实时展示思考链与工具时间线，并用有数据支撑的方式回答。
                未配置 API Key 时以内置演示数据降级答复。
              </div>
            </div>
          ) : null}

          {history.map((m, i) =>
            m.role === 'user' ? (
              <div key={`h-${i}`} className="flex justify-end">
                <div className="max-w-[78%] rounded-card border border-hairline bg-surface-2 px-3 py-2 text-[13px] whitespace-pre-wrap break-words text-ink">
                  {m.content}
                </div>
              </div>
            ) : (
              <div key={`h-${i}`} className="max-w-[92%] rounded-card border border-hairline bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink">
                <MarkdownContent content={m.content} />
              </div>
            ),
          )}

          {liveUser ? (
            <div className="flex justify-end">
              <div className="max-w-[78%] rounded-card border border-hairline bg-surface-2 px-3 py-2 text-[13px] whitespace-pre-wrap break-words text-ink">
                {liveUser}
              </div>
            </div>
          ) : null}

          {/* 运行中 / 刚完成的助手视图（思考流 + 工具时间线 + 正文） */}
          {liveUser && runVisible ? <AssistantRunView key={run.runId} phase={phase} /> : null}

          <div ref={bottomRef} />
        </div>
      </div>

      {/* 输入区 */}
      <footer className="hairline-t shrink-0 px-3 pb-2 pt-2">
        {!apiKeyConfigured ? (
          <div className="mx-auto mb-2 w-full max-w-[780px] rounded-tile border border-hairline bg-surface px-3 py-1.5 text-[12px] text-ink-2">
            未配置 <span className="mono">DEEPSEEK_API_KEY</span> —— 当前为演示降级模式：
            Agent 用内置示例数据走完整流程（工具时间线可用），配置 Key 后恢复真实答复。
          </div>
        ) : null}
        <div className="mx-auto flex w-full max-w-[780px] items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                void send()
              }
            }}
            placeholder={apiKeyConfigured ? '输入问题，Enter 发送 / Shift+Enter 换行' : '（演示模式）输入问题体验完整流程'}
            rows={1}
            className="max-h-28 min-h-9 flex-1 resize-y rounded-tile border border-hairline bg-surface px-2.5 py-2 text-[13px] leading-relaxed text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
          />
          {streaming ? (
            <Btn variant="danger" onClick={run.abort} title="取消本次回答">
              <Square size={12} fill="currentColor" /> 停止
            </Btn>
          ) : (
            <Btn variant="primary" onClick={() => void send()} disabled={!input.trim()}>
              <Send size={13} /> 发送
            </Btn>
          )}
        </div>
      </footer>
    </section>
  )
}

/** 运行中/完成态的助手视图 */
function AssistantRunView({ phase }: { phase: AgentRunPhase }) {
  const streaming = phase.status === 'streaming'
  const error = phase.status === 'error'
  const cancelled = phase.status === 'cancelled'

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {phase.reasoning ? <ThinkingFlow text={phase.reasoning} streaming={streaming} defaultOpen={streaming} /> : null}
      {phase.toolSteps.length ? (
        <div className="rounded-tile border border-hairline bg-bg px-2 py-1.5">
          <div className="px-2 pt-1 text-[11px] tracking-wide text-ink-3">工具链时间线</div>
          <div className="mt-1">
            <ToolTimeline steps={phase.toolSteps} />
          </div>
        </div>
      ) : null}
      <div className="max-w-[92%] rounded-card border border-hairline bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink">
        {phase.content ? (
          <MarkdownContent content={phase.content} />
        ) : streaming ? (
          <span className="flex items-center gap-1.5 text-ink-3">
            <Spinner size={11} /> {phase.writing || 'Agent 思考中…'}
          </span>
        ) : error ? (
          <span className="text-fall">运行失败：{phase.error}</span>
        ) : cancelled ? (
          <span className="text-ink-3">已取消本次回答（会话已落库可回放）</span>
        ) : null}
      </div>
    </div>
  )
}