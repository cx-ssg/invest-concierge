import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Server, ShieldCheck, ToggleRight } from 'lucide-react'
import { api } from '../lib/api'
import { Btn, Card, Kicker, Spinner } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'

/** 系统设置（M2）：/api/settings + /api/agent/config 只读展示 + 演示模式开关 */
export function SettingsPage() {
  const qc = useQueryClient()
  const [demoOn, setDemoOn] = useState(false)

  const { data: settings, isFetching } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get })
  const { data: config } = useQuery({ queryKey: ['agent-config'], queryFn: api.agent.config })

  const demoMut = useMutation({
    mutationFn: (enabled: boolean) => api.settings.setDemo(enabled),
    onSuccess: (r) => {
      setDemoOn(!!r.demo_mode)
      void qc.invalidateQueries({ queryKey: ['settings'] })
      void qc.invalidateQueries({ queryKey: ['agent-config'] })
    },
  })

  // v1.1 隐私开关（记忆显性化 C-4）：默认开，持久化在本地库
  const [holdingsSwitch, setHoldingsSwitch] = useState<boolean | null>(null)
  const holdingsMut = useMutation({
    mutationFn: (enabled: boolean) => api.settings.setAiReadHoldings(enabled),
    onSuccess: (r) => {
      setHoldingsSwitch(r.ai_read_holdings ?? false)
      void qc.invalidateQueries({ queryKey: ['settings'] })
    },
  })
  const currentHoldings = holdingsSwitch ?? settings?.ai_read_holdings ?? true

  const keyOk = settings?.api_key_configured ?? config?.api_key_configured ?? false
  // 演示模式状态：后端 /api/settings 不返回当前值（进程级开关），以本地切换为准
  const currentDemo = demoOn

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-4">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="系统设置"
        desc="数据引擎配置与服务状态"
      />

      {isFetching && !settings ? (
        <Card className="flex items-center gap-2 p-4 text-[13px] text-ink-2">
          <Spinner size={14} /> 加载设置…
        </Card>
      ) : null}

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <KeyRound size={14} className="text-ink-2" /> API Key
        </div>
        <div className="mt-2 flex items-center gap-2">
          <span
            className={`inline-block size-2 rounded-full ${keyOk ? '' : 'opacity-40'}`}
            style={{ background: keyOk ? 'var(--accent)' : 'var(--text-3)' }}
          />
          <span className="text-[13px] text-ink-2">{keyOk ? '已配置（真实调用可用）' : '未配置（演示降级模式）'}</span>
        </div>
        {!keyOk ? (
          <div className="mt-2 rounded-tile border border-hairline bg-bg px-3 py-2 text-[12px] leading-relaxed text-ink-3">
            配置方式：
            <br />1. 在项目目录创建 <span className="mono">local_env.bat</span>，写入{' '}
            <span className="mono">set DEEPSEEK_API_KEY=你的key</span> 后运行
            <br />2. 或设置系统环境变量 <span className="mono">DEEPSEEK_API_KEY</span> 后重启后端
          </div>
        ) : null}
      </Card>

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <Server size={14} className="text-ink-2" /> 模型与后端
        </div>
        <div className="mt-2 grid grid-cols-1 gap-2 md:grid-cols-3">
          <div className="rounded-tile border border-hairline bg-bg px-3 py-2">
            <Kicker>对话模型</Kicker>
            <div className="mono mt-1 text-[13px] text-ink-2">{config?.chat_model ?? '--'}</div>
          </div>
          <div className="rounded-tile border border-hairline bg-bg px-3 py-2">
            <Kicker>推理模型</Kicker>
            <div className="mono mt-1 text-[13px] text-ink-2">{config?.reasoner_model ?? '--'}</div>
          </div>
          <div className="rounded-tile border border-hairline bg-bg px-3 py-2">
            <Kicker>服务版本</Kicker>
            <div className="mono mt-1 text-[13px] text-ink-2">v{settings?.version ?? '--'}</div>
          </div>
        </div>
      </Card>

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <ShieldCheck size={14} className="text-ink-2" /> 隐私
        </div>
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={() => void holdingsMut.mutate(!currentHoldings)}
            className={`relative h-5 w-9 cursor-pointer rounded-full border transition-colors ${
              currentHoldings ? 'border-hairline-strong' : 'border-hairline'
            }`}
            style={{ background: currentHoldings ? 'var(--accent-soft)' : 'var(--surface-2)' }}
            aria-pressed={currentHoldings}
            aria-label="允许 AI 读取我的持仓"
          >
            <span
              className="absolute top-0.5 size-3.5 rounded-full transition-all"
              style={{
                left: currentHoldings ? 19 : 3,
                background: currentHoldings ? 'var(--accent)' : 'var(--text-3)',
                transitionDuration: 'var(--dur)',
              }}
            />
          </button>
          <span className="text-[12.5px] text-ink-2">
            允许 AI 读取我的持仓{currentHoldings ? '（回答可结合持仓个性化）' : '（已关闭）'}
          </span>
          {holdingsMut.isPending ? <Spinner size={12} /> : null}
        </div>
        <p className="mt-1.5 text-[11.5px] leading-relaxed text-ink-3">
          开启后，对话时会把持仓快照（名称/代码/金额/指标）随提问一并发送给 DeepSeek 用于个性化回答；
          关闭后 AI 不会读取持仓，也不会暗示知道你的持仓。数据仅在本机存储，关断即时生效。
        </p>
      </Card>

      <Card className="p-4">
        <div className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
          <ToggleRight size={14} className="text-ink-2" /> 演示模式
        </div>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-3">
          进程级开关：开启后 AI 使用内置示例数据走完整流程（工具时间线可用），适合无 Key 环境体验。
        </p>
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={() => {
              const next = !currentDemo
              setDemoOn(next)
              void demoMut.mutate(next)
            }}
            className={`relative h-5 w-9 cursor-pointer rounded-full border transition-colors ${
              currentDemo ? 'border-hairline-strong' : 'border-hairline'
            }`}
            style={{ background: currentDemo ? 'var(--accent-soft)' : 'var(--surface-2)' }}
            aria-pressed={currentDemo}
          >
            <span
              className="absolute top-0.5 size-3.5 rounded-full transition-all"
              style={{
                left: currentDemo ? 19 : 3,
                background: currentDemo ? 'var(--accent)' : 'var(--text-3)',
                transitionDuration: 'var(--dur)',
              }}
            />
          </button>
          <span className="text-[12.5px] text-ink-2">{currentDemo ? '演示模式已开启' : '演示模式关闭'}</span>
          {demoMut.isPending ? <Spinner size={12} /> : null}
          <div className="flex-1" />
          <Btn
            onClick={() => {
              void qc.invalidateQueries({ queryKey: ['settings'] })
              void qc.invalidateQueries({ queryKey: ['agent-config'] })
              void qc.invalidateQueries({ queryKey: ['status'] })
            }}
          >
            刷新
          </Btn>
        </div>
      </Card>
    </section>
  )
}