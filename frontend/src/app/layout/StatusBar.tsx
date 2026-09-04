import { StatusDot } from '../../components/ui/primitives'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'

/** 状态栏（26px 单行等宽小字）：引擎点 | 数据源 | 上次刷新 | 版本 —— /api/status 每 30s 驱动 */
export function StatusBar() {
  const { data: status } = useQuery({
    queryKey: ['status'],
    queryFn: api.status,
    refetchInterval: 30_000,
    retry: 1,
  })

  const engineState = status?.engine.api_key_configured ? 'ready' : 'off'
  const engineText = status?.engine.api_key_configured ? '引擎就绪' : '引擎未配置 Key'
  const dataSource = status?.data_source.ok ? 'AkShare 在线' : '数据源不可用'
  const refresh = status?.last_refresh ? status.last_refresh.replace('T', ' ') : '--:--:--'
  const version = status?.version ? `v${status.version}` : ''

  return (
    <footer
      className="hairline-t mono flex h-[26px] shrink-0 items-center gap-4 px-3 text-[11px] text-ink-2 select-none"
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
        刷新 <span className="num">{refresh}</span>
      </span>
      <div className="flex-1" />
      {status?.engine.reasoner_model ? (
        <span className="text-ink-3">{status.engine.reasoner_model}</span>
      ) : null}
      <span className="text-ink-3">{version}</span>
    </footer>
  )
}
