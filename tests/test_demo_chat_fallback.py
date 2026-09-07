# -*- coding: utf-8 -*-
"""演示模式 AI 对话降级测试（v1.1 bugfix：demo+无Key 穿帮修复）

用户场景：给别人演示时开了演示模式，一发消息报「请先配置 API Key」——穿帮。
修复契约：demo 开 + 无 Key → chat_with_tools 返回基于内置演示数据的示例回答
（标注演示内容），不报错、不外发请求。
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.ai_helper as ah


def _demo_no_key():
    """演示开 + 无 Key 环境"""
    ah.set_demo_mode(True)
    ah.API_KEY = ""
    return ah


def test_demo_no_key_returns_sample_not_error():
    """demo+无Key：返回示例回答（含演示标注），不报无 Key 错"""
    m = _demo_no_key()
    try:
        r = m.chat_with_tools([{"role": "user", "content": "分析我的持仓"}])
        assert r.get("type") == "text"
        content = r.get("content", "")
        assert "请先配置" not in content, f"demo 模式不应报无Key错：{content[:50]}"
        assert "演示" in content, "示例回答应标注演示内容"
    finally:
        m.set_demo_mode(False)


def test_demo_no_key_no_network_call():
    """demo+无Key：不发起任何网络请求（纯本地降级）"""
    m = _demo_no_key()
    try:
        with patch.object(m, "call_llm", side_effect=AssertionError("demo 降级不应调 LLM")):
            r = m.chat_with_tools([{"role": "user", "content": "随便分析下"}])
            assert r.get("type") == "text"
    finally:
        m.set_demo_mode(False)


def test_no_key_no_demo_still_errors():
    """非 demo + 无Key：保持原报错行为（提示配置），回归保护"""
    m = ah
    m.set_demo_mode(False)
    m.API_KEY = ""
    r = m.chat_with_tools([{"role": "user", "content": "test"}])
    assert "请先配置" in r.get("content", "")
