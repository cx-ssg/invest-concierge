import type { Track } from '../stores/ui'

/**
 * live 页路由路径（fund 轨/stock 轨分开命名空间，common 直挂根）。
 * 与 App.tsx 的 <Routes> 骨架一一对应。
 */
export function pagePath(track: Track, key: string): string {
  if (key === 'ai_chat') return '/'
  if (key === 'settings') return '/settings'
  return `/${track}/${key}`
}