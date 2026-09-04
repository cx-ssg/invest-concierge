# -*- coding: utf-8 -*-
"""P1 后续优化单测（2026-09-04 实测驱动）：行情/净值两类缓存的节流与负缓存行为。

背景（P1 验收实录，docs/AGENT_TOOLS_PLAN.md）：东财 clist 反爬按 IP 限流，
全市场列表 ~59 页分页；60s TTL + 失败不缓存会让 agent 连续工具调用高频重拉整表，
是触发封禁的元凶。锁定两个行为：
1. _get_akshare_spot_df：成功缓存 300s / 失败负缓存 300s（窗口内零重试）；
2. get_fund_history：真实拉取走 _fetch_fund_history（1h 成功缓存 / 5min 失败负缓存），
   公开契约 (dates, values) 与空数据 ([], []) 不变——monkeypatch 目标从
   get_fund_history 顺延到 _fetch_fund_history 仍然只影响缓存层，不改计算层。
"""
import datetime
from unittest.mock import patch

from data import fund_api, stock_api
from data.cache import _cache


# ==================== spot 缓存：TTL 300 + 负缓存 ====================

def _reset_spot_cache():
    stock_api._akshare_spot_cache = None
    stock_api._akshare_spot_cache_time = 0
    stock_api._akshare_spot_fail_time = 0


def test_spot_cache_ttl_is_300():
    """防回归：全市场快照 TTL 从 60 提到 300（反爬节流），负缓存窗口存在"""
    assert stock_api._AKSHARE_SPOT_CACHE_TTL == 300
    assert stock_api._AKSHARE_SPOT_FAIL_TTL == 300


def test_spot_success_cached_within_ttl():
    """成功拉取后窗口内重复调用零网络请求（mock 断言只调一次）"""
    import pandas as pd
    df = pd.DataFrame({"代码": ["600519"], "名称": ["贵州茅台"], "最新价": [1500.0]})
    _reset_spot_cache()
    with patch.object(stock_api, "call_akshare_with_retry", return_value=df) as mock_fetch:
        assert stock_api._get_akshare_spot_df() is not None
        assert stock_api._get_akshare_spot_df() is not None
        assert stock_api._get_akshare_spot_df() is not None
        assert mock_fetch.call_count == 1  # 300s 窗口内不重拉


def test_spot_failure_negatively_cached():
    """拉取失败 → 负缓存窗口内后续调用直接返回 None，不再打数据源"""
    _reset_spot_cache()
    with patch.object(stock_api, "call_akshare_with_retry", return_value=None) as mock_fetch:
        assert stock_api._get_akshare_spot_df() is None
        assert stock_api._get_akshare_spot_df() is None  # 负缓存命中
        assert stock_api._get_akshare_spot_df() is None
        assert mock_fetch.call_count == 1  # 只试了一次，没连环重试


def test_spot_recovers_after_fail_window():
    """负缓存窗口过后恢复重试（自愈，不死锁）"""
    _reset_spot_cache()
    import pandas as pd
    df = pd.DataFrame({"代码": ["600519"], "名称": ["贵州茅台"]})
    with patch.object(stock_api, "call_akshare_with_retry", return_value=None):
        stock_api._get_akshare_spot_df()  # 失败进入负缓存
    # 模拟窗口已过
    stock_api._akshare_spot_fail_time -= stock_api._AKSHARE_SPOT_FAIL_TTL + 1
    with patch.object(stock_api, "call_akshare_with_retry", return_value=df) as mock_fetch:
        out = stock_api._get_akshare_spot_df()
        assert out is not None and mock_fetch.call_count == 1


# ==================== fund_history：契约不变 + 缓存层归一 ====================

def test_fund_history_contract_unchanged():
    """公开契约：成功 (dates, values)；数据不足/失败 ([], [])——P1 工具依赖此形态"""
    dates = [datetime.date(2026, 1, i) for i in range(1, 21)]
    values = [1.0 + i * 0.01 for i in range(20)]
    with patch.object(fund_api, "_fetch_fund_history", return_value=(dates, values)):
        d, v = fund_api.get_fund_history("161725")
        assert d == dates and v == values
    with patch.object(fund_api, "_fetch_fund_history", return_value=None):
        d, v = fund_api.get_fund_history("999999")
        assert d == [] and v == []


def test_fund_history_fetch_cached_1h():
    """真实拉取层走 cached：同参重复调用只打一次网络；空数据负缓存 5 分钟"""
    dates = [datetime.date(2026, 1, i) for i in range(1, 21)]
    values = [1.0 + i * 0.01 for i in range(20)]
    try:
        _cache.clear()
        with patch.object(fund_api.ak, "fund_open_fund_info_em",
                          return_value=None) as mock_ak:
            assert fund_api._fetch_fund_history("999999") is None  # 空 → None 进负缓存
            assert fund_api._fetch_fund_history("999999") is None
            assert mock_ak.call_count == 1  # 5 分钟窗口内不重试
    finally:
        _cache.clear()


def test_fund_history_cached_result_is_tuple():
    """cached 层必须原样存取 tuple（dates, values）——calc_fund_metrics 直接解包"""
    import pandas as pd
    idx = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
    df = pd.DataFrame({"单位净值": [1.0, 1.1, 1.2]}, index=idx)
    try:
        _cache.clear()
        with patch.object(fund_api.ak, "fund_open_fund_info_em", return_value=df) as mock_ak:
            d1, v1 = fund_api.get_fund_history("161725", days=3)
            d2, v2 = fund_api.get_fund_history("161725", days=3)
            assert (d1, v1) == (d2, v2)
            assert mock_ak.call_count == 1  # 1h 窗口内命中缓存
    finally:
        _cache.clear()
