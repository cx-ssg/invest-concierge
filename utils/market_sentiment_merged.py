# -*- coding: utf-8 -*-
"""
市场情绪工具封装 - 三函数打包（对 LLM 暴露可读结论，而非六个分散的裸 dict）。

打包：get_limit_up_down_data + get_top_board_height + get_market_breadth
三函数均为 @cached(CACHE_SENTIMENT) 的独立数据函数，无同名主函数——
本模块提供唯一的 get_market_sentiment 入口，供 agent_core TOOL_REGISTRY 注册。

实现依据：docs/AGENT_MVP_DESIGN.md §3 首批工具清单。
"""
import sys

from data.sentiment_api import get_limit_up_down_data, get_top_board_height, get_market_breadth

# Windows 控制台 GBK 防护
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def get_market_sentiment():
    """市场情绪三指标打包：涨跌停家数 / 最高连板 / 市场涨跌家数。

    单指标失败不阻断其余指标，失败字段为 None，errors 记录原因（Agent 依此明说"数据不可得"）。
    """
    result = {
        "limit_up_down": None,
        "board_height": None,
        "breadth": None,
        "errors": [],
    }
    try:
        result["limit_up_down"] = get_limit_up_down_data()
    except Exception as e:  # noqa: BLE001
        result["errors"].append("涨跌停数据失败：{}".format(e))
    try:
        result["board_height"] = get_top_board_height()
    except Exception as e:  # noqa: BLE001
        result["errors"].append("连板高度失败：{}".format(e))
    try:
        result["breadth"] = get_market_breadth()
    except Exception as e:  # noqa: BLE001
        result["errors"].append("市场涨跌家数失败：{}".format(e))
    return result