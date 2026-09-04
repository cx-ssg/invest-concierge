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
from typing import Optional, List, Dict, Any

from config import DEEPSEEK_MODEL, DEEPSEEK_REASONER_MODEL

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


def execute_ai_tool_v2(tool_name, arguments):
    """执行一次 AI 工具调用（注册表分派），返回 JSON 字符串（回填给模型继续推理）。

    - 未知工具 → {"error": "未知工具：..."}（旧文案兼容）
    - 工具返回 None 且有 none_error 模板 → 按模板输出（get_fund_info 的"未找到基金"旧文案）
    - 结果统一 _truncate 截断后 JSON 序列化（default=str 兜底 DataFrame 等）
    """
    if not isinstance(arguments, dict):
        arguments = {}
    entry = TOOL_REGISTRY.get(tool_name)
    if not entry:
        return json.dumps({"error": "未知工具：{}".format(tool_name)}, ensure_ascii=False)
    try:
        fn = resolve(entry.fn_ref)
        kwargs = {k: v for k, v in arguments.items() if k in entry.param_names}
        result = fn(**kwargs)
        if result is None and entry.none_error:
            fmt_args = {k: arguments.get(k, "") for k in entry.param_names}
            return json.dumps({"error": entry.none_error.format(**fmt_args)}, ensure_ascii=False)
        payload = _truncate(result)
        return json.dumps(payload, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": "工具执行出错：{}".format(e)}, ensure_ascii=False)


# ==================== 兼容别名 ====================
# 旧名 + 旧错误文案保持（"未找到基金"/"未知工具"被存量 test_ai_tools 断言）；
# execute_ai_tool_v2 对 3 旧工具的行为与旧 if/elif 分派完全一致。
execute_ai_tool = execute_ai_tool_v2


# ==================== Agent 规划循环 ====================

AGENT_SYSTEM_PROMPT = """你是"基金小助手"，一位越用越懂你的投资私人顾问。
你可以调用工具实时查询基金、大盘、个股诊断/估值/排雷/护城河、市场情绪、投资日记等数据。

## 执行规范（必须遵守）
1. 复杂问题先简述执行计划（1-3 步：打算做什么、依次调用哪些工具），再逐步执行。
2. 每一步工具返回后，基于真实返回的数据继续推理，可以连续调用多个工具组合分析。
3. 防幻觉守则：工具返回 {"error": ...} 或空数据/全 None 时，必须明确告诉用户"该数据不可得"，
   并说明原因；绝不猜测、绝不编造数据。
4. 合规守则：只做分析辅助，不提供自动交易、不荐股；不给"买入/卖出"指令，
   只给分析依据与风险提示（延续免责声明）。

## 输出
用 Markdown 组织回答，简洁清晰，重点突出。"""


def agent_run(task, context=None, memory=False, session_id=None, tools=None,
              model=DEEPSEEK_REASONER_MODEL, temperature=0.7, max_tool_rounds=8,
              continue_question=False, on_progress=None, structured_progress=False):
    """带规划的 Agent 多轮执行循环（ai_chat / 诊断页"AI 追问"共用入口）。

    参数：
        task: 用户问题（str）
        context: 额外上下文（str 或 list[str]），拼进 system prompt（如诊断数据 + 会话记忆摘要）
        memory: 是否落库记忆（创建/续写 agent_sessions + agent_messages，满 8 轮自动摘要）
        session_id: 续写指定会话；memory=True 且为 None 时新建
        tools: 工具 schema 列表，None 用注册表全量
        model: 模型名，默认 deepseek-reasoner（返回 reasoning_content 原生思考流，
            由 on_progress("reasoning", ...) 实时透传给 UI 思考链）
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

    messages = [{"role": "system", "content": system}]

    # 追问链：复用同一会话的最近对话（只重放 user/assistant 文本——
    # tool 消息缺 tool_call_id/前置 assistant.tool_calls 会被 DeepSeek 400 拒掉；
    # 工具结论已含在 assistant 回复里，落库仅作审计）
    if memory and session_id and continue_question:
        prior = get_agent_messages(session_id, limit=6)
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
    if tools is None:
        tools = [t.schema for t in TOOL_REGISTRY.values()]

    for _round in range(max_tool_rounds + 1):
        result = ai_helper.call_llm(messages, tools=tools, model=model, temperature=temperature)
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
            return result

        tool_calls = result.get("content") or []
        messages.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {}
            _progress("tool", "{}({})".format(
                name, ", ".join("{}={}".format(k, v) for k, v in list(args.items())[:2])))
            _progress_structured("tool_start", {"name": name, "arguments": args})
            # 模块级名调用 → 测试可 patch agent_core.execute_ai_tool_v2
            _t0 = time.time()
            output = execute_ai_tool_v2(name, args)
            _ok = isinstance(output, str) and not output.startswith("工具执行失败")
            _progress_structured("tool_end", {
                "name": name,
                "ok": _ok,
                "elapsed_ms": int((time.time() - _t0) * 1000),
            })
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
    return {"type": "text", "content": content, "tool_trace": tool_trace, "session_id": session_id}


def build_tool_schemas():
    """对外 schema 列表（AI_TOOLS 的构建源，保持旧 shape：{"type":"function","function":{...}}）"""
    return [t.schema for t in TOOL_REGISTRY.values()]