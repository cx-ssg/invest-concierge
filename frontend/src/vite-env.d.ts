/// <reference types="vite/client" />

/**
 * pywebview 桌面壳注入的全局桥（desktop/launcher.py --frameless 时随窗口注入；
 * 浏览器模式不存在，全部字段可选）。窗口控制方法由 launcher 的 make_window_api
 * 通过 create_window(js_api=...) 暴露——方法名即 js_api 类的公开方法名。
 */
declare global {
  interface Window {
    pywebview?: {
      api?: {
        minimize?: () => void
        maximize?: () => void
        restore?: () => void
        destroy?: () => void
      }
    }
  }
}

export {}
