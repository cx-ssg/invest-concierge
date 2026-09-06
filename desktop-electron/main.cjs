// -*- coding: utf-8 -*-
// SHELL_UPGRADE ③ Electron 并行壳主进程（desktop-electron/main.cjs）
//
// 架构：spawn 主 exe --print-port --server（无头后端，不起 pywebview GUI）
//   → stdout 逐行正则 ^PORT=(\d+)$ 解析端口
//   → BrowserWindow(frameless:true) loadURL http://127.0.0.1:{port}/?shell=frameless
//   → 前端 TitleBar 三按钮走 preload 暴露的 desktopAPI（IPC）
//   → 关窗=隐藏到托盘（Tray）；托盘「退出」=杀 exe 进程树（映像名收集 pid）→ app.quit()
//
// 坑位对策：
//   - 端口解析只认 ^PORT=(\d+)$（exe 启动横幅/INFO 行不误匹配）
//   - ELECTRON_MIRROR 走 electron-builder.yml electronDownload.mirror
//   - exe 路径：dev 用 ../dist_m4/，打包后用 process.resourcesPath（extraResource）

const { app, BrowserWindow, Tray, Menu, ipcMain, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const fs = require('fs')
const http = require('http')
const { execFile } = require('child_process')

const EXE_BASE_NAME = 'invest-concierge'
const DEV_ROOT = path.join(__dirname, '..')

// ---------- exe 定位（dev = dist_m4；打包 = resourcesPath）----------
function resolveMainExe() {
  const packaged = path.join(process.resourcesPath, 'invest-concierge', 'invest-concierge.exe')
  if (fs.existsSync(packaged)) return { exe: packaged, packaged: true }
  const dev = path.join(DEV_ROOT, 'dist_m4', 'invest-concierge.exe')
  if (fs.existsSync(dev)) return { exe: dev, packaged: false }
  return null
}

// ---------- 后端进程管理 ----------
let backendProc = null // { child, port, pids }
let mainWindow = null
let tray = null
let quitting = false

// Windows onefile exe：bootloader 会派生多个同映像子进程，退出必须按 pid 树全杀。
// 注意不能用 taskkill /IM（按映像名）——portable 版里 Electron 壳主进程被
// electron-builder 命名为同名 invest-concierge.exe，/IM 会把壳自己也杀掉（自杀）。
function killBackendTree() {
  if (!backendProc) return
  const { child } = backendProc
  backendProc = null
  try {
    if (process.platform === 'win32' && child && child.pid) {
      // /T 杀 child 起的整棵树（onefile bootloader→winforms→WebView2 全在树内）
      execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], () => {})
    } else if (child && !child.killed) {
      child.kill('SIGTERM')
    }
  } catch (e) {
    console.error('[electron] killBackendTree error:', e && e.message)
  }
}

// stdout 逐行解析 ^PORT=(\d+)$ —— 横幅/INFO 噪音行不会误匹配
function startBackend(exe) {
  return new Promise((resolve, reject) => {
    const child = spawn(exe, ['--print-port', '--server'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true, // 无头模式：藏住 exe 的 console 窗
    })
    let settled = false
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true
        reject(new Error('后端启动超时（90s 未等到 PORT= 行）'))
      }
    }, 90 * 1000)

    const onLine = (buf) => {
      const text = buf.toString('utf-8')
      for (const raw of text.split(/\r?\n/)) {
        const m = raw.match(/^PORT=(\d+)$/)
        if (m) {
          if (!settled) {
            settled = true
            clearTimeout(timer)
            resolve({ child, port: Number(m[1]) })
          }
          return
        }
        if (/^\[错误\]/.test(raw) || /Traceback/.test(raw)) {
          if (!settled) {
            settled = true
            clearTimeout(timer)
            reject(new Error(`后端启动失败：${raw.slice(0, 200)}`))
          }
        }
      }
    }
    child.stdout.on('data', onLine)
    child.stderr.on('data', onLine)
    child.on('exit', (code) => {
      if (!settled) {
        settled = true
        clearTimeout(timer)
        reject(new Error(`后端进程提前退出 code=${code}`))
      }
    })
  })
}

// 健康等待（health 200 才 loadURL，避免白屏）
function waitHealth(port, timeoutMs = 30 * 1000) {
  const deadline = Date.now() + timeoutMs
  return new Promise((resolve) => {
    const poll = () => {
      const req = http.get(`http://127.0.0.1:${port}/api/health`, (res) => {
        res.resume()
        resolve(res.statusCode === 200)
      })
      req.on('error', () => {
        if (Date.now() > deadline) resolve(false)
        else setTimeout(poll, 500)
      })
    }
    poll()
  })
}

// ---------- 窗口 ----------
function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    frame: false, // Electron frameless（自绘标题栏在 React TitleBar）
    backgroundColor: '#1A1B1E',
    icon: path.join(__dirname, 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false, // preload 需 require('electron') 白名单 IPC
    },
    show: false,
  })
  mainWindow.once('ready-to-show', () => mainWindow.show())
  // 关窗=隐藏到托盘（与 pywebview 壳语义一致）
  mainWindow.on('close', (e) => {
    if (!quitting) {
      e.preventDefault()
      mainWindow.hide()
    }
  })
  mainWindow.loadURL(`http://127.0.0.1:${port}/?shell=frameless`)
}

// ---------- 托盘 ----------
function createTray(port) {
  const iconPath = fs.existsSync(path.join(__dirname, 'icon.ico'))
    ? path.join(__dirname, 'icon.ico')
    : path.join(process.resourcesPath, 'invest-concierge', 'icon.ico')
  tray = new Tray(iconPath)
  tray.setToolTip('invest-concierge · A股投研工作台')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: '显示主界面', click: () => (mainWindow ? (mainWindow.show(), mainWindow.focus()) : createWindow(port)) },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitting = true
          killBackendTree()
          app.quit()
        },
      },
    ])
  )
  tray.on('double-click', () => mainWindow && (mainWindow.show(), mainWindow.focus()))
}

// ---------- IPC（preload 白名单转发）----------
ipcMain.handle('win:minimize', () => mainWindow && mainWindow.minimize())
ipcMain.handle('win:maximize', () => {
  if (!mainWindow) return false
  if (mainWindow.isMaximized()) {
    mainWindow.unmaximize()
    return false // 还原
  }
  mainWindow.maximize()
  return true // 已最大化
})
ipcMain.handle('win:restore', () => mainWindow && mainWindow.restore())
ipcMain.handle('win:close', () => mainWindow && mainWindow.close()) // 走 close 事件=隐藏到托盘

// 最大化态同步（React 图标 Square↔Copy 切换需要事件通知）
ipcMain.handle('win:isMaximized', () => !!mainWindow && mainWindow.isMaximized())

// ---------- 生命周期 ----------
const singleLock = app.requestSingleInstanceLock()
if (!singleLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (mainWindow) (mainWindow.show(), mainWindow.focus())
  })

  app.whenReady().then(async () => {
    const resolved = resolveMainExe()
    if (!resolved) {
      dialog.showErrorBox(
        '启动失败',
        '未找到主程序 invest-concierge.exe。\n开发模式：先构建 dist_m4/invest-concierge.exe（见 docs/PACKAGING.md）。\n安装包：产物不完整，请重新安装。'
      )
      app.quit()
      return
    }
    try {
      backendProc = await startBackend(resolved.exe)
      console.log(`[electron] 后端就绪 port=${backendProc.port}（${resolved.packaged ? '打包' : 'dev'} 模式）`)
      const healthy = await waitHealth(backendProc.port)
      if (!healthy) {
        dialog.showErrorBox('启动失败', `后端健康检查超时：http://127.0.0.1:${backendProc.port}/api/health`)
        killBackendTree()
        app.quit()
        return
      }
      createWindow(backendProc.port)
      createTray(backendProc.port)
    } catch (err) {
      dialog.showErrorBox('启动失败', String(err && err.message ? err.message : err))
      killBackendTree()
      app.quit()
    }
  })

  // 所有出口统一清后端（托盘退出/系统关机/意外）
  const cleanup = () => {
    quitting = true
    killBackendTree()
  }
  app.on('before-quit', cleanup)
  app.on('will-quit', cleanup)
  app.on('window-all-closed', () => {
    // 保留托盘驻留语义：窗口全关不退出（与 pywebview 壳一致）
  })
}
