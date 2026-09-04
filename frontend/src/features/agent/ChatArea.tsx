import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Send, Square } from 'lucide-react'
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
  const navigate = useNavigate()
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

  // 显示门控：只展示属于当前会话的回显和运行视图。
  // runVisible 加 idle 排除：新会话空闲时 sessionId 与 activeId 同为 null，
  // 恒真会把空状态工作台永久顶掉（luna 评审"首屏空白"的根因，2026-09-04 修复）
  const liveUser = live && live.sessionId === activeId ? live.user : null
  const runVisible = phase.status !== 'idle' && phase.sessionId === activeId

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

  /** 发送一条消息并走完整运行（send 与快捷问题共用：回显/命名/失效缓存/滚底） */
  function startRun(text: string) {
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

  async function send() {
    const text = input.trim()
    if (!text || streaming) return
    setInput('')
    startRun(text)
  }

  /** 空状态快捷问题：点击即发送（UI_POLISH_PLAN §2/§4） */
  function askQuick(question: string) {
    if (streaming) return
    startRun(question)
  }

  return (
    <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col">
      {/* 头部：会话信息 + 模型 */}
      <header className="hairline-b flex h-10 shrink-0 items-center gap-2 px-3">
        <span className="text-[12.5px] font-medium text-ink-2">
          会话{activeId != null ? ` #${activeId}` : '（新）'}
          <span className="ml-2 font-normal text-ink-3">上下文记忆已开启</span>
        </span>
        <div className="flex-1" />
        <span className="mono text-[10.5px] text-ink-3">{models.reasoner}</span>
      </header>

      {/* 消息区（等宽可读列） */}
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        <div className="mx-auto flex w-full max-w-[780px] flex-col gap-4">
          {!activeId && !liveUser && !runVisible ? (
            <EmptyWorkbench onAsk={(q) => void askQuick(q)} />
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
          <div className="mx-auto mb-1.5 w-full max-w-[780px] px-1 text-[11px] text-ink-3">
            演示模式 · 配置数据引擎后可获取实时行情
            <button
              type="button"
              onClick={() => navigate('/settings')}
              className="ml-2 cursor-pointer underline underline-offset-2 hover:text-ink-2"
            >
              前往设置
            </button>
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

/** 首屏工作台开场（UI_POLISH_PLAN §2/§4）：标题+说明+3 条快捷问题（点击即发送），
 *  落在 --bg 上、无插画无渐变，位于首屏视觉中心偏上（top 22vh）。 */
const QUICK_PROMPTS = [
  '分析贵州茅台（600519）今日走势，结合成交量和资金变化',
  '对比沪深300和中证500近一年表现，给出适合当前市场环境的观察点',
  '根据我的持仓，找出近期基本面没有明显恶化的标的',
]

function EmptyWorkbench({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <div className="mx-auto flex w-full max-w-[640px] flex-col gap-3 pt-[22vh]">
      <div className="flex flex-col gap-1.5 text-left">
        <div className="text-[16px] font-semibold text-ink">从今天的市场问题开始</div>
        <div className="text-[12.5px] leading-relaxed text-ink-3">
          输入股票、指数或组合，生成行情与基本面分析
        </div>
      </div>
      <div className="flex flex-col rounded-tile border border-hairline">
        {QUICK_PROMPTS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onAsk(q)}
            className="group flex min-h-9 cursor-pointer items-center gap-2 border-b border-hairline bg-transparent px-3 text-left text-[12.5px] text-ink-2 transition-colors last:border-b-0 hover:bg-surface hover:text-ink"
            style={{ transitionDuration: 'var(--dur)' }}
          >
            <span className="flex-1 leading-relaxed">{q}</span>
            <ArrowRight size={13} className="shrink-0 text-ink-3 group-hover:text-ink" />
          </button>
        ))}
      </div>
    </div>
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