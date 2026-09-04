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
 * 三区壳（M1）：行 = [TitleBar 38px | 内容 1fr | StatusBar 26px]；
 * 列 = [Sidebar 200px | 主区 1fr]。路由骨架 = live 6 页
 * （dashboard/portfolio/diary / stock_diagnosis / ai_chat/settings）。
 */
export default function App() {
  return (
    <div className="flex h-screen flex-col overflow-hidden bg-bg text-ink">
      <TitleBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex min-h-full max-w-[1100px] flex-col p-4">
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