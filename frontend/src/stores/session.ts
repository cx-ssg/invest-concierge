import { create } from 'zustand'

/**
 * 会话选择全局态：主侧栏的「历史会话」列表与聊天页共用一个 activeId
 * （列表在 Sidebar，聊天区在 AiChatPage——跨组件必须走 store，不能 useState）
 */
interface SessionState {
  activeId: number | null
  setActiveId: (id: number | null) => void
}

export const useSessionStore = create<SessionState>((set) => ({
  activeId: null,
  setActiveId: (activeId) => set({ activeId }),
}))
