import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { SessionSidebar } from '../features/agent/SessionSidebar'
import { ChatArea } from '../features/agent/ChatArea'

/**
 * AI 对话（M2）：会话侧栏（列表/新建/右键重命名·置顶·归档·删除）
 * + SSE 流式对话 + 工具链时间线；无 Key 时演示降级。
 */
export function AiChatPage() {
  const [activeId, setActiveId] = useState<number | null>(null)
  const { data: config } = useQuery({ queryKey: ['agent-config'], queryFn: api.agent.config })

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-3">
      <div className="flex flex-col gap-1">
        <div className="px-1 text-[11px] tracking-[1.5px] text-ink-3 select-none">INVEST CONCIERGE</div>
        <h1 className="px-1 text-lg font-semibold text-ink">AI 对话</h1>
        <p className="px-1 text-[13px] text-ink-2">
          Agent 多轮推理 + 会话记忆 + 工具链实时时间线（SSE 流式）
        </p>
      </div>
      <div
        className="flex min-h-0 flex-1 overflow-hidden rounded-card border border-hairline"
        style={{ background: 'var(--surface)' }}
      >
        <SessionSidebar activeId={activeId} onSelect={setActiveId} />
        <ChatArea
          activeId={activeId}
          apiKeyConfigured={config?.api_key_configured ?? false}
          models={{
            chat: config?.chat_model ?? 'deepseek-chat',
            reasoner: config?.reasoner_model ?? 'deepseek-reasoner',
          }}
        />
      </div>
    </section>
  )
}