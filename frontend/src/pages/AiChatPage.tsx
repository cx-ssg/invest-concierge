import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { ChatArea } from '../features/agent/ChatArea'
import { useSessionStore } from '../stores/session'

/**
 * AI 对话（M2，2026-09-04 布局改版）：
 * 会话列表已收进主侧栏「对话」下方（SessionList 组件），本页只留聊天主体——
 * 全宽对话列 + SSE 流式 + 工具链时间线；activeId 走 useSessionStore 跨组件共享。
 */
export function AiChatPage() {
  const activeId = useSessionStore((s) => s.activeId)
  const { data: config } = useQuery({ queryKey: ['agent-config'], queryFn: api.agent.config })

  return (
    <ChatArea
      activeId={activeId}
      apiKeyConfigured={config?.api_key_configured ?? false}
      models={{
        chat: config?.chat_model ?? 'deepseek-chat',
        reasoner: config?.reasoner_model ?? 'deepseek-reasoner',
      }}
    />
  )
}
