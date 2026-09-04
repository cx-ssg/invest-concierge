import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Search, X } from 'lucide-react'
import { api } from '../lib/api'
import type { DiagnosisPayload } from '../types/api'
import { fmtNum } from '../lib/format'
import { Btn, Card, Spinner, Tabs } from '../components/ui/primitives'
import { PageHeader } from '../components/layout/PageHeader'
import { FundamentalTab, MinefieldTab, MoatTab, ValuationTab, ReportsTab } from '../features/diagnosis/DataTabs'
import { AiTab } from '../features/diagnosis/AiTab'
import { MetricGrid } from '../features/diagnosis/ui'

const EXAMPLES = [
  { label: '600519 贵州茅台', code: '600519' },
  { label: '300750 宁德时代', code: '300750' },
]

const TAB_LIST = [
  { key: 'fundamental', label: '基本面' },
  { key: 'minefield', label: '排雷' },
  { key: 'moat', label: '护城河' },
  { key: 'valuation', label: '估值' },
  { key: 'reports', label: '财报三表' },
  { key: 'ai', label: 'AI 辩论' },
]

/** 综合诊断（M2）：输入代码 → /api/stocks/{code}/diagnosis → 6 tab + AI 辩论/追问 */
export function StockDiagnosisPage() {
  const qc = useQueryClient()
  const [code, setCode] = useState('')
  const [submitted, setSubmitted] = useState<string | null>(null)
  const [inputError, setInputError] = useState<string | null>(null)
  const [tab, setTab] = useState('fundamental')

  const { data, isFetching, isError, error, refetch } = useQuery({
    queryKey: ['diagnosis', submitted],
    queryFn: ({ signal }) => api.diagnosis(submitted!, signal),
    enabled: submitted != null,
    retry: false,
    staleTime: 24 * 60 * 60_000, // 与后端 24h 缓存对齐
  })

  const { data: config } = useQuery({ queryKey: ['agent-config'], queryFn: api.agent.config })

  function startDiag(c?: string) {
    const target = (c ?? code).trim()
    if (!/^\d{6}$/.test(target)) {
      setInputError('请输入 6 位数字股票代码（如 600519）')
      return
    }
    setInputError(null)
    setCode(target)
    setSubmitted(target)
    setTab('fundamental')
  }

  function cancelDiag() {
    void qc.cancelQueries({ queryKey: ['diagnosis', submitted] })
    void qc.removeQueries({ queryKey: ['diagnosis', submitted] })
    setSubmitted(null)
  }

  const d: DiagnosisPayload | undefined = data
  const name = d?.stock_info?.name ? String(d.stock_info.name) : ''

  return (
    <section className="flex min-w-0 flex-1 flex-col gap-4">
      <PageHeader
        eyebrow="INVEST CONCIERGE"
        title="综合诊断"
        desc="输入股票代码，一键体检基本面 / 排雷 / 护城河 / 估值 / 财报三表，可选 AI 辩论与追问"
      />

      {/* 输入区 */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={code}
          onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          onKeyDown={(e) => {
            if (e.key === 'Enter') startDiag()
          }}
          placeholder="6 位股票代码，如 600519"
          inputMode="numeric"
          className="w-44 rounded-tile border border-hairline bg-surface px-2.5 py-1.5 text-[13px] text-ink outline-none placeholder:text-ink-3 focus:border-hairline-strong"
        />
        {isFetching ? (
          <Btn variant="danger" onClick={cancelDiag}>
            <X size={13} /> 取消
          </Btn>
        ) : (
          <Btn variant="primary" onClick={() => startDiag()}>
            <Search size={13} /> 开始诊断
          </Btn>
        )}
        {EXAMPLES.map((ex) => (
          <Btn key={ex.code} onClick={() => startDiag(ex.code)} disabled={isFetching}>
            {ex.label}
          </Btn>
        ))}
      </div>
      {inputError ? <p className="px-1 text-[12px] text-fall">{inputError}</p> : null}
      <p className="px-1 text-[11px] text-ink-3">
        数据源：AkShare（东方财富 / 同花顺 / 乐咕 / 百度股市通），财务数据 24h 缓存；仅供参考，非投资建议。
      </p>

      {/* 结果区 */}
      {!submitted ? (
        <Card className="p-6 text-center">
          <div className="text-[13px] text-ink-2">输入代码后开始一键诊断</div>
          <div className="mt-1 text-[12px] text-ink-3">
            首次拉取 5+ 数据源约 15-40 秒（已缓存秒回）；个别引擎失败自动降级，页面不崩。
          </div>
        </Card>
      ) : null}

      {submitted && isFetching ? (
        <Card className="flex flex-col items-center gap-2 p-8">
          <Spinner size={18} />
          <div className="text-[13px] text-ink-2">正在拉取 {submitted} 的 5 大引擎数据（首次约 15-40 秒，已缓存秒回）…</div>
          <div className="text-[11px] text-ink-3">东财等数据源被墙时自动降级并展示「数据不可得」文案</div>
        </Card>
      ) : null}

      {submitted && isError ? (
        <Card className="p-4">
          <div className="text-[13px] font-medium text-fall">诊断获取失败（{String(error)}）</div>
          <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">
            可能是后端未启动（FastAPI 8000）、数据源全网不可达或超过 90s 超时。已降级展示，页面不会崩溃。
          </p>
          <div className="mt-2">
            <Btn variant="primary" onClick={() => void refetch()}>
              <RefreshCw size={13} /> 重试
            </Btn>
          </div>
        </Card>
      ) : null}

      {submitted && d && !isFetching ? (
        <>
          {/* 页顶总览（4 指标） */}
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-baseline gap-2 px-1">
              <span className="text-[15px] font-semibold text-ink">
                {name ? `${name}（${d.code}）` : d.code}
              </span>
              {d.stock_info?.price ? (
                <span className="num text-[13px] text-ink-2">现价 ¥{fmtNum(d.stock_info.price)}</span>
              ) : null}
              {!d.stock_info?.price ? (
                <span className="text-[12px] text-ink-3">该股票可能停牌或无行情数据，以下为财务口径数据（如有）</span>
              ) : null}
            </div>
            <MetricGrid
              items={[
                {
                  label: '基本面评分',
                  value: d.fundamental_score?.total_score != null ? `${d.fundamental_score.total_score}/100` : '--',
                },
                { label: '排雷评级', value: d.risk_rating?.level ?? '--' },
                { label: '护城河等级', value: d.moat_scores?.level ?? '--' },
                { label: '估值状态', value: d.valuation?.valuation_status ?? '--' },
              ]}
            />
            {d.errors?.length ? (
              <div className="rounded-tile border border-hairline bg-bg px-3 py-2">
                <div className="text-[11px] text-ink-3">
                  部分引擎获取失败（{d.errors.length} 项，已自动降级展示）
                </div>
                <ul className="mt-1 flex flex-col gap-0.5 text-[11.5px] text-ink-3">
                  {d.errors.map((e, i) => (
                    <li key={i}>- {e}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>

          {/* 6 tab */}
          <div className="flex flex-col gap-3">
            <Tabs tabs={TAB_LIST} active={tab} onChange={setTab} />
            {tab === 'fundamental' ? <FundamentalTab d={d} /> : null}
            {tab === 'minefield' ? <MinefieldTab d={d} /> : null}
            {tab === 'moat' ? <MoatTab d={d} /> : null}
            {tab === 'valuation' ? <ValuationTab d={d} /> : null}
            {tab === 'reports' ? <ReportsTab d={d} /> : null}
            {tab === 'ai' ? <AiTab d={d} name={name} apiKeyConfigured={config?.api_key_configured ?? false} /> : null}
          </div>
        </>
      ) : null}
    </section>
  )
}