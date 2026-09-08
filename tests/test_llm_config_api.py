# -*- coding: utf-8 -*-
"""v1.2 模型接入（多 provider）API 单测。

覆盖：
1. GET /api/settings/llm：provider 注册表 + key 掩码（无配置 source=env/none）
2. POST /api/settings/llm：保存 siliconflow + key → 回读 provider/source/key 掩码；
   api_key 空串沿用已有；"-" 清除；未知 provider 400 语义（ok=false）
3. POST /api/settings/llm/test：mock OpenAI 成功 / 401 翻译 / 缺 key 拒绝
4. 动态链生效：保存 DB key 后 agent_service.config() 的 api_key_configured 翻真
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import llm_config  # noqa: E402
from data import database  # noqa: E402


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_FILE", str(tmp_path / "t.db"))
    database.init_db()
    yield


@pytest.fixture()
def client(tmp_db):
    from fastapi.testclient import TestClient
    from server.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_llm_settings(tmp_db):
    """每测前后清 llm_* 设置，防串扰"""
    from data.database import set_setting
    for k in ("llm_provider", "llm_api_key", "llm_base_url", "llm_model", "llm_reasoner_model"):
        set_setting(k, "")
    llm_config._TEST_KEY_OVERRIDE = None
    yield
    llm_config._TEST_KEY_OVERRIDE = None


def _clear_key():
    llm_config._TEST_KEY_OVERRIDE = ""  # 屏蔽本机 .env 真 key，测纯 DB 链


def test_llm_view_masks_key_and_lists_providers(client):
    _clear_key()
    r = client.get("/api/settings/llm").json()
    assert r["ok"] is True
    assert set(r["providers"]) == {"deepseek", "siliconflow", "dashscope", "custom"}
    assert r["providers"]["deepseek"]["label"] == "DeepSeek 官方"
    assert r["api_key_configured"] is False  # 钩子清空 + DB 空
    assert r["source"] == "none"


def test_llm_save_and_roundtrip(client):
    _clear_key()
    r = client.post("/api/settings/llm", json={
        "provider": "siliconflow", "api_key": "sk-sf-test-1234567890",
    }).json()
    assert r["ok"] is True
    assert r["provider"] == "siliconflow"
    assert r["source"] == "settings"
    assert r["api_key_configured"] is True
    assert "sk-sf" not in r.get("api_key_masked", "") or "****" in r["api_key_masked"]
    assert r["model"] == "deepseek-ai/DeepSeek-V4-Flash"  # 默认模型回填


def test_llm_save_empty_key_keeps_existing(client):
    _clear_key()
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": "sk-abc-12345678"})
    r = client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": ""}).json()
    assert r["api_key_configured"] is True  # 空串不覆盖
    r2 = client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": "-"}).json()
    assert r2["api_key_configured"] is False  # "-" 清除


def test_llm_save_unknown_provider_rejected(client):
    r = client.post("/api/settings/llm", json={"provider": "nope", "api_key": "x"}).json()
    assert r["ok"] is False and "未知 provider" in r["error"]


def test_llm_test_ok(client):
    _clear_key()
    fake = MagicMock()
    fake.choices[0].message.content = "pong"
    with patch("openai.OpenAI") as mk:
        mk.return_value.chat.completions.create.return_value = fake
        r = client.post("/api/settings/llm/test", json={
            "provider": "deepseek", "api_key": "sk-x"}).json()
    assert r["ok"] is True and r["reply"] == "pong"
    assert "latency_ms" in r


def test_llm_test_401_translated(client):
    _clear_key()
    with patch("openai.OpenAI") as mk:
        mk.return_value.chat.completions.create.side_effect = Exception("Error code: 401 - Invalid API key")
        r = client.post("/api/settings/llm/test", json={
            "provider": "deepseek", "api_key": "sk-bad"}).json()
    assert r["ok"] is False and "401" in r["error"]


def test_llm_test_requires_key(client):
    _clear_key()
    r = client.post("/api/settings/llm/test", json={"provider": "deepseek"}).json()
    assert r["ok"] is False and "Key" in r["error"]


def test_db_key_activates_agent_engine(client):
    """保存 DB key 后 /api/agent/config 立即认为引擎可用（动态链生效）"""
    _clear_key()
    before = client.get("/api/agent/config").json()
    assert before["api_key_configured"] is False
    client.post("/api/settings/llm", json={"provider": "deepseek", "api_key": "sk-live-123456789"})
    after = client.get("/api/agent/config").json()
    assert after["api_key_configured"] is True


def test_custom_provider_requires_base_url(client):
    _clear_key()
    with patch("openai.OpenAI") as mk:
        r = client.post("/api/settings/llm/test", json={
            "provider": "custom", "api_key": "sk-x", "base_url": "", "model": "gpt-x"}).json()
    assert r["ok"] is False and "Base URL" in r["error"]
