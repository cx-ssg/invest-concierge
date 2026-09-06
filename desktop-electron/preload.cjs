// SHELL_UPGRADE ③ preload（desktop-electron/preload.cjs）
// contextIsolation 白名单桥：只暴露窗口控制四方法 + 最大化态查询，
// 前端经 window.desktopAPI 调用（TitleBar Electron 分支）。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktopAPI', {
  minimize: () => ipcRenderer.invoke('win:minimize'),
  // 返回值：true=已最大化 false=已还原（React 图标切换用）
  maximize: () => ipcRenderer.invoke('win:maximize'),
  restore: () => ipcRenderer.invoke('win:restore'),
  // 走主进程 close 事件 = 隐藏到托盘（语义与 pywebview 壳 destroy→closing 链一致）
  close: () => ipcRenderer.invoke('win:close'),
  isMaximized: () => ipcRenderer.invoke('win:isMaximized'),
})
