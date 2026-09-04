# -*- coding: utf-8 -*-
"""
AI 助手模块 - AI 调用、提示词、对话管理等
使用 OpenAI SDK 调用 DeepSeek API
"""

import json
from openai import OpenAI

from config import DEEPSEEK_API_BASE, DEEPSEEK_MODEL, API_KEY
from data.database import load_funds
from data.fund_api import get_fund_info, safe_float_convert, calc_fund_metrics, compare_funds
from data.market_api import get_market_index, get_valuation_data, get_hot_sectors, calc_market_sentiment
# Agent MVP：execute_ai_tool 兼容别名 + AI_TOOLS 派生自 agent_core.TOOL_REGISTRY（11 工具）。
# 晚绑定：execute_ai_tool_v2 经 resolve() 调用时动态查 ai_helper 模块属性，
# 存量 test_ai_tools 的 patch.object(ai_helper, "get_fund_info", ...) 因此仍然生效。
# （agent_core 不反向 import ai_helper 模块级符号，无循环 import。）
from utils.agent_core import execute_ai_tool, build_tool_schemas


def _get_client():
    """获取 OpenAI 客户端（延迟初始化）"""
    if not API_KEY:
        return None
    return OpenAI(
        api_key=API_KEY,
        base_url=DEEPSEEK_API_BASE
    )


def call_llm(prompt, tools=None, model="deepseek-chat", temperature=0.7):
    """统一的 LLM 调用函数

    参数：
        prompt: 字符串或消息列表（格式为 [{"role": "system", "content": ...}, {"role": "user", "content": ...}]）
        tools: 工具定义列表（可选），当不为空时启用 tool_choice="auto"
        model: 模型名称，默认 "deepseek-chat"
        temperature: 温度参数，默认 0.7

    返回：
        - 纯文本模式：{"type": "text", "content": "回复内容"}
        - 工具调用模式：{"type": "tool_call", "content": tool_calls}
    """
    if not API_KEY:
        return {"type": "text", "content": "⚠️ 请先配置 DeepSeek API Key 才能使用 AI 功能哦~"}

    client = _get_client()

    # 构建 messages 参数
    if isinstance(prompt, str):
        messages = [{"role": "user", "content": prompt}]
    elif isinstance(prompt, list):
        messages = prompt
    else:
        return {"type": "text", "content": "❌ 不支持的 prompt 格式"}

    try:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 2000,
        }

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        if tools and choice.finish_reason == "tool_calls":
            tool_calls = []
            for tc in choice.message.tool_calls:
                tool_calls.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                })
            # reasoner 模型在决定工具调用时也会返回原生思考流
            reasoning = getattr(choice.message, "reasoning_content", None) or ""
            return {"type": "tool_call", "content": tool_calls, "reasoning": reasoning}

        content = choice.message.content
        if content is None:
            content = ""
        # reasoner 的原生思考流（deepseek-chat 为空字符串）
        reasoning = getattr(choice.message, "reasoning_content", None) or ""
        return {"type": "text", "content": content, "reasoning": reasoning}

    except Exception as e:
        error_msg = str(e)
        if "timeout" in error_msg.lower():
            return {"type": "text", "content": "⏰ AI 响应超时了，请稍后再试~"}
        elif "connection" in error_msg.lower():
            return {"type": "text", "content": "🔌 网络连接失败，请检查网络设置~"}
        elif "unauthorized" in error_msg.lower() or "401" in error_msg:
            return {"type": "text", "content": "🔑 API Key 无效，请检查配置~"}
        elif "rate" in error_msg.lower() or "429" in error_msg:
            return {"type": "text", "content": "⏳ 请求太频繁了，请稍后再试~"}
        else:
            return {"type": "text", "content": "❌ AI 调用出错：{}".format(error_msg)}


def get_ai_response(messages):
    """调用 DeepSeek API 获取 AI 回复（兼容旧接口，返回纯文本）"""
    result = call_llm(messages, model=DEEPSEEK_MODEL, temperature=0.7)
    return result["content"]


def build_system_prompt():
    """构建系统提示词"""
    funds = _load_funds_from_store()
    fund_context = ""
    if funds:
        fund_context = "\n用户当前持仓基金：\n"
        for code, fund in funds.items():
            fund_data = get_fund_info(code)
            cost_nav = fund.get('cost_nav', 0) or 0
            shares = fund.get('hold_shares', 0) or 0
            cost = cost_nav * shares
            if fund_data:
                current_price = safe_float_convert(
                    fund_data.get('gsz', fund_data.get('dwjz', 0)), default=cost_nav)
                fund_name = fund_data.get('name', fund.get('name', ''))
                today_change = safe_float_convert(fund_data.get('gszzl', 0), default=0)
            else:
                current_price = cost_nav
                fund_name = fund.get('name', '')
                today_change = 0

            current_value = shares * current_price
            profit = current_value - cost
            profit_rate = (profit / cost) * 100 if cost > 0 else 0

            fund_context += "- {}（{}）：成本{:.2f}元，当前市值{:.2f}元，收益{:.2f}元（{:.2f}%），今日涨跌{:.2f}%\n".format(
                fund_name, code, cost, current_value, profit, profit_rate, today_change
            )

    # 获取市场数据
    market_data = get_market_index()
    market_context = ""
    if market_data:
        market_context = "\n当前大盘指数：\n"
        for idx in market_data:
            market_context += "- {}：{}（{}{}%）\n".format(
                idx['name'], idx['price'],
                "+" if idx['change_percent'] >= 0 else "", idx['change_percent']
            )

    # 获取市场情绪
    sentiment = calc_market_sentiment()
    sentiment_context = ""
    if sentiment:
        sentiment_context = "\n市场情绪：{}（{}分）\n建议：{}\n".format(
            sentiment['status'], sentiment['score'], sentiment['suggestion']
        )

    system_prompt = """你是一个专业的基金投资顾问 AI 助手，名叫"基金小助手"。
你的职责是帮助用户分析基金、提供投资建议、解答基金相关问题。

## 核心能力
1. **基金分析**：分析单只或多只基金的表现、风险、持仓等
2. **投资建议**：根据用户的风险偏好和持仓情况，提供个性化的投资建议
3. **市场解读**：解读当前市场行情、热点板块、政策影响等
4. **知识问答**：回答基金投资相关的各种问题
5. **持仓诊断**：分析用户当前持仓的合理性，指出潜在风险
6. **智能推荐**：根据用户需求和市场情况推荐合适的基金

## 回答风格
- 专业但不晦涩，让新手也能听懂
- 数据驱动，尽量引用具体数据
- 客观中立，不推荐具体个股
- 提示风险，不保证收益
- 用 emoji 让回答更生动
- 回答简洁明了，重点突出

## 当前市场环境
{market_context}
{sentiment_context}

## 用户持仓信息
{fund_context}

请根据以上信息，为用户提供专业的基金投资建议。""".format(
        market_context=market_context,
        sentiment_context=sentiment_context,
        fund_context=fund_context
    )

    return system_prompt


def get_ai_recommendation():
    """获取 AI 智能推荐"""
    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请根据我的持仓和市场情况，给我一些基金投资建议和推荐。包括：1）当前持仓分析 2）市场机会 3）具体操作建议"}
    ]
    return get_ai_response(messages)


def get_ai_diagnosis():
    """获取 AI 持仓诊断"""
    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请对我的基金持仓进行全面诊断，包括：1）持仓结构分析 2）风险提示 3）优化建议"}
    ]
    return get_ai_response(messages)


def get_ai_tech_analysis():
    """获取 AI 科技赛道分析"""
    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请分析当前科技赛道（半导体、AI、新能源等）的投资机会，包括：1）各细分赛道分析 2）相关基金推荐 3）风险提示"}
    ]
    return get_ai_response(messages)


def get_ai_fund_analysis(fund_code):
    """获取 AI 对单只基金的分析"""
    fund_info = get_fund_info(fund_code)
    if not fund_info:
        return "未找到基金 {}，请检查基金代码是否正确".format(fund_code)

    metrics = calc_fund_metrics(fund_code)
    metrics_text = ""
    if metrics:
        returns_text = "、".join(["{}：{}%".format(k, v) for k, v in metrics['returns'].items()])
        metrics_text = "\n基金指标：\n- 各周期收益：{}\n- 最大回撤：{}%\n- 年化波动率：{}%\n- 夏普比率：{}".format(
            returns_text, metrics['max_drawdown'], metrics['volatility'], metrics['sharpe']
        )

    system_prompt = """你是一个专业的基金投资顾问 AI 助手。
请对以下基金进行详细分析，包括基金特点、风险收益特征、适合人群等。

基金信息：
- 名称：{fund_name}
- 代码：{fund_code}
- 最新净值：{nav}
- 估算净值：{gsz}
- 今日涨跌：{change}%
- 更新时间：{update_time}
{metrics}

请给出客观专业的分析，包括优点和风险。""".format(
        fund_name=fund_info.get('name', ''),
        fund_code=fund_code,
        nav=fund_info.get('dwjz', '--'),
        gsz=fund_info.get('gsz', '--'),
        change=fund_info.get('gszzl', 0),
        update_time=fund_info.get('gztime', ''),
        metrics=metrics_text
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请分析这只基金"}
    ]
    return get_ai_response(messages)


def get_ai_market_analysis():
    """获取 AI 市场分析"""
    system_prompt = build_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请分析当前市场行情，包括：1）主要指数表现 2）热点板块 3）资金流向 4）后市展望"}
    ]
    return get_ai_response(messages)


def get_ai_compare_analysis(fund_code1, fund_code2):
    """获取 AI 对两只基金的对比分析"""
    compare_result = compare_funds(fund_code1, fund_code2)
    system_prompt = """你是一个专业的基金投资顾问 AI 助手。
请根据以下两只基金的对比数据，给出专业的对比分析建议。

{compare_result}

请从收益、风险、持仓、适合人群等角度进行对比分析。""".format(compare_result=compare_result)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请对比分析这两只基金"}
    ]
    return get_ai_response(messages)

# ============================================
# 多智能体股票分析（借鉴 TradingAgents 框架）
# ============================================

# 四个专家角色定义
STOCK_ANALYST_ROLES = {
    "fundamentals": {
        "name": "基本面分析师",
        "icon": "📊",
        "prompt": "你是一位资深基本面分析师。只从以下维度分析，不涉及技术面或情绪面。\n- 财务数据：营收、净利润、ROE、毛利率\n- 估值水平：PE、PB、PS，与行业均值对比\n- 成长性：近3年复合增长率\n- 风险点：负债率、现金流、质押比例\n输出格式：\n## 📊 基本面分析\n### 核心财务指标\n### 估值判断（低估/合理/高估）\n### 主要风险\n### 一句话总结",
    },
    "technical": {
        "name": "技术分析师",
        "icon": "📈",
        "prompt": "你是一位资深技术分析师。只从技术指标维度分析。\n- 趋势：均线排列、MACD\n- 动能：RSI、KDJ、成交量\n- 支撑/压力位\n- 形态识别\n输出格式：\n## 📈 技术面分析\n### 趋势判断\n### 关键支撑/压力位\n### 技术指标信号\n### 一句话总结",
    },
    "sentiment": {
        "name": "情绪分析师",
        "icon": "💬",
        "prompt": "你是一位市场情绪分析师。只从情绪和资金维度分析。\n- 新闻舆情：正面/负面信号\n- 资金流向：主力净流入/流出\n- 龙虎榜动向（如适用）\n- 市场整体情绪\n输出格式：\n## 💬 情绪面分析\n### 新闻舆情摘要\n### 资金动向\n### 市场情绪判断\n### 一句话总结",
    },
    "risk": {
        "name": "风控分析师",
        "icon": "🛡️",
        "prompt": "你是一位风控专家。只从风险维度分析，不给买卖建议。\n- 下行风险：最大回撤、波动率\n- 流动性风险：日均换手率\n- 行业风险：政策、周期\n- 仓位建议：适合的仓位比例\n输出格式：\n## 🛡️ 风险分析\n### 主要风险因素\n### 极端情景推演\n### 仓位建议\n### 一句话总结",
    },
}

DEBATE_PROMPT = """你现在是交易决策委员会主席。以下是四位独立分析师对 {stock_name}({stock_code}) 的报告：

{analyst_reports}

请执行以下流程：

## 第一步：找出分歧
列出四位分析师之间观点矛盾的地方。

## 第二步：多方 vs 空方辩论
- 🐂 多方论点
- 🐻 空方论点
- ⚖️ 各自论据的强弱分析

## 第三步：综合判断
给出最终投资评级（5档：强烈推荐/推荐/中性/谨慎/回避）。

## 第四步：操作建议
如果当前持有，建议的操作（加仓/持有/减仓/清仓）。

输出格式使用 Markdown。"""


def multi_agent_stock_analysis(stock_code, stock_name="", stock_data=None):
    """多智能体股票分析——4位专家独立分析+辩论+综合判断

    借鉴 TradingAgents 框架的多 Agent 协作模式。

    参数：
        stock_code: 股票代码
        stock_name: 股票名称
        stock_data: 股票数据字典（可选）

    返回：
        dict: analyst_reports, debate, rating, success
    """
    stock_label = stock_name + "（" + stock_code + "）" if stock_name else stock_code

    # 构建数据上下文
    data_context = ""
    if stock_data:
        data_context = "\n\n以下为该股票已知数据，请在分析中使用：\n"
        for key, value in stock_data.items():
            data_context += "- " + str(key) + ": " + str(value) + "\n"

    # 第一阶段：四位专家独立分析
    analyst_reports = {}
    for role_key, role_info in STOCK_ANALYST_ROLES.items():
        full_prompt = role_info["prompt"] + data_context
        messages = [
            {"role": "system", "content": full_prompt},
            {"role": "user", "content": "请对 " + stock_label + " 进行" + role_info["name"] + "。"},
        ]
        result = call_llm(messages, model=DEEPSEEK_MODEL, temperature=0.7)

        if result["type"] == "text":
            analyst_reports[role_key] = {
                "name": role_info["name"],
                "icon": role_info["icon"],
                "report": result["content"],
            }
        else:
            analyst_reports[role_key] = {
                "name": role_info["name"],
                "icon": role_info["icon"],
                "report": "分析出错，请重试",
            }

    # 拼接所有专家报告
    reports_text = ""
    for role_key, report in analyst_reports.items():
        reports_text += "\n\n---\n\n### " + report["icon"] + " " + report["name"] + "\n\n" + report["report"]

    # 第二阶段：辩论 + 综合判断
    debate_prompt = DEBATE_PROMPT.format(
        stock_name=stock_name or stock_code,
        stock_code=stock_code,
        analyst_reports=reports_text,
    )

    messages = [
        {"role": "system", "content": "你是交易决策委员会主席。请基于四位独立分析师报告，组织辩论并给出综合判断。"},
        {"role": "user", "content": debate_prompt},
    ]
    debate_result = call_llm(messages, model=DEEPSEEK_MODEL, temperature=0.7)
    debate_text = debate_result["content"] if debate_result["type"] == "text" else "综合判断生成失败"

    # 提取评级
    rating = "未评级"
    for keyword in ["强烈推荐", "推荐", "中性", "谨慎", "回避"]:
        if keyword in debate_text:
            rating = keyword
            break

    return {
        "success": True,
        "stock_code": stock_code,
        "stock_name": stock_name,
        "analyst_reports": analyst_reports,
        "debate": debate_text,
        "rating": rating,
    }


# ============================================
# 演示 seed + AI 工具调用（ai_chat 接入）
# ============================================

# 内置示例持仓（演示模式用）：字段与 data.database.load_funds 一致（纯内存 seed，不写数据库）
DEMO_FUNDS = {
    "161725": {"name": "招商中证白酒指数(LOF)A", "code": "161725",
               "amount": 20000.0, "cost_nav": 1.1200, "hold_shares": 17857.14},
    "110022": {"name": "易方达消费行业股票", "code": "110022",
               "amount": 15000.0, "cost_nav": 3.4200, "hold_shares": 4385.96},
    "000961": {"name": "天弘沪深300ETF联接A", "code": "000961",
               "amount": 10000.0, "cost_nav": 1.6500, "hold_shares": 6060.61},
}


def seed_demo_funds():
    """演示 seed 函数：返回内置示例持仓（不改数据库结构、不落盘，仅存在于内存）"""
    return dict(DEMO_FUNDS)


# 演示模式进程级开关（M0：FastAPI 服务化后无 st.session_state，
# Streamlit 端勾选时同步调 set_demo_mode；默认 False 与原行为一致）
_demo_flag = False


def set_demo_mode(enabled):
    """设置演示模式（Streamlit 页面在开关变化时调用；服务层也可按请求头切换）"""
    global _demo_flag
    _demo_flag = bool(enabled)


def _is_demo_mode():
    """演示模式开关：优先进程级 flag（服务层/测试），否则回落 st.session_state（Streamlit 兼容）"""
    if _demo_flag:
        return True
    try:
        import streamlit as st
        return bool(st.session_state.get("use_demo_funds", False))
    except Exception:
        return False


def _load_funds_from_store():
    """AI 上下文/工具读取持仓的统一入口：演示模式用内置示例数据，否则读真实持仓库"""
    if _is_demo_mode():
        return dict(DEMO_FUNDS)
    return load_funds()


def load_funds_snapshot(max_funds_with_metrics=6, metrics_days=365):
    """P1（2026-09-04）：持仓快照 = 持仓列表 + 逐只量化指标。

    在 _load_funds_from_store 基础上附加每只基金的 calc_fund_metrics 结果
    （各期收益率/最大回撤/年化波动率/夏普），指标缺 None 不带键、单只失败不影响整体。
    网络成本控制：带指标的持仓最多 max_funds_with_metrics 只（历史净值是逐只网络请求），
    其余持仓只带基础字段；净值序列（dates/values）太长对 LLM 无意义，剔除。
    """
    funds = _load_funds_from_store()
    result = {"count": len(funds), "funds": []}
    items = list(funds.values())
    for i, fund in enumerate(items):
        row = dict(fund)
        if i < max_funds_with_metrics:
            try:
                m = calc_fund_metrics(row.get("code", ""), days=metrics_days)
                if m:
                    m = dict(m)
                    m.pop("dates", None)
                    m.pop("values", None)
                    row["metrics"] = m
            except Exception:
                pass  # 单只指标失败不影响整体快照
        result["funds"].append(row)
    return result


# ==================== OpenAI tools schema ====================
# Agent MVP：AI_TOOLS 由 utils.agent_core.TOOL_REGISTRY 派生（11 个声明式工具），
# 对外 shape 不变（{"type":"function","function":{...}}），ai_chat 无感知。

AI_TOOLS = build_tool_schemas()


def chat_with_tools(messages, tools=None, model=DEEPSEEK_MODEL, temperature=0.7, max_tool_rounds=4):
    """带工具调用的多轮对话循环（ai_chat 主入口）。

    流程：
        1) messages(tools=tools, tool_choice="auto") 交给模型；
        2) 模型请求工具 → execute_ai_tool 执行并把结果回填为 role="tool" 消息；
        3) 循环至多 max_tool_rounds 轮，直到模型给出纯文本回答。

    返回：
        {"type": "text", "content": "最终回答", "tool_trace": [{"name", "arguments", "output"}, ...]}
    """
    if not API_KEY:
        return {"type": "text", "content": "⚠️ 请先配置 DeepSeek API Key 才能使用 AI 功能哦~", "tool_trace": []}

    history = [dict(m) for m in messages]
    tool_trace = []
    tools = AI_TOOLS if tools is None else tools

    for _round in range(max_tool_rounds + 1):
        result = call_llm(history, tools=tools, model=model, temperature=temperature)
        if result.get("type") != "tool_call":
            result.setdefault("tool_trace", tool_trace)
            return result

        tool_calls = result.get("content") or []
        history.append({"role": "assistant", "content": None, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
            except Exception:
                args = {}
            output = execute_ai_tool(name, args)
            tool_trace.append({"name": name, "arguments": args, "output": output})
            history.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": output,
            })

    return {"type": "text", "content": "⚠️ 工具调用轮数超限，请把问题拆分后再试。", "tool_trace": tool_trace}
