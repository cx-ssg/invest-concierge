# -*- coding: utf-8 -*-
"""多智能体分析编排单测：mock LLM，验证调用次数、角色顺序与降级行为"""
from unittest.mock import patch

from utils import ai_helper

ROLE_ORDER = ['fundamentals', 'technical', 'sentiment', 'risk']


def test_multi_agent_calls_llm_5_times_in_order():
    calls = []

    def fake_call_llm(messages, model=None, temperature=None):
        calls.append(messages[0]['content'])
        return {'type': 'text', 'content': '最终评级：推荐'}

    with patch.object(ai_helper, 'call_llm', side_effect=fake_call_llm):
        r = ai_helper.multi_agent_stock_analysis('600519', '贵州茅台', {'PE': 25})

    assert len(calls) == 5                                  # 4 分析师 + 1 主席
    assert list(r['analyst_reports'].keys()) == ROLE_ORDER  # 角色顺序稳定
    assert '基本面分析师' in calls[0]
    assert '风控' in calls[3]
    assert '交易决策委员会主席' in calls[4]
    assert 'PE' in calls[0]                                 # stock_data 注入上下文
    assert r['rating'] == '推荐'
    assert r['success'] is True
    assert r['debate'] == '最终评级：推荐'


def test_multi_agent_degrades_when_llm_fails():
    def fake_call_llm(messages, model=None, temperature=None):
        return {'type': 'tool_call', 'content': None}       # 非 text 视为失败

    with patch.object(ai_helper, 'call_llm', side_effect=fake_call_llm):
        r = ai_helper.multi_agent_stock_analysis('600519')

    assert r['analyst_reports']['fundamentals']['report'] == '分析出错，请重试'
    assert r['debate'] == '综合判断生成失败'
    assert r['rating'] == '未评级'
