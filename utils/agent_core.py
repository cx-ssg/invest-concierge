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
from typing import Optional, List, Dict, Any

from config import DEEPSEEK_MODEL

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
        fn="_load_funds_from_store",
        description="查询用户当前的基金持仓列表（基金代码、名称、成本净值、市值、持有份额）。演示模式下返回内置示例持仓。",
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
        fn="compare_funds",
        description=(
            "对比两只基金的基本情况（名称、最新净值、估算净值、今日涨跌幅）。"
            "注意：本工具返回的是 Markdown 文本或错误提示字符串，不是 JSON 对象。"
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
              model=DEEPSEEK_MODEL, temperature=0.7, max_tool_rounds=8,
              continue_question=False):
    """带规划的 Agent 多轮执行循环（ai_chat / 诊断页"AI 追问"共用入口）。

    参数：
        task: 用户问题（str）
        context: 额外上下文（str 或 list[str]），拼进 system prompt（如诊断数据 + 会话记忆摘要）
        memory: 是否落库记忆（创建/续写 agent_sessions + agent_messages，满 8 轮自动摘要）
        session_id: 续写指定会话；memory=True 且为 None 时新建
        tools: 工具 schema 列表，None 用注册表全量
        max_tool_rounds: 工具调用轮次上限（默认 8，诊断类多步需要）
        continue_question: 追问链开关——memory 会话有历史时，把最近消息作为上下文带入

    返回：
        {"type": "text", "content": "...", "tool_trace": [...], "session_id": id|None}
    """
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

    # 追问链：复用同一会话的最近消息（避免上轮结果丢失）
    if memory and session_id and continue_question:
        prior = get_agent_messages(session_id, limit=6)
        for m in prior:
            role = m.get("role")
            content = m.get("content")
            if role in ("user", "assistant", "tool") and content:
                messages.append({"role": role, "content": str(content)})

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
        if result.get("type") != "tool_call":
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
            # 模块级名调用 → 测试可 patch agent_core.execute_ai_tool_v2
            output = execute_ai_tool_v2(name, args)
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