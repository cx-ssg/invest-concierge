import { Navigate, Route, Routes } from 'react-router-dom'
import { TitleBar } from './app/layout/TitleBar'
import { Sidebar } from './app/layout/Sidebar'
import { StatusBar } from './app/layout/StatusBar'
import { AiChatPage } from './pages/AiChatPage'
import { DashboardPage } from './pages/DashboardPage'
import { DiaryPage } from './pages/DiaryPage'
import { PortfolioPage } from './pages/PortfolioPage'
import { SettingsPage } from './pages/SettingsPage'
import { StockDiagnosisPage } from './pages/StockDiagnosisPage'

/**
 * 三区壳：行 = [TitleBar 44px | 内容 1fr | StatusBar 28px]；
 * 列 = [Sidebar 200px | 主区 1fr]。主内容 max-w 1120px 居中（UI_POLISH_PLAN §5）。
 * 路由骨架 = live 6 页（dashboard/portfolio/diary / stock_diagnosis / ai_chat/settings）。
 */
export default function App() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg text-ink">
      <TitleBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        {/* 聊天页需要高度链贯通（min-h-0 + flex-1）让输入框贴底；其他页内容自然滚动 */}
        <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <div className="mx-auto flex min-h-0 w-full min-w-0 max-w-[1120px] flex-1 flex-col px-6 py-4">
            <Routes>
              <Route path="/" element={<AiChatPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/fund/dashboard" element={<DashboardPage />} />
              <Route path="/fund/portfolio" element={<PortfolioPage />} />
              <Route path="/fund/diary" element={<DiaryPage />} />
              <Route path="/stock/stock_diagnosis" element={<StockDiagnosisPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
      <StatusBar />
    </div>
  )
}