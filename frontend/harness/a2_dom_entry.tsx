/**
 * A2 离线 DOM 回归 harness 的 **React 入口**（任务 A-R1 的 F1/F2/F3）。
 *
 * 为什么需要它：BUG-001（新会话回答不显示）的修复只被人工脚本
 * `scripts/verify_a2r2_chat_visible.py`（需 Edge + 真 LLM + 真后端）覆盖，
 * `pytest` 476 全绿也拦不住把修复改回缺陷版（阶段 A 审计 F1 实测）。
 * 本入口把**真 `ChatArea` 组件**（不是复刻、不是 mock）挂进 jsdom，
 * 由 `scripts/verify_a2_dom.mjs` 喂**桩 SSE**（stub fetch）跑断言。
 *
 * ⚠️ 本文件**不在** `tsconfig.app.json` 的 `include: ["src"]` 里 ⇒ 不进 `npm run build`、
 *    不进 `tsc -b`，对产品构建零影响；只被 `scripts/verify_a2_dom.mjs` 用 rolldown 打包。
 */
import { act, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { ChatArea } from '../src/features/agent/ChatArea'
import { MarkdownContent } from '../src/components/engine/MarkdownContent'
import type { RetrievalSource } from '../src/types/api'

export interface HarnessChatAreaProps {
  activeId: number | null
  apiKeyConfigured: boolean
  demoMode: boolean
  models: { chat: string; reasoner: string }
}

/** 挂载**真** `ChatArea`（含它自己的 `useAgentRun` / SSE 消费 / 显示门控逻辑）。
 *
 * ⚠️ A-R2/BUG-002：`activeId` 在**上层**持有（生产里是 `AiChatPage` 的
 *    `useSessionStore`），`ChatArea` 只能通过 `onSessionAdopted` 回写。
 *    因此 harness 也不再用「写死的 prop」，而是复刻这个壳：state 持有 activeId、
 *    把它传给 ChatArea、并把回写落到同一份 state（否则测不出"服务端新 id 被采纳"）。
 */
export function mountChatArea(
  container: HTMLElement,
  props: HarnessChatAreaProps,
): { root: Root; queryClient: QueryClient; getActiveId: () => number | null } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  })
  const root = createRoot(container)
  // 已渲染的会话归属镜像（断言用；真值在 ChatShell 的 state 里）
  const adopted: { current: number | null } = { current: props.activeId }
  function ChatShell(initial: HarnessChatAreaProps) {
    const [activeId, setActiveId] = useState<number | null>(initial.activeId)
    return (
      <ChatArea
        activeId={activeId}
        apiKeyConfigured={initial.apiKeyConfigured}
        demoMode={initial.demoMode}
        models={initial.models}
        onSessionAdopted={(id) => {
          adopted.current = id
          setActiveId(id)
        }}
      />
    )
  }
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <ChatShell {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { root, queryClient, getActiveId: () => adopted.current }
}

/** 单独挂 **真** `MarkdownContent`（F3 的代码块保护 / H1 的 scope 断言用）。
 *
 * H1：有 `sources` 时 `MarkdownContent` 的 `scope` 在类型上必填（引用命名空间），
 * 因此这里显式分支 —— 无 sources 走不带 scope 的调用（与生产调用方同款）。
 */
export function renderMarkdown(
  container: HTMLElement,
  content: string,
  sources?: RetrievalSource[],
  scope = 'harness',
): Root {
  const root = createRoot(container)
  root.render(
    <MemoryRouter initialEntries={['/']}>
      {sources && sources.length
        ? <MarkdownContent content={content} sources={sources} scope={scope} />
        : <MarkdownContent content={content} />}
    </MemoryRouter>,
  )
  return root
}

/**
 * React `act` 的**本 bundle 实例**再导出。
 * ⚠️ 必须从本 bundle 出，不能在 runner 里 `require('react')` —— 那样会拿到**另一份** React
 *   副本，与组件用的 reconciler 不是同一个实例，act 队列对不上（会静默失效）。
 */
export { act }

