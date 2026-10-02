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
import { act } from 'react'
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

/** 挂载**真** `ChatArea`（含它自己的 `useAgentRun` / SSE 消费 / 显示门控逻辑）。 */
export function mountChatArea(
  container: HTMLElement,
  props: HarnessChatAreaProps,
): { root: Root; queryClient: QueryClient } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  })
  const root = createRoot(container)
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <ChatArea {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { root, queryClient }
}

/** 单独挂 **真** `MarkdownContent`（F3 的代码块保护断言用）。 */
export function renderMarkdown(
  container: HTMLElement,
  content: string,
  sources?: RetrievalSource[],
): Root {
  const root = createRoot(container)
  root.render(
    <MemoryRouter initialEntries={['/']}>
      <MarkdownContent content={content} sources={sources} />
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

