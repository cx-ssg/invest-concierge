# -*- coding: utf-8 -*-
"""Golden set —— 评测用例单一事实源（P0-3，见 docs/COVERAGE_DESIGN.md §11.2）

**离线**（tests/test_golden_offline.py）与**在线**（未来 scripts/eval_agent.py）共用同一份用例。

字段：
    id           唯一标识（用例表变更时便于追踪）
    question     喂给 agent 的用户问题
    expect_tools 期望的工具调用序列（离线：驱动 mock LLM；在线：判命中率）
    tool_args    可选，给 mock 用的参数（键必须是该工具真实 param_names 的子集）
    expect_facts 期望回答中出现/覆盖的事实要点（**离线阶段只存档**，在线阶段才断言）
    tags         分类标签（便于按场景筛选与统计）

⚠️ 本表最大的坑：**工具名 / 参数名写错会静默失效**（离线测不出、在线才发现）。
   tests/test_golden_offline.py 对此有专门的契约校验（工具名存在性 + 参数名合法性）。

诚实边界（2026-09-15 独立审计判定）：
- 离线测试**不是评测**：mock LLM 按本表的 expect_tools 吐工具序列、agent 再执行同一序列，
  本质是「编排冒烟 + 用例表自校验」，**不能证明模型选得对**（那是在线阶段的事）。
- 在线评测（scripts/eval_agent.py）判定约定：**按工具集合命中**，顺序不敏感
  （模型可能先估值再诊断）；仅对显式标注 `order_sensitive: True` 的用例按序列判定。
"""

CASES = [
    # ---------- 基金基础 ----------
    {"id": "fund_info_001", "question": "帮我查一下基金 000001 的基本信息",
     "expect_tools": ["get_fund_info"], "tool_args": {"get_fund_info": {"fund_code": "000001"}},
     "expect_facts": ["基金名称", "净值"], "tags": ["基金", "单工具"]},

    {"id": "search_fund_002", "question": "帮我搜一下沪深300相关的基金",
     "expect_tools": ["search_fund"], "tool_args": {"search_fund": {"keyword": "沪深300"}},
     "expect_facts": ["基金代码"], "tags": ["基金", "搜索"]},

    {"id": "fund_metrics_003", "question": "000001 最近一年的收益率和最大回撤是多少",
     "expect_tools": ["get_fund_metrics"],
     "tool_args": {"get_fund_metrics": {"fund_code": "000001", "days": 365}},
     "expect_facts": ["收益率", "最大回撤"], "tags": ["基金", "指标"]},

    {"id": "fund_history_004", "question": "000001 最近的净值走势怎么样",
     "expect_tools": ["get_fund_history"],
     "tool_args": {"get_fund_history": {"fund_code": "000001", "days": 90}},
     "expect_facts": ["净值", "日期"], "tags": ["基金", "净值"]},

    {"id": "compare_funds_005", "question": "对比一下 000001 和 110022 这两只基金",
     "expect_tools": ["compare_funds"],
     "tool_args": {"compare_funds": {"fund_code1": "000001", "fund_code2": "110022"}},
     "expect_facts": ["对比", "收益"], "tags": ["基金", "对比"]},

    {"id": "dca_backtest_006", "question": "帮我回测一下 000001 每月定投 1000 块的效果",
     "expect_tools": ["backtest_dca"],
     "tool_args": {"backtest_dca": {"fund_code": "000001", "monthly_amount": 1000, "months": 12}},
     "expect_facts": ["定投", "收益"], "tags": ["基金", "回测"]},

    # ---------- 持仓 ----------
    {"id": "load_funds_007", "question": "看看我持仓的基金现在什么情况",
     "expect_tools": ["load_funds"], "expect_facts": ["持仓", "收益"],
     "tags": ["持仓", "单工具"]},

    # ---------- 大盘 / 市场 ----------
    {"id": "market_index_008", "question": "今天大盘怎么样",
     "expect_tools": ["get_market_index"], "expect_facts": ["指数", "涨跌"],
     "tags": ["大盘", "单工具"]},

    {"id": "market_sentiment_009", "question": "现在市场情绪如何",
     "expect_tools": ["get_market_sentiment"], "expect_facts": ["情绪"],
     "tags": ["大盘", "情绪"]},

    {"id": "market_moneyflow_010", "question": "今天大盘的资金流向是什么情况",
     "expect_tools": ["get_market_moneyflow"], "expect_facts": ["资金", "流入"],
     "tags": ["大盘", "资金"]},

    {"id": "hot_sectors_011", "question": "今天哪些板块涨幅靠前",
     "expect_tools": ["get_hot_sectors"], "expect_facts": ["板块", "涨幅"],
     "tags": ["大盘", "板块"]},

    {"id": "limit_up_012", "question": "帮我复盘一下今天的涨停板",
     "expect_tools": ["get_limit_up_review"], "expect_facts": ["涨停"],
     "tags": ["大盘", "复盘"]},

    {"id": "index_valuation_013", "question": "沪深300 现在的估值分位是多少",
     "expect_tools": ["get_index_valuation"], "expect_facts": ["估值", "分位"],
     "tags": ["大盘", "估值"]},

    # ---------- 个股 ----------
    {"id": "stock_info_014", "question": "600519 这家公司基本情况怎么样",
     "expect_tools": ["get_stock_info"], "tool_args": {"get_stock_info": {"stock_code": "600519"}},
     "expect_facts": ["公司", "行业"], "tags": ["个股", "基本面"]},

    {"id": "stock_diagnosis_015", "question": "帮我诊断一下 600519",
     "expect_tools": ["get_stock_diagnosis"],
     "tool_args": {"get_stock_diagnosis": {"stock_code": "600519"}},
     "expect_facts": ["诊断"], "tags": ["个股", "诊断"]},

    {"id": "stock_valuation_016", "question": "600519 现在估值贵不贵",
     "expect_tools": ["get_stock_valuation"],
     "tool_args": {"get_stock_valuation": {"stock_code": "600519"}},
     "expect_facts": ["估值"], "tags": ["个股", "估值"]},

    {"id": "stock_minefield_017", "question": "帮我给 000858 排雷",
     "expect_tools": ["get_stock_minefield"],
     "tool_args": {"get_stock_minefield": {"stock_code": "000858"}},
     "expect_facts": ["风险"], "tags": ["个股", "排雷"]},

    # 2026-09-15 审计指出：原问题写"贵州茅台"未给代码，在线评测时模型可能先 search_stock → 改为直接给代码
    {"id": "stock_moat_018", "question": "600519 的护城河怎么样",
     "expect_tools": ["get_stock_moat"], "tool_args": {"get_stock_moat": {"stock_code": "600519"}},
     "expect_facts": ["护城河"], "tags": ["个股", "护城河"]},

    {"id": "stock_kline_019", "question": "600519 最近的 K 线走势",
     "expect_tools": ["get_stock_kline"],
     "tool_args": {"get_stock_kline": {"stock_code": "600519", "days": 60}},
     "expect_facts": ["K线", "收盘"], "tags": ["个股", "行情"]},

    {"id": "stock_moneyflow_020", "question": "600519 今天的资金流向",
     "expect_tools": ["get_stock_moneyflow"],
     "tool_args": {"get_stock_moneyflow": {"stock_code": "600519"}},
     "expect_facts": ["资金", "主力"], "tags": ["个股", "资金"]},

    {"id": "search_stock_021", "question": "帮我搜一下宁德时代",
     "expect_tools": ["search_stock"], "tool_args": {"search_stock": {"keyword": "宁德时代"}},
     "expect_facts": ["股票代码"], "tags": ["个股", "搜索"]},

    # ---------- 日记（写操作） ----------
    {"id": "diary_add_022", "question": "记一笔：今天买入 000001 一千块",
     "expect_tools": ["add_diary"],
     "tool_args": {"add_diary": {"fund_code": "000001", "action": "buy", "amount": 1000}},
     "expect_facts": ["已记录"], "tags": ["日记", "写操作"]},

    {"id": "diary_get_023", "question": "看看我的投资日记",
     "expect_tools": ["get_diary"], "expect_facts": ["日记"],
     "tags": ["日记", "单工具"]},

    # ---------- 多工具编排 ----------
    {"id": "multi_diagnosis_024", "question": "全面分析一下 600519：诊断 + 估值 + 排雷",
     "expect_tools": ["get_stock_diagnosis", "get_stock_valuation", "get_stock_minefield"],
     "tool_args": {"get_stock_diagnosis": {"stock_code": "600519"},
                   "get_stock_valuation": {"stock_code": "600519"},
                   "get_stock_minefield": {"stock_code": "600519"}},
     "expect_facts": ["诊断", "估值", "风险"], "tags": ["多工具", "个股"]},

    {"id": "multi_portfolio_025", "question": "看看我的持仓、大盘走势和市场情绪",
     "expect_tools": ["load_funds", "get_market_index", "get_market_sentiment"],
     "expect_facts": ["持仓", "指数", "情绪"], "tags": ["多工具", "组合"]},

    {"id": "multi_stock_full_026", "question": "600519 的基本面、K线、资金流都看一下",
     "expect_tools": ["get_stock_info", "get_stock_kline", "get_stock_moneyflow"],
     "tool_args": {"get_stock_info": {"stock_code": "600519"},
                   "get_stock_kline": {"stock_code": "600519"},
                   "get_stock_moneyflow": {"stock_code": "600519"}},
     "expect_facts": ["公司", "K线", "资金"], "tags": ["多工具", "个股"]},
]


# 离线契约的覆盖下限（2026-09-15 审计建议：20 形同虚设 → 提到与注册表等值 23）
# 语义：新增工具若没同步进 golden set，这条断言立刻失败（防「加了工具却没加用例」）
MIN_TOOL_COVERAGE = 23
