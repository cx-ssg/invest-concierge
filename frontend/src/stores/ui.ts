import { create } from 'zustand'

export type Theme = 'dark' | 'light'
export type Track = 'fund' | 'stock'

interface UiState {
  theme: Theme
  track: Track
  setTheme: (t: Theme) => void
  toggleTheme: () => void
  setTrack: (t: Track) => void
}

/** UI 全局态：主题（黑白浅/暗）+ 当前轨道（基金/股票） */
export const useUiStore = create<UiState>((set) => ({
  theme: 'dark',
  track: 'fund',
  setTheme: (theme) => {
    document.documentElement.dataset.theme = theme
    set({ theme })
  },
  toggleTheme: () =>
    set((s) => {
      const theme: Theme = s.theme === 'dark' ? 'light' : 'dark'
      document.documentElement.dataset.theme = theme
      return { theme }
    }),
  setTrack: (track) => set({ track }),
}))

/** 启动时挂默认主题（main.tsx 调用一次） */
export function initTheme(theme: Theme = 'dark') {
  document.documentElement.dataset.theme = theme
}
