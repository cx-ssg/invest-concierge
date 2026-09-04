import { StatusDot } from '../../components/ui/primitives'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

/** 状态栏（28px 单行小字）：引擎 | 数据源 | 更新时间 | 版本 —— /api/status 每 30s 驱动 */
export function StatusBar() {
  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    refetchInterval: 30_000,
    retry: 1,
  })

  const engineState = status?.engine.api_key_configured ? 'ready' : 'off'
  const engineText = status?.engine.api_key_configured ? '分析引擎 已连接' : '分析引擎 未配置'
  const dataSource = status?.data_source.ok ? '数据源 正常' : '数据源 异常'
  const refresh = status?.last_refresh ? status.last_refresh.replace('T', ' ') : '--:--:--'
  const version = status?.version ? `v${status.version}` : ''

  return (
    <footer
      className="hairline-t mono flex h-[28px] shrink-0 items-center gap-4 px-3 text-[11px] text-ink-2 select-none"
      style={{ background: 'var(--bg-sidebar)' }}
    >
      <span className="flex items-center gap-1.5">
        <StatusDot state={engineState} />
        {engineText}
      </span>
      <span className="text-ink-3">|</span>
      <span>{dataSource}</span>
      <span className="text-ink-3">|</span>
      <span>
        更新 <span className="num">{refresh}</span>
      </span>
      <div className="flex-1" />
      {status?.engine.reasoner_model ? (
        <span className="text-ink-3">{status.engine.reasoner_model}</span>
      ) : null}
      <span className="text-ink-3">{version}</span>
    </footer>
  )
}
