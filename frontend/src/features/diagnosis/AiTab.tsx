import { useState } from 'react'
import { Bot, MessageSquareText, Send, Square } from 'lucide-react'
import type { DiagnosisPayload } from '../../types/api'
import { useAgentRun } from '../agent/useAgentRun'
import { buildDiagnosisContext } from './stockData'
import { ThinkingFlow } from '../../components/engine/ThinkingFlow'
import { ToolTimeline } from '../../components/engine/ToolTimeline'
import { MarkdownContent } from '../../components/engine/MarkdownContent'
import { Btn, Card, Kicker, Spinner } from '../../components/ui/primitives'
import { fmtMs } from '../../lib/format'

/** 多角色辩论任务（对齐 multi_agent_stock_analysis 的角色设定，走 Agent SSE 通道） */
const DEBATE_TASK = `请基于本次上下文中的诊断数据，扮演 4 位分析师依次分析并辩论：
1. 基本面分析师 📊（ROE/毛利率/成长性/财务健康）
2. 技术分析师 📈（走势与量价结构，用诊断数据推断）
3. 情绪分析师 💬（市场情绪与关注度）
4. 风控分析师 🛡️（排雷风险点与估值安全边际）
每位分析师给出独立观点（80-150 字），随后主席汇总分歧并给出最终综合判断与评级
（强烈推荐/推荐/中性/谨慎/回避）。以 Markdown 组织输出。`

/** Tab AI：多智能体辩论 + 基于诊断追问（都走 POST /api/agent/chat/stream → 实时工具时间线） */
export function AiTab({ d, name, apiKeyConfigured }: { d: DiagnosisPayload; name: string; apiKeyConfigured: boolean }) {
  const [question, setQuestion] = useState('')
  const debate = useAgentRun()
  const follow = useAgentRun()

  const busy = debate.phase.status === 'streaming' || follow.phase.status === 'streaming'
  const active = debate.phase.status === 'streaming' ? 'debate' : follow.phase.status === 'streaming' ? 'follow' : null
  const runView = active === 'debate' ? debate.phase : active === 'follow' ? follow.phase : debate.phase.status === 'done' ? debate.phase : follow.phase.status === 'done' ? follow.phase : null

  const context = () => buildDiagnosisContext(d, name)

  function startDebate() {
    if (busy) return
    follow.reset()
    debate.start(DEBATE_TASK, { context: context() })
  }

  function startFollow() {
    const q = question.trim()
    if (!q || busy) return
    debate.reset()
    follow.start(q, { context: context() })
  }

  return (
    <div className="flex flex-col gap-3">
      {!apiKeyConfigured ? (
        <Card className="p-3">
          <div className="text-[13px] font-medium text-ink-2">🔑 未配置 API Key</div>
          <div className="mt-1 text-[12.5px] leading-relaxed text-ink-3">
            还没有配置 DeepSeek API Key，AI 辩论 / 追问将以内置演示数据降级答复（流程与时间线可用）。
            <br />
            1. 在项目目录创建 <span className="mono">local_env.bat</span>，写入{' '}
            <span className="mono">set DEEPSEEK_API_KEY=你的key</span>
            <br />
            2. 设置系统环境变量 <span className="mono">DEEPSEEK_API_KEY</span> 后重启终端
          </div>
        </Card>
      ) : null}

      <Card className="p-3">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Bot size={14} className="text-ink-2" />
          多智能体辩论
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
          点击后 4 位分析师（基本面 📊 / 技术 📈 / 情绪 💬 / 风控 🛡️）基于上方 5 大引擎数据依次分析并辩论
          （约 30-60 秒），工具链时间线实时展示每一步。
        </p>
        <div className="mt-2">
          {active === 'debate' ? (
            <Btn variant="danger" onClick={debate.abort}>
              <Square size={12} fill="currentColor" /> 停止辩论
            </Btn>
          ) : (
            <Btn variant="primary" onClick={startDebate} disabled={busy}>
              {debate.phase.status === 'streaming' ? <Spinner size={11} /> : <Bot size={13} />}
              开始多智能体辩论
            </Btn>
          )}
        </div>
      </Card>

      <Card className="p-3">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <MessageSquareText size={14} className="text-ink-2" />
          基于这份诊断追问
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
          结合上方诊断数据 + 跨页会话记忆，向 AI 提出进一步问题（例如：结合排雷和估值，当前主要风险是什么？）。
        </p>
        <div className="mt-2 flex items-end gap-2">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                startFollow()
              }
            }}
            placeholder="例如：最大的风险点是什么？现在值得关注吗？"
            rows={2}
            className="max-h-28 min-h-9 flex-1 resize-y rounded-tile border border-hairline bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
          />
          {active === 'follow' ? (
            <Btn variant="danger" onClick={follow.abort}>
              <Square size={12} fill="currentColor" /> 停止
            </Btn>
          ) : (
            <Btn variant="primary" onClick={startFollow} disabled={busy || !question.trim()}>
              <Send size={13} /> 追问
            </Btn>
          )}
        </div>
      </Card>

      {/* 运行结果（思考流 + 时间线 + Markdown 正文） */}
      {runView ? (
        <div className="flex flex-col gap-2">
          {runView.reasoning ? (
            <ThinkingFlow text={runView.reasoning} streaming={runView.status === 'streaming'} defaultOpen={runView.status === 'streaming'} />
          ) : null}
          {runView.toolSteps.length ? (
            <div className="rounded-tile border border-hairline bg-bg px-2 py-1.5">
              <div className="flex items-center justify-between px-2 pt-1 text-[11px] tracking-wide text-ink-3">
                <span>工具链时间线</span>
                {runView.status === 'done' && runView.toolSteps.length ? (
                  <span className="mono text-[10.5px]">
                    {runView.toolSteps.length} 次 · 共{' '}
                    {fmtMs(runView.toolSteps.reduce((a, s) => a + (s.elapsedMs ?? 0), 0))}
                  </span>
                ) : null}
              </div>
              <div className="mt-1">
                <ToolTimeline steps={runView.toolSteps} />
              </div>
            </div>
          ) : null}
          <div className="rounded-card border border-hairline bg-surface px-3 py-2.5">
            {runView.content ? (
              <MarkdownContent content={runView.content} />
            ) : runView.status === 'streaming' ? (
              <span className="flex items-center gap-1.5 text-[13px] text-ink-3">
                <Spinner size={11} /> {runView.writing || 'Agent 思考中…'}
              </span>
            ) : runView.status === 'error' ? (
              <span className="text-[13px] text-fall">运行失败：{runView.error}</span>
            ) : runView.status === 'cancelled' ? (
              <span className="text-[13px] text-ink-3">已取消本次 AI 分析</span>
            ) : null}
          </div>
        </div>
      ) : (
        <Card className="p-3">
          <Kicker>AI 输出</Kicker>
          <div className="mt-1 text-[12.5px] text-ink-3">
            点击上方按钮后，这里会实时展示思考链、工具时间线（✓/●/✕ + 耗时）与最终答复。
          </div>
        </Card>
      )}
    </div>
  )
}