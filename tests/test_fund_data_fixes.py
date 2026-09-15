# -*- coding: utf-8 -*-
"""基金数据链缺口回归锁（2026-09-15 由在线评测 P0-3-B 连带发现）

三个缺口（都不在 P0 范围内，是"评测真跑"顺藤摸瓜抓出来的）：

① `_fetch_fund_history` **按位置取列**：把 `row.iloc[0]`（实际是"净值日期"列）当净值去
   `float()` → 每次 ValueError → 全行 `continue` → **基金历史净值恒返回空**。
   连带：`calc_fund_metrics` / `backtest_dca` / 净值曲线 / `get_fund_history` 工具全废。
② `get_fund_info` 的 `dwjz` / `gsz` / `gszzl` / `gztime` 是**硬编码占位**（它只从
   `ak.fund_name_em()` 拿名字），但 `holdings_service`（持仓页净值）、`alert_service`
   （价格预警判涨跌幅）、`compare_funds`（基金对比）都当它是净值源 → 持仓页恒 `--`、
   **基金预警恒按涨跌 0 判断（永不触发）**。
③ 国内财经域名（eastmoney 等）**未绕系统代理**：走 v2rayN 代理时一旦抖动就
   `ProxyError ... RemoteDisconnected` → 股票 K 线/资金流整体不可得（评测实测）。
"""
import pandas as pd
import pytest

from data import fund_api


# ==================== ① 历史净值解析（按列名，不按位置/index） ====================


def _fake_nav_df():
    """akshare `fund_open_fund_info_em` 的真实形状（2026-09 实测）：index 是 0..N 整数"""
    return pd.DataFrame({
        "净值日期": ["2026-09-11", "2026-09-14", "2026-09-15"],
        "单位净值": [1.240, 1.235, 1.250],
        "日增长率": [0.10, -1.52, 1.21],
    })


def test_fetch_fund_history_parses_by_column_name(monkeypatch):
    """必须按列名解析：index 是整数、首列是日期 —— 旧实现两者都取错 → 恒空"""
    monkeypatch.setattr(fund_api, "_get_fund_list", lambda: pd.DataFrame())
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fund_api.ak, "fund_open_fund_info_em", lambda **kw: _fake_nav_df())
        dates, values = fund_api._fetch_fund_history("900001", days=5)
    assert dates is not None, "旧实现这里返回 None（全部行被跳过）"
    assert len(dates) == 3
    assert values[-1] == 1.25
    assert str(dates[-1])[:10] == "2026-09-15", "日期必须来自『净值日期』列，而不是 index"


def test_get_fund_history_returns_data(monkeypatch):
    """公开契约：返回 (dates, values)，不再是恒空"""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fund_api.ak, "fund_open_fund_info_em", lambda **kw: _fake_nav_df())
        dates, values = fund_api.get_fund_history("900002", days=5)
    assert len(dates) == 3 and values[-1] == 1.25


def test_fetch_fund_history_tolerates_column_rename(monkeypatch):
    """列名微调（不同 akshare 版本）仍要能解析"""
    df = pd.DataFrame({"日期": ["2026-09-15"], "单位净值": [1.250]})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fund_api.ak, "fund_open_fund_info_em", lambda **kw: df)
        dates, values = fund_api._fetch_fund_history("900003", days=5)
    assert len(dates) == 1 and values[0] == 1.25


# ==================== ② get_fund_info 补真实净值 ====================


def test_get_fund_info_fills_real_nav(monkeypatch):
    """dwjz / gszzl / gztime 必须是真实值（旧实现硬编码 '--' / 0 / ''）"""
    lst = pd.DataFrame({"基金代码": ["900004"], "基金简称": ["测试基金"]})
    nav_dates = pd.to_datetime(["2026-09-14", "2026-09-15"])
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fund_api, "_get_fund_list", lambda: lst)
        mp.setattr(fund_api, "get_fund_history",
                   lambda code, days=30: (nav_dates, [1.235, 1.250]))
        info = fund_api.get_fund_info("900004")
    assert info["name"] == "测试基金"
    assert info["dwjz"] == 1.25, "最新单位净值必须来自真实历史净值"
    assert info["gszzl"] == 1.21, "日涨跌幅应由相邻两个净值算出（1.250/1.235-1）"
    assert str(info["gztime"]).startswith("2026-09-15")


def test_get_fund_info_degrades_when_nav_unavailable(monkeypatch):
    """净值拿不到时降级为占位（不能抛异常、不能编个数）"""
    lst = pd.DataFrame({"基金代码": ["900005"], "基金简称": ["测试基金2"]})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fund_api, "_get_fund_list", lambda: lst)
        mp.setattr(fund_api, "get_fund_history", lambda code, days=30: ([], []))
        info = fund_api.get_fund_info("900005")
    assert info["name"] == "测试基金2"
    assert info["dwjz"] == "--"
    assert info["gszzl"] == 0


# ==================== ③ 国内财经域名绕系统代理 ====================


def test_domestic_finance_hosts_bypass_proxy(monkeypatch):
    """国内财经域名必须绕过代理；海外 API 仍需代理（否则模型调不通）

    注意：`requests.utils.should_bypass_proxies(url, no_proxy)` 的第二个参数是 no_proxy
    本身，**不是** proxies dict —— 要验环境变量生效，得用 `get_environ_proxies(url)`
    （绕过时代理 dict 为空）。
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10808")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10808")
    import utils.common  # noqa: F401  触发模块级 NO_PROXY 设置
    from requests.utils import get_environ_proxies

    for host in ("https://push2his.eastmoney.com/api/qt/stock/kline/get",
                 "https://fund.eastmoney.com/js/x.js",
                 "https://hq.sinajs.cn/list=sh600519"):
        assert get_environ_proxies(host) == {}, "应绕过代理: %s" % host
    assert get_environ_proxies(
        "https://api.deepseek.com/chat/completions") != {}, "海外 API 不能绕代理"
