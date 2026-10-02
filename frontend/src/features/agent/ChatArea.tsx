import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, ExternalLink, Send, Sparkles, Square } from 'lucide-react'
import { api } from '../../lib/api'
import type { RetrievalSource, SessionMessage } from '../../types/api'
import { MarkdownContent } from '../../components/engine/MarkdownContent'
import { ThinkingFlow } from '../../components/engine/ThinkingFlow'
import { ToolTimeline } from '../../components/engine/ToolTimeline'
import { useAgentRun, type AgentRunPhase } from './useAgentRun'
import { isSafeExternalUrl } from '../../lib/url'
import { Btn, Spinner } from '../../components/ui/primitives'

/**
 * AI 对话主区：历史（/api/agent/sessions/{id}/messages）+ 流式新消息
 * （POST /api/agent/chat/stream SSE → 思考流 + 工具时间线 + Markdown）。
 *
 * 会话切换不靠 effect 重置——所有回显/运行视图都以「本次运行的起始会话」为键做显示门控：
 * - liveUser 只在本会话（live.sessionId === activeId）时显示
 * - run.phase 也只在本会话（live.sessionId === activeId）时显示
 *   （不用 phase.sessionId：它会被 done 事件覆写成服务端新会话 id，见下方 BUG-001 注释）
 * 切走后旧流事件仍被消费但不影响当前视图；再次进入时自动隐藏。
 */
export function ChatArea({
  activeId,
  apiKeyConfigured,
  demoMode,
  models,
  onSessionAdopted,
}: {
  activeId: number | null
  apiKeyConfigured: boolean
  /** v1.1：演示模式当前值（agent-config.demo_mode，跨页 invalidate 即时可见） */
  demoMode: boolean
  models: { chat: string; reasoner: string }
  /**
   * 服务端落库的会话 id **回写**入口（生产 = `AiChatPage` 的 `useSessionStore.setActiveId`）。
   * 必填而非可选：漏接即 BUG-002 原样复发（新会话第 2 条另起会话 + 上一条回答消失）。
   */
  onSessionAdopted: (sessionId: number) => void
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
  // ⚠️ 归属判断必须用**本次运行的起始会话** live.sessionId（与 liveUser 同一模式）：
  //    phase.sessionId 会在 done 事件里被覆写成服务端新会话 id，而新会话的 activeId 仍是 null
  //    ⇒ 用 phase.sessionId 判断会在回答落地的瞬间把回答视图卸载（BUG-001，2026-10-02）
  const liveUser = live && live.sessionId === activeId ? live.user : null
  const runVisible = phase.status !== 'idle' && live != null && live.sessionId === activeId

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
          // BUG-002（2026-10-03 二路审计 F2）：服务端落库的会话 id 必须回写到**会话归属**。
          // 新会话（activeId == null）里若把 done 带回的 sid 丢掉：
          //   ① 追问仍以 sessionId=null 请求 ⇒ 后端 `ensure_session` 再建一个会话（会话分裂）；
          //   ② 第 1 条的问答只活在 phase.content 里，第 2 次 `run.start` 重置即丢
          //      （历史因 `activeId == null` 不加载）⇒ 上一条回答消失。
          // ⚠️ 采纳时 live.sessionId 必须**同步**切到 sid：runVisible / liveUser 的键是
          //    「本次会话归属」（= activeId）。只切 activeId 不切 live.sessionId，会在 done
          //    的同一帧把运行视图门控卸载（runVisible=false），而历史里那条回答又会被
          //    `dedupeHistory` 当作「已由运行视图展示」跳过 ⇒ 两条都看不见。
          if (activeId == null) {
            setLive((l) => (l ? { ...l, sessionId: sid } : l))
            onSessionAdopted(sid)
          }
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
        {demoMode ? (
          <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[10.5px] text-ink-2">
            演示模式 · 本地示例数据
          </span>
        ) : (
          <span className="mono text-[10.5px] text-ink-3">{models.reasoner}</span>
        )}
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
        {!apiKeyConfigured && !demoMode ? (
          <div className="mx-auto mb-1.5 w-full max-w-[780px] px-1 text-[11px] text-ink-3">
            未配置 API Key · 当前为体验降级（配置后可获取实时行情与 AI 点评）
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
            placeholder={apiKeyConfigured ? '输入问题，Enter 发送 / Shift+Enter 换行' : (demoMode ? '演示模式已开启 · 输入问题体验完整流程' : '未配置 Key · 输入问题体验降级流程（可在设置开启演示模式）')}
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
  // 记忆显性化 chip（v1.1 + M2）：按服务端实际注入的 sources 动态显示（无注入不显示）
  // ⚠️ 2026-10-02 审计 F6：原先白名单只有 holdings/history ⇒ M2 的长期记忆
  //    （long_term / preferences / facts / experiences）在 UI 上**完全不可见**。
  const longTermSources = ['long_term', 'preferences', 'facts', 'experiences']
  const memLabel = [
    phase.memorySources.includes('holdings') ? '你的持仓' : '',
    phase.memorySources.includes('history') ? '历史对话' : '',
    phase.memorySources.some((s) => longTermSources.includes(s)) ? '长期记忆' : '',
  ]
    .filter(Boolean)
    .join(' · ')

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
      {memLabel ? (
        <div className="flex items-center gap-1 px-1 text-[11px] text-ink-3">
          <Sparkles size={11} className="text-accent" />
          <span>已结合{memLabel}</span>
        </div>
      ) : null}
      <div className="max-w-[92%] rounded-card border border-hairline bg-surface px-3 py-2 text-[13px] leading-relaxed text-ink">
        {phase.content ? (
          <MarkdownContent content={phase.content} sources={phase.sources} />
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
      <SourceList sources={phase.sources} />
    </div>
  )
}

/**
 * A2 引用回跳的「来源」卡列表（回答下方）。
 *
 * - 编号 `[n]` 与 `sources` 下标 1-based 对应（正文上标点击后滚到这里并高亮 1.5s）
 * - `url` 与 `title` **都为空**的条目（`none` 档：后端刻意剥掉引用凭据）显示
 *   「（该条证据充分性未确认，不提供来源凭据）」且**不可点外链** —— 不给假凭据
 * - A-R1 F5：`url` 仅 `http`/`https` 才渲染可点外链，其余 scheme（`javascript:`/`data:`…）
 *   只显示纯文本、不给 `href`（`lib/url.ts::isSafeExternalUrl`）
 * - 空态不渲染（无 sources 时行为与改造前完全一致）
 */
function SourceList({ sources }: { sources: RetrievalSource[] }) {
  if (!sources.length) return null
  return (
    <div className="flex max-w-[92%] flex-col gap-1">
      <div className="px-1 text-[11px] tracking-wide text-ink-3">来源</div>
      {sources.map((s, i) => {
        const n = i + 1
        const hasCredential = Boolean(s.url || s.title)
        // A-R1 F5：外链 scheme 白名单 —— 只有 http/https 才渲染可点 `<a>`（其余降级为纯文本）。
        // 后端 `extract_sources` 已做数据层收窄，这里是不依赖 React 版本行为的第二道。
        const safeUrl = isSafeExternalUrl(s.url) ? s.url : null
        return (
          <div
            key={`${s.chunk_id ?? 'x'}-${n}`}
            id={`src-${n}`}
            className="scroll-mt-4 rounded-tile border border-hairline bg-surface px-2.5 py-1.5 text-[11.5px]"
          >
            {hasCredential ? (
              <>
                <div className="flex items-start gap-2">
                  <span className="mono shrink-0 text-ink-3">[{n}]</span>
                  <span className="flex-1 leading-relaxed text-ink">
                    {s.title || '（未提供标题）'}
                    {s.is_table ? <span className="ml-1 text-ink-3">· 表格块</span> : null}
                  </span>
                  <span className="shrink-0 text-[10.5px] text-ink-3">
                    {s.source || ''}
                    {s.source && s.published_at ? ' · ' : ''}
                    {s.published_at || ''}
                  </span>
                  {safeUrl ? (
                    <a
                      href={safeUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      title="打开原文"
                      className="shrink-0 text-ink-3 hover:text-ink"
                    >
                      <ExternalLink size={11} />
                    </a>
                  ) : null}
                </div>
                {s.url ? (
                  <div className="mono mt-0.5 truncate pl-6 text-[10.5px] text-ink-3">{s.url}</div>
                ) : null}
              </>
            ) : (
              <div className="flex items-start gap-2">
                <span className="mono shrink-0 text-ink-3">[{n}]</span>
                <span className="flex-1 leading-relaxed text-ink-3">
                  （该条证据充分性未确认，不提供来源凭据）
                </span>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}