# -*- coding: utf-8 -*-
"""发版前审查修复的回归测试（2026-09-07）

1. config 数据目录：exe（frozen）→ %LOCALAPPDATA%/invest-concierge；源码 → 项目根；
   INVEST_DATA_DIR 显式覆盖。防"数据散落 exe 旁/多份数据库"复发。
2. 周报 AI 失败降级卡不落库：同周幂等缓存不得锁死失败结果。
"""
import importlib
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_data_dir_source_mode_is_repo_root():
    """源码跑：数据目录 = 项目根（config.py 所在目录）"""
    import config
    assert config.DB_FILE.startswith(os.path.dirname(os.path.abspath(config.__file__)))
    assert os.path.isabs(config.DB_FILE)


def test_data_dir_frozen_goes_localappdata(monkeypatch=None):
    """exe（frozen）：数据目录 = %LOCALAPPDATA%/invest-concierge"""
    import config
    fake_appdata = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_appdata")
    os.makedirs(fake_appdata, exist_ok=True)
    with mock.patch.dict(os.environ, {"LOCALAPPDATA": fake_appdata}, clear=False):
        with mock.patch.object(sys, "frozen", True, create=True):
            importlib.reload(config)
            assert os.path.dirname(config.DB_FILE) == os.path.join(fake_appdata, "invest-concierge")
    # 还原源码模式
    importlib.reload(config)
    assert config.DB_FILE.startswith(os.path.dirname(os.path.abspath(config.__file__)))


def test_data_dir_env_override():
    """INVEST_DATA_DIR 显式覆盖优先级最高"""
    import config
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_datadir")
    os.makedirs(target, exist_ok=True)
    with mock.patch.dict(os.environ, {"INVEST_DATA_DIR": target}, clear=False):
        importlib.reload(config)
        assert config.DB_FILE.startswith(target)
    importlib.reload(config)


def test_weekly_ai_failure_not_cached():
    """AI 层失败：返回 degraded 卡但不落 reports 表（同周可重试）"""
    import services.report_service as rs
    agg = {
        "period": "2099-W01", "fund_count": 0, "funds": [],
        "total_invest": 0, "total_market_value": None, "total_week_pnl": None,
        "portfolio_weekly_pct": None,
        "index_weekly": {"code": "000300", "name": "沪深300", "weekly_pct": None,
                         "unavailable_note": "x"},
        "hs300_valuation": None,
    }
    with mock.patch.object(rs, "_week_key", return_value="2099-W01"), \
         mock.patch.object(rs, "get_report", return_value=None), \
         mock.patch.object(rs, "aggregate_weekly", return_value=agg), \
         mock.patch.object(rs, "save_report") as save_mock:
        import config as _c
        old_key = _c.API_KEY
        _c.API_KEY = "sk-test"
        try:
            with mock.patch("utils.agent_core.agent_run", side_effect=RuntimeError("网络炸了")):
                r = rs.generate_weekly(force=True)
        finally:
            _c.API_KEY = old_key
        assert r["degraded"] is True
        assert "AI 点评生成失败" in r["content"]
        assert "可稍后重试" in r["content"]
        save_mock.assert_not_called(), "AI 失败的降级卡不得落库"
