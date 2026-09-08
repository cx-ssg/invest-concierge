import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, PlugZap, Server, ShieldCheck, ToggleRight } from 'lucide-react'
import { api } from '../lib/api'
import { Btn, Card, Kicker, Spinner } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'

/** 系统设置（M2）：/api/settings + /api/agent/config 只读展示 + 演示模式开关 */
export function SettingsPage() {
  const qc = useQueryClient()
  const [demoSwitch, setDemoSwitch] = useState<boolean | null>(null)

  const { data: settings, isFetching } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get })
  const { data: config } = useQuery({ queryKey: ['agent-config'], queryFn: api.agent.config })

  const demoMut = useMutation({
    mutationFn: (enabled: boolean) => api.settings.setDemo(enabled),
    onSuccess: (r) => {
      setDemoSwitch(!!r.demo_mode)
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

  // 演示模式状态：后端 GET /api/settings 现返回真实 demo_mode（v1.1 修复"开了不显示"），
  // 初始化与服务端为准；点击后乐观切换 + mutation 结果校正
  const currentDemo = demoSwitch ?? settings?.demo_mode ?? false

  // ==================== v1.2 模型接入（多 provider） ====================
  const { data: llm, isFetching: llmFetching } = useQuery({ queryKey: ['llm-view'], queryFn: api.settings.getLlm })
  const [llmProvider, setLlmProvider] = useState<string>('')
  const [llmKey, setLlmKey] = useState('')
  const [llmBaseUrl, setLlmBaseUrl] = useState('')
  const [llmModel, setLlmModel] = useState('')
  const [llmTestMsg, setLlmTestMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [llmSaveMsg, setLlmSaveMsg] = useState<string | null>(null)

  // 首次加载后用服务端值初始化本地表单（之后用户输入为准）
  const [llmInited, setLlmInited] = useState(false)
  if (llm && !llmInited) {
    setLlmProvider(llm.provider)
    setLlmBaseUrl(llm.provider === 'custom' ? llm.custom_base_url || '' : llm.base_url || '')
    setLlmModel(llm.source === 'settings' ? llm.model : '')
    setLlmInited(true)
  }

  const llmMeta = llm?.providers[llmProvider || llm?.provider || 'deepseek']
  const isCustom = (llmProvider || llm?.provider || '') === 'custom'
  const modelOptions = llmMeta?.models ?? []

  const llmTestMut = useMutation({
    mutationFn: () =>
      api.settings.testLlm({
        provider: llmProvider || llm!.provider,
        api_key: llmKey,
        base_url: isCustom ? llmBaseUrl : '',
        model: llmModel,
      }),
    onSuccess: (r) => {
      setLlmTestMsg(
        r.ok
          ? { ok: true, text: `连接成功 · ${r.latency_ms}ms · ${r.model}${r.reply ? ` · 回复「${r.reply}」` : ''}` }
          : { ok: false, text: r.error || '连接失败' },
      )
    },
    onError: (e) => setLlmTestMsg({ ok: false, text: String(e).slice(0, 120) }),
  })

  const llmSaveMut = useMutation({
    mutationFn: () =>
      api.settings.saveLlm({
        provider: llmProvider || llm!.provider,
        api_key: llmKey,
        base_url: isCustom ? llmBaseUrl : '',
        model: llmModel,
      }),
    onSuccess: (r) => {
      if (r.ok) {
        setLlmKey('') // 保存成功后清输入框（服务端只存不回显明文）
        setLlmSaveMsg(`已保存：${r.provider_label} · ${r.api_key_masked || '未配置 Key'}`)
        setLlmTestMsg(null)
        void qc.invalidateQueries({ queryKey: ['llm-view'] })
        void qc.invalidateQueries({ queryKey: ['settings'] })
        void qc.invalidateQueries({ queryKey: ['agent-config'] })
        void qc.invalidateQueries({ queryKey: ['status'] })
      } else {
        setLlmSaveMsg(r.error ? `保存失败：${r.error}` : '保存失败')
      }
    },
    onError: (e) => setLlmSaveMsg(String(e).slice(0, 120)),
  })

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
          <PlugZap size={14} className="text-ink-2" /> 模型接入
        </div>
        {llm && (
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px] text-ink-2">
            <span
              className={`inline-block size-2 rounded-full ${llm.api_key_configured ? '' : 'opacity-40'}`}
              style={{ background: llm.api_key_configured ? 'var(--accent)' : 'var(--text-3)' }}
            />
            <span>
              {llm.provider_label} · {llm.api_key_masked || '未配置 Key'}
              {llm.source === 'env' ? '（来自 .env / 环境变量）' : llm.source === 'settings' ? '' : '（演示降级模式）'}
            </span>
          </div>
        )}

        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          <label className="block">
            <Kicker>接入方 Provider</Kicker>
            <select
              value={llmProvider || llm?.provider || 'deepseek'}
              onChange={(e) => {
                setLlmProvider(e.target.value)
                setLlmSaveMsg(null)
                setLlmTestMsg(null)
                const meta = llm?.providers[e.target.value]
                setLlmBaseUrl(e.target.value === 'custom' ? llm?.custom_base_url || '' : '')
                setLlmModel(meta?.default_model || '')
              }}
              className="mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[13px] text-ink outline-none focus:border-hairline-strong"
            >
              {Object.entries(llm?.providers ?? {}).map(([pid, p]) => (
                <option key={pid} value={pid}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <Kicker>API Key{llmMeta ? `（${llmMeta.key_hint}）` : ''}</Kicker>
            <input
              type="password"
              value={llmKey}
              onChange={(e) => setLlmKey(e.target.value)}
              placeholder={llm?.api_key_masked ? `已配置 ${llm.api_key_masked}，留空沿用` : '粘贴 API Key'}
              autoComplete="off"
              className="mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
            />
          </label>
          {isCustom ? (
            <label className="block md:col-span-2">
              <Kicker>Base URL（OpenAI 兼容端点）</Kicker>
              <input
                value={llmBaseUrl}
                onChange={(e) => setLlmBaseUrl(e.target.value)}
                placeholder="https://your-gateway.example.com/v1"
                className="mono mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
              />
            </label>
          ) : null}
          <label className="block">
            <Kicker>对话模型（可选，留空用默认）</Kicker>
            <input
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
              placeholder={llmMeta?.default_model || '如 deepseek-chat'}
              list="llm-model-options"
              className="mono mt-1 w-full rounded-tile border border-hairline bg-bg px-3 py-2 text-[12.5px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
            />
            <datalist id="llm-model-options">
              {modelOptions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Btn onClick={() => void llmTestMut.mutate()} disabled={llmTestMut.isPending}>
            {llmTestMut.isPending ? <Spinner size={12} /> : <PlugZap size={13} />} 测试连接
          </Btn>
          <Btn onClick={() => void llmSaveMut.mutate()} disabled={llmSaveMut.isPending}>
            {llmSaveMut.isPending ? <Spinner size={12} /> : <KeyRound size={13} />} 保存
          </Btn>
          {llmFetching ? <Spinner size={12} /> : null}
        </div>
        {llmTestMsg ? (
          <div
            className={`mt-2 rounded-tile border px-3 py-2 text-[12px] leading-relaxed ${
              llmTestMsg.ok ? 'border-hairline bg-bg text-ink-2' : 'border-hairline-strong bg-bg text-ink-2'
            }`}
          >
            {llmTestMsg.ok ? '✅ ' : '❌ '}
            {llmTestMsg.text}
          </div>
        ) : null}
        {llmSaveMsg ? (
          <div className="mt-2 rounded-tile border border-hairline bg-bg px-3 py-2 text-[12px] text-ink-2">
            💾 {llmSaveMsg}
          </div>
        ) : null}
        <p className="mt-2 text-[11.5px] leading-relaxed text-ink-3">
          Key 仅保存在本机数据库（不上传/不入 git/不回显明文）。保存后立即生效，无需重启；
          .env 方式继续有效（设置页配置优先）。自定义接入支持任何 OpenAI 兼容端点（中转/网关/本地部署）。
        </p>
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
              setDemoSwitch(next)
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