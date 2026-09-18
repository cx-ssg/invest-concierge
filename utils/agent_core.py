# -*- coding: utf-8 -*-
"""
Agent Core（UI 无关可测） - 声明式 Tool Registry + 带规划的 Agent 循环。

- TOOL_REGISTRY：11 个工具的声明式注册表（真名核对过），**晚绑定**
  （fn 存 "模块.函数名"，调用时 import 解析 → 存量 test_ai_tools 的 mock.patch 可见）。
- execute_ai_tool_v2：注册表分派 + _truncate 截断（长列表 top-20 / 超长 8000）。
- execute_ai_tool：兼容别名（旧名 + 3 旧工具错误文案不变，"未找到基金"被测试断言）。
- agent_run：规划循环（先计划 → 逐步执行 → 工具 error 回填时明说"数据不可得"），
  max_tool_rounds=8；支持 memory 落库与 continue_question 追问链。

实现依据：docs/AGENT_MVP_DESIGN.md §2 / §3 / §5 实施注意。
"""
import importlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Optional, List, Dict, Any

from config import DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL


def _reasoner_model():
    """v1.2 多 provider：DB 配置的 reasoner 模型优先（未配置回落 DeepSeek）"""
    from services.llm_config import get_llm_config
    return get_llm_config()["reasoner_model"] or DEEPSEEK_REASONER_MODEL

# Windows 控制台 GBK 防护
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


class ToolDef:
    """工具定义：schema + 晚绑定 fn 引用（module.fn 字符串）+ 可选 none_error 模板"""

    def __init__(self, name, module, fn, description, params, required=None, none_error=None):
        # type: (str, str, str, str, Dict[str, Any], Optional[List[str]], Optional[str]) -> None
        self.name = name
        self.module = module
        self.fn = fn
        self.fn_ref = "{}.{}".format(module, fn)
        self.description = description
        self.params = params
        self.required = list(required or [])
        self.param_names = set(params.keys())
        self.none_error = none_error  # 工具返回 None 时的错误消息模板（兼容旧文案用）
        self.schema = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": self.required,
                },
            },
        }


def _stock_code_param(desc="6 位数字股票代码，如 600519"):
    """股票代码参数的常见 schema"""
    return {"stock_code": {"type": "string", "description": desc}}


# ==================== Tool Registry（11 个，真名核对过） ====================

TOOL_REGISTRY = {
    # --- 已有 3 工具（迁移 + 晚绑定；schema 与旧 AI_TOOLS 完全一致）---
    "get_fund_info": ToolDef(
        name="get_fund_info",
        module="utils.ai_helper",
        fn="get_fund_info",
        description="查询单只基金的基本信息：名称、最新净值、估算净值、今日涨跌幅。输入 6 位基金代码（如 161725）。",
        params={"fund_code": {"type": "string", "description": "6 位基金代码，如 161725"}},
        required=["fund_code"],
        none_error="未找到基金 {fund_code}",
    ),
    "get_market_index": ToolDef(
        name="get_market_index",
        module="utils.ai_helper",
        fn="get_market_index",
        description="查询当前大盘指数行情：上证指数、深证成指、创业板指、沪深300 等主要指数的点位与涨跌幅。",
        params={},
    ),
    "load_funds": ToolDef(
        name="load_funds",
        module="utils.ai_helper",
        fn="load_funds_snapshot",
        description=(
            "查询用户当前的基金持仓快照：每只基金的代码、名称、投入金额、成本净值、持有份额，"
            "并附量化指标（近1周/1月/3月/6月/1年收益率、最大回撤、年化波动率、夏普比率，"
            "前 6 只带指标，其余仅基础字段）。演示模式下返回内置示例持仓。"
            "用户问\u201c我的持仓怎么样、帮我分析下我的基金\u201d时先用它。"
        ),
        params={},
    ),
    # --- P0 诊断闭环 ---
    "get_stock_diagnosis": ToolDef(
        name="get_stock_diagnosis",
        module="data.diagnosis",
        fn="build_diagnosis_payload",
        description=(
            "查询个股综合诊断（6 引擎打包）：行情、基本面评分、财报三表核心指标、财务排雷（8 大雷区）、"
            "护城河评分、估值结论与 PE/PB 历史分位。首次调用约 15-40 秒（24h 缓存，二次秒回）。"
        ),
        params=_stock_code_param(),
        required=["stock_code"],
    ),
    "get_stock_valuation": ToolDef(
        name="get_stock_valuation",
        module="data.stock_valuation",
        fn="get_valuation_data",
        description="查询个股估值原始数据：当前价、PE、PB、ROE、EPS、BVPS、股息率、市值、行业、利润/营收增速。",
        params=_stock_code_param(),
        required=["stock_code"],
    ),
    "get_stock_minefield": ToolDef(
        name="get_stock_minefield",
        module="data.financial_minefield",
        fn="minefield_pipeline",
        description=(
            "查询个股财务排雷结论（五连链打包）：风险评级、触发的风险项、安全项、综合建议，"
            "覆盖存贷双高/商誉/现金流/应收/质押/毛利率/存货/审计意见 8 大雷区。"
        ),
        params=_stock_code_param(),
        required=["stock_code"],
    ),
    # --- P1 横向对比 + 情绪 ---
    "get_stock_moat": ToolDef(
        name="get_stock_moat",
        module="data.moat_analysis",
        fn="moat_pipeline",
        description=(
            "查询个股护城河分析结论（两步链打包）：品牌/技术/规模/网络/转换成本/资源 6 大护城河评分、"
            "总分与等级、优势、不足与建议。行业对比 20 家约 10-25 秒。"
        ),
        params=_stock_code_param(),
        required=["stock_code"],
    ),
    "compare_funds": ToolDef(
        name="compare_funds",
        module="data.fund_api",
        fn="compare_funds_structured",
        description=(
            "对比两只基金的基本情况：返回结构化对比（ok/error + funds 信息 + 指标行），"
            "指标含最新净值、估算净值、今日涨跌幅、更新时间。"
        ),
        params={
            "fund_code1": {"type": "string", "description": "第一只基金 6 位代码，如 161725"},
            "fund_code2": {"type": "string", "description": "第二只基金 6 位代码，如 110022"},
        },
        required=["fund_code1", "fund_code2"],
    ),
    "get_market_sentiment": ToolDef(
        name="get_market_sentiment",
        module="utils.market_sentiment_merged",
        fn="get_market_sentiment",
        description=(
            "查询市场情绪三指标：涨停/跌停家数、最高连板高度、市场涨跌家数（赚钱效应）。"
            "单个指标失败时对应字段为 null。"
        ),
        params={},
    ),
    # --- P2 锦上添花 ---
    "add_diary": ToolDef(
        name="add_diary",
        module="utils.diary_tool",
        fn="add_diary",
        description=(
            "写入一条投资日记/交易记录（先读后追加再整表保存）。"
            "date 缺省为今天（YYYY-MM-DD）；action 建议取值：买入/卖出/定投/观察/止盈/止损。"
            "注意：MVP 接受多写并发最后写入覆盖的边界风险。"
        ),
        params={
            "date": {"type": "string", "description": "交易日期 YYYY-MM-DD，缺省为今天"},
            "fund_code": {"type": "string", "description": "基金/股票代码"},
            "fund_name": {"type": "string", "description": "基金/股票名称"},
            "action": {"type": "string", "description": "操作类型：买入/卖出/定投/观察等"},
            "amount": {"type": "number", "description": "交易金额（元）"},
            "note": {"type": "string", "description": "备注"},
        },
        required=["fund_code", "action"],
    ),
    "get_diary": ToolDef(
        name="get_diary",
        module="data.database",
        fn="load_diary",
        description="查询投资日记列表（按日期倒序）：日期、基金代码/名称、操作、金额、备注。",
        params={},
    ),
    # --- P1 对话能力升级（2026-09-04 拍板：21 页收敛为工具；全部晚绑定现成引擎函数）---
    "search_fund": ToolDef(
        name="search_fund",
        module="data.fund_api",
        fn="search_fund",
        description="按关键词搜索基金（代码/名称/拼音缩写均可，如“白酒”、“161725”）。返回最多 50 条：代码、名称、类型。用户问“帮我找某类基金”时先用它。",
        params={"keyword": {"type": "string", "description": "搜索关键词：基金代码/名称/拼音，如 白酒、161725、bb"}},
        required=["keyword"],
    ),
    "search_stock": ToolDef(
        name="search_stock",
        module="data.stock_api",
        fn="search_stock",
        description="按关键词搜索 A 股股票（代码或名称，如“宁德”、“600519”）。返回最多 20 条：代码、名称。用户给出股票名而非代码时先用它换成代码。",
        params={"keyword": {"type": "string", "description": "搜索关键词：股票代码或名称，如 宁德时代、600519"}},
        required=["keyword"],
    ),
    "get_fund_metrics": ToolDef(
        name="get_fund_metrics",
        module="data.fund_api",
        fn="calc_fund_metrics",
        description="计算基金的量化指标：近1周/1月/3月/6月/1年各期收益率、最大回撤、年化波动率、夏普比率（无风险利率按2%）。评估“这只基金表现怎么样”时用。",
        params={
            "fund_code": {"type": "string", "description": "6 位基金代码，如 161725"},
            "days": {"type": "integer", "description": "回看天数，默认 365（近1年）"},
        },
        required=["fund_code"],
        none_error="未找到基金 {fund_code} 或历史数据不足",
    ),
    "get_fund_history": ToolDef(
        name="get_fund_history",
        module="data.fund_api",
        fn="get_fund_history",
        description="查询基金历史单位净值序列（返回日期+净值数组，可用于描述净值走势）。",
        params={
            "fund_code": {"type": "string", "description": "6 位基金代码，如 161725"},
            "days": {"type": "integer", "description": "回看天数，默认 365"},
        },
        required=["fund_code"],
        none_error="未找到基金 {fund_code} 的历史净值",
    ),
    "backtest_dca": ToolDef(
        name="backtest_dca",
        module="data.fund_api",
        fn="dca_result",
        description="基金定投回测：给定每月定投金额与月数，基于真实历史净值模拟，返回总投入、最终市值、盈亏与收益率。用户问“定投某基金能赚多少”时用（一次性买入请说明用回测工具的 lump_sum 思路，当前只支持定投）。",
        params={
            "fund_code": {"type": "string", "description": "6 位基金代码，如 161725"},
            "monthly_amount": {"type": "number", "description": "每月定投金额（元），如 1000"},
            "months": {"type": "integer", "description": "定投总月数，如 36"},
        },
        required=["fund_code", "monthly_amount", "months"],
        none_error="基金 {fund_code} 历史数据不足，无法回测",
    ),
    # --- M1 私域知识层（2026-09-15）：第 24 个工具 ---
    "retrieve_docs": ToolDef(
        name="retrieve_docs",
        module="utils.rag.retrieve",
        fn="retrieve_docs",
        description=(
            "检索本地私域知识库（上市公司公告 / 券商研报 / 财报原文），返回带来源与日期的原文片段。"
            "用户问「某公司最近的公告说了什么」「研报怎么看这个行业」等**需要文档原文**的问题时用它；"
            "实时行情、财务指标、资金流请用对应专用工具。检索为空时本工具会明确回「未找到相关公告」，"
            "此时**必须如实告知用户知识库中没有相关内容，不得凭记忆编造**。"
        ),
        params={
            "query": {"type": "string", "description": "检索问题或关键词，如「茅台上半年营收」"},
            "code": {"type": "string", "description": "可选，限定股票代码（如 600519）；不填则全库检索"},
            "top_n": {"type": "integer", "description": "返回条数，默认 5"},
        },
        required=["query"],
    ),
    "get_stock_info": ToolDef(
        name="get_stock_info",
        module="data.stock_api",
        fn="get_stock_info",
        description="查询个股实时行情：现价、今开/昨收/最高/最低、涨跌额与涨跌幅、成交量额、换手率、振幅、PE/PB。用户问“某股票现在多少钱”时用。",
        params=_stock_code_param(),
        required=["stock_code"],
        none_error="未找到股票 {stock_code}（可能停牌或代码有误）",
    ),
    "get_stock_kline": ToolDef(
        name="get_stock_kline",
        module="data.stock_api",
        fn="get_stock_kline",
        description="查询个股 K 线数据（前复权 OHLC + 成交量，日/周/月线）。分析“最近走势是什么形态”时用；数据点较多时只描述趋势特征（如高低点、连续阳/阴线），不要逐日罗列。",
        params={
            "stock_code": {"type": "string", "description": "6 位股票代码，如 600519"},
            "days": {"type": "integer", "description": "取最近 N 个交易日的数据，默认 60"},
            "ktype": {"type": "string", "description": "K 线周期：daily=日K（默认）/ weekly=周K / monthly=月K"},
        },
        required=["stock_code"],
        none_error="未找到股票 {stock_code} 的 K 线数据",
    ),
    "get_stock_moneyflow": ToolDef(
        name="get_stock_moneyflow",
        module="data.stock_api",
        fn="get_stock_moneyflow",
        description="查询个股最新资金流向：主力/大单/中单/小单净流入（元）与主力净流入占比。回答“主力资金在流入还是流出某股票”时用。",
        params=_stock_code_param(),
        required=["stock_code"],
        none_error="未查询到 {stock_code} 的资金流向数据",
    ),
    "get_market_moneyflow": ToolDef(
        name="get_market_moneyflow",
        module="data.moneyflow_api",
        fn="get_market_moneyflow",
        description="查询大盘资金全景：主力/超大单/大单/中单/小单净流入（亿元）、北向与南向资金净流入。回答“今天大盘资金面怎么样”时用。",
        params={},
        none_error="大盘资金流向数据不可得",
    ),
    "get_hot_sectors": ToolDef(
        name="get_hot_sectors",
        module="data.market_api",
        fn="get_hot_sectors",
        description="查询今日热门行业板块涨幅榜（前 10：板块名、代码、涨跌幅）。回答“今天哪个板块最强/最热”时用。",
        params={},
        none_error="板块行情数据不可得",
    ),
    "get_limit_up_review": ToolDef(
        name="get_limit_up_review",
        module="data.limit_up_api",
        fn="get_limit_up_review_data",
        description="查询今日涨停板复盘：涨停/跌停概览、连板天梯、板块分布、涨停原因分类、首板列表。回答“今天涨停复盘/连板情况”时用（数据量大，重点提炼概览与天梯）。",
        params={},
        none_error="涨停复盘数据不可得",
    ),
    "get_index_valuation": ToolDef(
        name="get_index_valuation",
        module="data.market_api",
        fn="get_valuation_data",
        description="查询主要宽基指数（沪深300/上证50/创业板指/中证500）的估值：PE/PB 及历史分位与估值状态（低估/合理/高估）。回答“某指数现在贵不贵、能不能定投”时用。",
        params={},
        none_error="指数估值数据不可得",
    ),
}


def resolve(fn_ref):
    """晚绑定解析：'module.fn' → 调用时动态查模块属性（mock.patch 可见）"""
    mod_name, fn_name = fn_ref.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    return getattr(mod, fn_name)


def _truncate(result, max_len=8000, list_top_n=20):
    """返回值截断（zcode 评审 §5.2）：长列表 top-N、超长 8000 截断，防 prompt 撑爆。

    递归处理 dict/list；标量统一 str()（DataFrame 等由 default=str 兜底前已截断）。
    """
    def trunc_str(s):
        s = str(s)
        if len(s) > max_len:
            return s[:max_len] + "...[截断 {} 字符]".format(len(s) - max_len)
        return s

    if isinstance(result, dict):
        return {k: _truncate(v, max_len, list_top_n) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        items = list(result)
        truncated = [_truncate(x, max_len, list_top_n) for x in items[:list_top_n]]
        if len(items) > list_top_n:
            truncated.append("[已截断，共 {} 项，仅显示前 {} 项]".format(len(items), list_top_n))
        return truncated
    if result is None:
        return None
    return trunc_str(result)


# ==================== 工具错误契约 ====================
# 错误返回在**旧中文文案之上**新增机器可判字段（旧文案保持不变，被存量测试断言）：
#   {"error": "<中文文案>", "error_code": "<码>", "tool": "<工具名>", "retryable": bool}
TOOL_ERR_UNKNOWN_TOOL = "UNKNOWN_TOOL"   # 注册表里没有该工具
TOOL_ERR_NOT_FOUND = "NOT_FOUND"         # 工具返回 None（声明了 none_error 模板）
TOOL_ERR_EXCEPTION = "TOOL_EXCEPTION"    # 执行抛异常
TOOL_ERR_UNKNOWN = "UNKNOWN_ERROR"       # 兜底：老格式 / 非 JSON 输出
TOOL_ERR_INVALID_ARGS = "INVALID_ARGS"   # 缺必填参数（P0-4）
TOOL_ERR_TIMEOUT = "TIMEOUT"             # 单次调用超时（P0-4，可重试）

# 网络/超时类异常视为可重试（requests 的超时与连接异常均继承自 OSError 系）
_RETRYABLE_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)

# 单次工具调用超时（秒）；设为 0 关闭超时控制（P0-4）
TOOL_TIMEOUT_SECONDS = 30


class _ToolTimeout(Exception):
    """**看门狗**超时（区别于工具内部自己抛的 TimeoutError）。

    两者语义不同：工具内部的网络超时属于「该工具执行失败」（TOOL_EXCEPTION，仍可重试）；
    看门狗超时是「我们等不下去了」（TIMEOUT）。
    """


def _call_tool_fn(fn, kwargs, timeout):
    """执行工具函数（带可选超时）。

    超时用线程池 `future.result(timeout)` 实现 —— ⚠️ 它**不杀线程**（Python 无法强杀），
    只是让调用方不再被阻塞，被放弃的线程会在后台自行结束。这是已知折中：
    换来的是「Agent 对话不会被一个卡住的行情接口拖死」。

    ⚠️ 这里**不能**用 `with ThreadPoolExecutor(...)`：其 `__exit__` 会
    `shutdown(wait=True)` 等线程跑完，超时控制就形同虚设
    （2026-09-15 由 P0-4 的超时测试当场抓出，故用 try/finally + wait=False）。
    """
    if not timeout or timeout <= 0:
        return fn(**kwargs)
    ex = ThreadPoolExecutor(max_workers=1)
    try:
        fut = ex.submit(fn, **kwargs)
        try:
            return fut.result(timeout=timeout)
        # ⚠️ 2026-09-17：必须**同时**抓 `concurrent.futures.TimeoutError` —— 它到 **3.11 才**成为
        # 内置 `TimeoutError` 的别名；3.9/3.10 上它是**另一个类**，只写 `except TimeoutError:` 抓不住
        # 看门狗超时 → 穿透到 `except Exception` → `error_code` 退化成 TOOL_EXCEPTION 且 `retryable=False`
        # （独立审计在 Python 3.10 上实测；CI 矩阵含 3.9、README 宣称 3.9+）。
        except (TimeoutError, FutureTimeoutError):
            if fut.done():
                raise          # 工具自己抛的 TimeoutError → 保持原语义（归 TOOL_EXCEPTION）
            raise _ToolTimeout("工具执行超过 {}s 看门狗上限".format(timeout))
    finally:
        ex.shutdown(wait=False)


def make_tool_error(message, code, tool_name, exc=None):
    """构造标准化的工具错误 JSON 字符串（中文文案 + 机器可判字段）。

    消费方（agent_run / SSE / 未来的 M1 检索与 M3 图节点）应据此分支：
    `error_code` 判类型、`retryable` 判能否重试。
    """
    retryable = bool(exc is not None and isinstance(exc, _RETRYABLE_EXCEPTIONS))
    return json.dumps({
        "error": message,
        "error_code": code,
        "tool": tool_name,
        "retryable": retryable,
    }, ensure_ascii=False)


def tool_output_error(output):
    """解析工具输出：失败 → 错误信息 dict；成功 → None。

    兼容三种形态：
    - 新格式（带 error_code / tool / retryable）
    - 老格式（只有 error 文案）→ 兜底 error_code=UNKNOWN_ERROR
    - 非 str / 非 JSON（异常路径）→ 同样按失败计，不抛异常
    """
    if not isinstance(output, str):
        return {"error": str(output), "error_code": TOOL_ERR_UNKNOWN, "tool": "", "retryable": False}
    try:
        data = json.loads(output)
    except Exception:
        return {"error": output, "error_code": TOOL_ERR_UNKNOWN, "tool": "", "retryable": False}
    if isinstance(data, dict) and data.get("error"):
        return {
            "error": data["error"],
            "error_code": data.get("error_code") or TOOL_ERR_UNKNOWN,
            "tool": data.get("tool", ""),
            "retryable": bool(data.get("retryable", False)),
        }
    return None


def execute_ai_tool_v2(tool_name, arguments):
    """执行一次 AI 工具调用（注册表分派），返回 JSON 字符串（回填给模型继续推理）。

    - 未知工具 → {"error": "未知工具：…"}（旧文案兼容）+ error_code=UNKNOWN_TOOL
    - 工具返回 None 且有 none_error 模板 → 按模板输出（旧文案兼容）+ error_code=NOT_FOUND
    - 缺必填参数 → {"error": "缺少必填参数：…"} + error_code=INVALID_ARGS（P0-4，不穿透给工具）
    - 单次调用超时（TOOL_TIMEOUT_SECONDS）→ error_code=TIMEOUT，retryable=True（P0-4）
    - 执行抛异常 → {"error": "工具执行出错：…"} + error_code=TOOL_EXCEPTION
      （网络/超时类异常额外带 retryable=True，调用方可据此重试）
    - 结果统一 _truncate 截断后 JSON 序列化（default=str 兜底 DataFrame 等）

    ⚠️ 契约（成功载荷）：**不得含非空的顶层 `error` 字段**。
    判定方 `tool_output_error()` 以「顶层 error 为真值」作为失败依据；已有先例
    `data/fund_api.py` 的 compare_funds_structured 成功时返回 {"ok": true, "error": "", ...}
    （空串是假值 → 判成功）。若未来有工具要用顶层 error 传「部分成功/告警」文案，
    必须先改这条约定与判定逻辑（2026-09-15 独立审计 🟡 条指出该启发式耦合）。
    """
    if not isinstance(arguments, dict):
        arguments = {}
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return make_tool_error("未知工具：{}".format(tool_name),
                               TOOL_ERR_UNKNOWN_TOOL, tool_name)
    # P0-4：必填校验（旧实现会把空值透传给工具 —— 等于用一次真实网络请求换一个看不懂的报错）
    missing = [k for k in (entry.required or []) if arguments.get(k) in (None, "")]
    if missing:
        return make_tool_error("缺少必填参数：{}".format("、".join(missing)),
                               TOOL_ERR_INVALID_ARGS, tool_name)
    try:
        fn = resolve(entry.fn_ref)
        kwargs = {k: v for k, v in arguments.items() if k in entry.param_names}
        result = _call_tool_fn(fn, kwargs, TOOL_TIMEOUT_SECONDS)
        if result is None and entry.none_error:
            fmt_args = {k: arguments.get(k, "") for k in entry.param_names}
            return make_tool_error(entry.none_error.format(**fmt_args),
                                   TOOL_ERR_NOT_FOUND, tool_name)
        payload = _truncate(result)
        return json.dumps(payload, ensure_ascii=False, default=str)
    except _ToolTimeout:
        return make_tool_error("工具执行超时（{}s）：{}".format(TOOL_TIMEOUT_SECONDS, tool_name),
                               TOOL_ERR_TIMEOUT, tool_name, exc=TimeoutError())
    except Exception as e:
        return make_tool_error("工具执行出错：{}".format(e),
                               TOOL_ERR_EXCEPTION, tool_name, exc=e)


# ⚠️ 2026-09-18 第六轮审计一 P3：此处原有 `tool_output_is_error()` —— **已删除**。
# 它的来历：P0-2 时代为修 `tool_end.ok` 恒 True 而加（见 CHANGELOG.md 第 9 行），
# 后来改成 `tool_output_error()` 的薄封装（第 14 行）。
# **删它的理由**：全仓**零引用** —— 连它 docstring 里写的"回归锁见
# `tests/test_p0_agent_fixes.py`"也不成立（那个文件断言的是 `payload["ok"]`，
# 测的是 `tool_output_error`）。**形状与上一轮那条 `load_holdout()` 死代码同族**：
# 名字留着、指向的保护并不存在。
# 判定"输出是否代表失败"现在统一走 `tool_output_error(output) is not None`。
# git 历史里仍可取回本函数。


# ==================== 兼容别名 ====================
# 旧名 + 旧错误文案保持（"未找到基金"/"未知工具"被存量 test_ai_tools 断言）；
# execute_ai_tool_v2 对 3 旧工具的行为与旧 if/elif 分派完全一致。
execute_ai_tool = execute_ai_tool_v2


# ==================== Agent 规划循环 ====================

from utils.rag.messages import NONE_EVIDENCE_DIRECTIVE

# ⚠️ 2026-09-18 第六轮审计二 P1-1：第 3 条的命令措辞改为**从 `messages` 取**。
# 此前这里（**系统提示词，权威高于工具返回值**）写着「必须明确告诉用户"该数据不可得"」，
# 而 `retrieve.py` 的 `NONE_EVIDENCE_NOTE` 早已改成「关联性**未能确认**」——
# ⇒ 对「判据假阴性、但语料确有答案」的查询（`rel-0014` gold 在 rank 4 / `rel-0015` 在 rank 1），
#   模型会照**更高权威**的指令发出**关于语料内容的假断言**。现在两处同源。
AGENT_SYSTEM_PROMPT = ("""你是"基金小助手"，一位越用越懂你的投资私人顾问。
你可以调用工具实时查询基金、大盘、个股诊断/估值/排雷/护城河、市场情绪、投资日记等数据。

## 执行规范（必须遵守）
1. 复杂问题先简述执行计划（1-3 步：打算做什么、依次调用哪些工具），再逐步执行。
2. 每一步工具返回后，基于真实返回的数据继续推理，可以连续调用多个工具组合分析。
3. 防幻觉守则：""" + NONE_EVIDENCE_DIRECTIVE + """
   注：`retrieve_docs` 的结果里若带 `evidence_level: "none"`，**即使 results 非空**也按本条处理 ——
   2026-09-18 起 none 档不再无条件清空 results（避免丢掉已检索到的正确证据），
   故本条是该档位的**行为约束**（措辞与 `utils/rag/messages.py` 的 `NONE_EVIDENCE_NOTE` **同源**，
   不再两处各写一半）。
4. 合规守则：只做分析辅助，不提供自动交易、不荐股；不给"买入/卖出"指令，
   只给分析依据与风险提示（延续免责声明）。

## 输出
用 Markdown 组织回答，简洁清晰，重点突出。""")


def agent_run(task, context=None, memory=False, session_id=None, tools=None,
              model=None, temperature=0.7, max_tool_rounds=8,
              continue_question=False, on_progress=None, structured_progress=False,
              parallel_tools=False):
    """带规划的 Agent 多轮执行循环（ai_chat / 诊断页"AI 追问"共用入口）。

    参数：
        task: 用户问题（str）
        context: 额外上下文（str 或 list[str]），拼进 system prompt（如诊断数据 + 会话记忆摘要）
        memory: 是否落库记忆（创建/续写 agent_sessions + agent_messages，满 8 轮自动摘要）
        session_id: 续写指定会话；memory=True 且为 None 时新建
        tools: 工具 schema 列表，None 用注册表全量
        model: 模型名；None（默认）= 取当前配置的 reasoner 模型，**在调用时解析**
            （旧版写成 model=_reasoner_model()，Python 默认参数只在 import 求值一次，
            导致设置页切换模型对 Agent 链路完全无效）。模型返回 reasoning_content
            原生思考流，由 on_progress("reasoning", ...) 实时透传给 UI 思考链
        max_tool_rounds: 工具调用轮次上限（默认 8，诊断类多步需要）
        continue_question: 追问链开关——memory 会话有历史时，把最近消息作为上下文带入
        on_progress: 进度回调 fn(stage, detail)——UI 实时思考链用，不传则无副作用。
            stage: "reasoning"（模型原生思考流文本）/ "tool"（工具调用）/
                   "writing"（组织最终回答）；detail 为人类可读描述
        structured_progress: M0 纯增量开关——True 时额外发结构化事件：
            ("tool_start", {"name", "arguments"}) / ("tool_end", {"name", "ok", "elapsed_ms"})
            给 SSE/API 桥消费；默认 False 行为与旧版完全一致

    返回：
        {"type": "text", "content": "...", "tool_trace": [...], "session_id": id|None}
    """

    # 晚绑定：默认 model 必须在**调用时**解析。
    # 若写成 model=_reasoner_model()，Python 默认参数只在 import 时求值一次 →
    # 用户在设置页切换模型后，Agent 对话链路仍用旧模型（配置链断点）。
    if model is None:
        model = _reasoner_model()

    def _progress(stage, detail):
        if on_progress:
            try:
                on_progress(stage, detail)
            except Exception:  # noqa: BLE001 - 进度展示失败不影响主流程
                pass

    def _progress_structured(stage, payload):
        if structured_progress and on_progress:
            try:
                on_progress(stage, payload)
            except Exception:  # noqa: BLE001 - 进度展示失败不影响主流程
                pass

    # 晚绑定：避免 import 环（ai_helper → agent_core），且让测试可 patch ai_helper.call_llm
    from utils import ai_helper
    from utils.agent_memory import ensure_session, record_message, maybe_summarize_session, get_agent_messages

    system = AGENT_SYSTEM_PROMPT
    if context:
        if isinstance(context, str):
            context = [context]
        parts = [str(c) for c in context if c is not None and str(c).strip()]
        if parts:
            system += "\n\n## 本次上下文\n" + "\n".join(parts)

    # v1.1 记忆显性化（粘性三件套 C）：memory 会话注入紧凑持仓快照（隐私开关可关），
    # 并向 UI 发 memory_used 事件——感知到的智能才产生粘性；无注入不发事件（不撒谎）。
    memory_sources = []
    prior = []
    if memory and session_id and continue_question:
        prior = get_agent_messages(session_id, limit=6)
        if any(m.get("role") in ("user", "assistant") and str(m.get("content") or "").strip()
               for m in prior):
            memory_sources.append("history")
    if memory and _ai_read_holdings_enabled() and not _demo_mode_on():
        brief = holdings_context_brief()
        if brief:
            system += ("\n\n## 已知用户上下文（来自用户本地持仓快照，仅供个性化引用；"
                       "引用时自然说明依据，禁止编造未提供的持仓事实）\n" + brief)
            memory_sources.append("holdings")
    if memory_sources:
        _progress_structured("memory_used", {"sources": memory_sources})

    messages = [{"role": "system", "content": system}]

    # 追问链：复用同一会话的最近对话（只重放 user/assistant 文本——
    # tool 消息缺 tool_call_id/前置 assistant.tool_calls 会被 DeepSeek 400 拒掉；
    # 工具结论已含在 assistant 回复里，落库仅作审计）
    for m in prior:
        role = m.get("role")
        content = str(m.get("content") or "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": str(task)})

    # 记忆落库：user 消息
    if memory:
        session_id = ensure_session(session_id, title=str(task)[:30])
        record_message(session_id, "user", str(task))

    tool_trace = []
    # P0-3-B：token 记账（跨轮累加；上游没给 usage 的轮次只计 calls）
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    if tools is None:
        tools = [t.schema for t in TOOL_REGISTRY.values()]

    for _round in range(max_tool_rounds + 1):
        # v1.2.1：思考模式开关——agent 链路默认显式关闭思考（旧 chat 行为：快+便宜）。
        # 原生思考流展示走 chat_with_tools/_reasoner_model 链路（那是诊断页 AI 追问）；
        # 若未来要 agent 思考，加 thinking=True 并处理 reasoning_content 回传契约。
        result = ai_helper.call_llm(messages, tools=tools, model=model, temperature=temperature, thinking=False)
        usage_total["calls"] += 1
        _u = result.get("usage")
        if _u:
            for _k in ("prompt_tokens", "completion_tokens", "total_tokens"):
                usage_total[_k] += _u.get(_k, 0)
        # 模型原生思考流（reasoner 才有；deepseek-chat 为空）实时透传
        if result.get("reasoning"):
            _progress("reasoning", result["reasoning"])
        if result.get("type") != "tool_call":
            _progress("writing", "组织最终回答")
            content = result.get("content") or ""
            if memory:
                record_message(session_id, "assistant", content)
                maybe_summarize_session(session_id)
            result.setdefault("tool_trace", tool_trace)
            result["session_id"] = session_id
            result["usage"] = usage_total
            return result

        tool_calls = result.get("content") or []
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        # 先解析全部调用（P0-4：解析与执行分离，便于并行）
        _parsed = []
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {}
            _parsed.append((tc, name, args))

        # P0-4：并行执行（**默认关**——工具内部对缓存/SQLite 的线程安全性尚未实测）。
        # 并行只影响「执行」；tool_start/tool_end 事件与 tool_trace 仍**按调用顺序**发出/回填。
        # 并行模式下 elapsed_ms 记的是整批耗时（顺序模式仍是单次耗时）。
        if parallel_tools and len(_parsed) > 1:
            _t0_batch = time.time()
            with ThreadPoolExecutor(max_workers=min(4, len(_parsed))) as _ex:
                _outputs = list(_ex.map(lambda _it: execute_ai_tool_v2(_it[1], _it[2]), _parsed))
            _batch_ms = int((time.time() - _t0_batch) * 1000)
            _timings = [_batch_ms] * len(_parsed)
        else:
            _outputs, _timings = [], []
            for _tc, _n, _a in _parsed:
                _t0 = time.time()
                _outputs.append(execute_ai_tool_v2(_n, _a))
                _timings.append(int((time.time() - _t0) * 1000))

        for (tc, name, args), output, _ms in zip(_parsed, _outputs, _timings):
            _progress("tool", "{}({})".format(
                name, ", ".join("{}={}".format(k, v) for k, v in list(args.items())[:2])))
            _progress_structured("tool_start", {"name": name, "arguments": args})
            _err_info = tool_output_error(output)
            _payload = {
                "name": name,
                "ok": _err_info is None,
                "elapsed_ms": _ms,
            }
            if _err_info:
                # 失败时带机器码与可重试标记。注意：**当前前端未消费这两个字段**
                # （前瞻性附加，供未来的 M3 图节点 / 降级重试逻辑分支用；勿在文案里
                # 宣称"消费方可按类型分支"——见 2026-09-15 独立审计 ⚪ 条）
                _payload["error_code"] = _err_info["error_code"]
                _payload["retryable"] = _err_info["retryable"]
            _progress_structured("tool_end", _payload)
            tool_trace.append({"name": name, "arguments": args, "output": output})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": output,
            })
            if memory:
                record_message(session_id, "tool", output)

    # 轮数超限：封顶提示
    content = "⚠️ 工具调用轮数超限，请把问题拆分后再试。"
    if memory:
        record_message(session_id, "assistant", content)
    return {"type": "text", "content": content, "tool_trace": tool_trace,
            "session_id": session_id, "usage": usage_total}


def build_tool_schemas():
    """对外 schema 列表（AI_TOOLS 的构建源，保持旧 shape：{"type":"function","function":{...}}）"""
    return [t.schema for t in TOOL_REGISTRY.values()]


# ==================== v1.1 记忆显性化：持仓快照注入 ====================

def _demo_mode_on():
    try:
        from utils.ai_helper import _is_demo_mode
        return bool(_is_demo_mode())
    except Exception:  # noqa: BLE001 - 判定失败按非演示处理
        return False


def _ai_read_holdings_enabled():
    """隐私开关「允许 AI 读取我的持仓」（默认开）；设置层不可用按默认。"""
    try:
        from services.settings_service import get_ai_read_holdings
        return bool(get_ai_read_holdings())
    except Exception:  # noqa: BLE001
        return True


def holdings_context_brief(max_rows=6):
    """紧凑持仓快照文本（LLM 友好，附量化指标数值）；无持仓/读取失败返回空串（调用方不注入）。

    网络成本说明：load_funds_snapshot 对前 max_rows 只拉历史净值算指标（各 ~1 请求），
    走 get_fund_history 的 TTL 缓存；冷启动首问 + 数秒可接受，换隐私收益值得。
    """
    try:
        from utils.ai_helper import load_funds_snapshot
        snap = load_funds_snapshot(max_funds_with_metrics=max_rows)
        funds = [dict(f) for f in (snap.get("funds") or [])]
    except Exception:  # noqa: BLE001 - 注入失败静默降级（无记忆≠错误）
        return ""
    if not funds:
        return ""
    lines = []
    for f in funds[:max_rows]:
        parts = ["{}({})".format(f.get("name") or "未命名基金", f.get("code", ""))]
        for label, key in (("金额", "amount"), ("成本净值", "cost_nav"), ("持有份额", "hold_shares")):
            try:
                v = float(f.get(key))
                if v > 0:
                    parts.append("{}={:g}".format(label, v))
            except (TypeError, ValueError):
                pass
        m = f.get("metrics")
        if isinstance(m, dict):
            nums = {}
            for k, v in m.items():
                try:
                    if isinstance(v, bool) or v is None:
                        continue
                    nums[k] = round(float(v), 4)
                except (TypeError, ValueError):
                    continue
            if nums:
                parts.append("指标=" + json.dumps(nums, ensure_ascii=False))
        lines.append("- " + "，".join(parts))
    return "共{}只持仓：\n".format(len(funds)) + "\n".join(lines)