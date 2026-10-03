/**
 * H5 离线 DOM 回归 harness 的 React 入口（市场行情 / 自选股两个新页面）。
 *
 * 为什么需要它：H5 的两个新页面只被「真后端 + 真数据源」的 API 证据覆盖，
 * 而 pytest 只测后端契约、`tsc -b` 只测类型——页面**渲染语义**（tab 切换、
 * 涨跌色、降级文案、持仓市值计算）没有任何自动化闸门。
 * 本入口把**真 `MarketPage` / `WatchlistPage` 组件**（不是复刻）挂进 jsdom，
 * 由 `scripts/verify_h5_pages.mjs` 喂桩 HTTP 跑断言，并支持变异自检（证明 harness 有牙）。
 *
 * ⚠️ 本文件**不在** `tsconfig.app.json` 的 `include: ["src"]` 里 ⇒ 不进 `npm run build`、
 *    不进 `tsc -b`，对产品构建零影响；只被 `scripts/verify_h5_pages.mjs` 用 rolldown 打包。
 */
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import { MarketPage } from '../src/pages/MarketPage'
import { WatchlistPage } from '../src/pages/WatchlistPage'

function mountWithShell(container: HTMLElement, node: React.ReactNode): { root: Root } {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } },
  })
  const root = createRoot(container)
  root.render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>{node}</MemoryRouter>
    </QueryClientProvider>,
  )
  return { root }
}

/** 挂**真** `MarketPage`（含五个 tab 各自的数据请求与降级分支） */
export function mountMarketPage(container: HTMLElement): { root: Root } {
  return mountWithShell(container, <MarketPage />)
}

/** 挂**真** `WatchlistPage`（自选/持仓两个 tab，复用 Tabs/Num/Btn 基元） */
export function mountWatchlistPage(container: HTMLElement): { root: Root } {
  return mountWithShell(container, <WatchlistPage />)
}

/**
 * React `act` 的**本 bundle 实例**再导出。
 * ⚠️ 必须从本 bundle 出，不能在 runner 里 `require('react')` —— 那样会拿到**另一份** React
 *   副本，与组件用的 reconciler 不是同一个实例，act 队列对不上（会静默失效）。
 */
export { act }
