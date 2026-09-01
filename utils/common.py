# -*- coding: utf-8 -*-
"""
通用工具函数
"""

import ipaddress
import json
import logging
import os
import re
import socket
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Union, Any, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

# ==================== JSON 工具 ====================


def safe_json_parse(text, label="数据"):
    """安全解析 JSON，失败返回 None 并打印日志"""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError) as e:
        print("解析{0}失败：{1}".format(label, e))
        return None


# ==================== 字典/序列访问工具 ====================


def safe_get(obj, key, default=None):
    """安全地从字典或类字典对象中获取键值，失败返回 default"""
    if obj is None:
        return default
    try:
        return obj.get(key, default)
    except (AttributeError, TypeError):
        try:
            return obj[key]
        except (KeyError, TypeError, IndexError):
            return default


def safe_get_dict(obj, *keys, default=None):
    """安全地链式获取嵌套字典值，如 safe_get_dict(d, 'a', 'b', 'c') 返回 d['a']['b']['c']"""
    if obj is None:
        return default
    current = obj
    for key in keys:
        if current is None:
            return default
        try:
            if isinstance(current, dict):
                current = current.get(key, default)
            else:
                try:
                    current = current[key]
                except (KeyError, TypeError, IndexError):
                    return default
        except (AttributeError, TypeError):
            return default
    return current


# ==================== 数字工具 ====================


def safe_float_convert(value, default=0.0, label=""):
    """安全转换浮点数"""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        if label:
            print("转换{0}失败：{1} - {2}".format(label, value, e))
        return default


# ==================== 友好错误提示 ====================


def friendly_error(error_type="general"):
    """返回友好的错误提示"""
    messages = {
        "api": "⚠️ 外部数据接口暂时无响应，请稍后再试。",
        "network": "⚠️ 网络连接异常，请检查网络后重试。",
        "data": "⚠️ 数据解析失败，可能是格式异常。",
        "general": "⚠️ 处理请求时出现异常，请稍后再试。",
    }
    return messages.get(error_type, messages["general"])


# ==================== JSON 文件读写 ====================


def load_json_file(filepath, default=None):
    """从 JSON 文件读取数据"""
    if default is None:
        default = {}
    try:
        if not os.path.exists(filepath):
            return default
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning("读取文件失败 %s: %s", filepath, e)
        return default


def save_json_file(filepath, data):
    """保存数据到 JSON 文件"""
    if not is_safe_write_path(filepath):
        logger.error("拒绝写入项目目录之外的路径：%s", filepath)
        return False
    try:
        real = os.path.realpath(filepath)
        Path(real).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True
    except (OSError, TypeError) as e:
        logger.error("保存文件失败 %s: %s", filepath, e)
        return False


# ==================== 投资计算 ====================


def calc_profit_rate(profit, cost):
    """计算收益率（保留两位小数）"""
    if cost is None or cost == 0:
        return 0.0
    return round((profit / cost) * 100, 2)


def get_fund_current_price(fund_data):
    """从基金数据中提取当前价格"""
    if not fund_data:
        return 0.0
    return safe_float_convert(
        fund_data.get("gsz", fund_data.get("dwjz", 0)),
        default=0,
        label="基金当前价格",
    )


# ==================== Streamlit 安全重运行 ====================


def safe_rerun():
    """安全执行 st.rerun，兼容新旧版本"""
    import streamlit as st

    try:
        st.rerun()
    except AttributeError:
        try:
            st.experimental_rerun()
        except AttributeError:
            pass


# ==================== 安全防护工具 ====================

# 项目根目录（utils/ 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 域名安全校验结果缓存：同一域名只解析一次
_DNS_SAFE_CACHE = {}


def is_safe_public_url(url):
    """校验外链 URL 是否安全：仅允许 http/https，且主机不得为
    localhost/环回/私有/保留/链路本地地址（防 SSRF）。
    返回 False 时调用方应拒绝发起请求。"""
    try:
        parsed = urlparse(str(url))
        scheme = (parsed.scheme or "").lower()
        host = parsed.hostname
    except (ValueError, AttributeError):
        return False
    if scheme not in ("http", "https") or not host:
        return False
    host = host.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".lan")):
        return False
    # host 本身是 IP 字面量的情况
    try:
        ip = ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback or ip.is_reserved
                    or ip.is_link_local or ip.is_multicast or ip.is_unspecified)
    except ValueError:
        pass
    # 域名：解析后逐一校验解析结果（带缓存）
    if host in _DNS_SAFE_CACHE:
        return _DNS_SAFE_CACHE[host]
    safe = True
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_reserved
                    or ip.is_link_local or ip.is_multicast or ip.is_unspecified):
                safe = False
                break
    except (socket.gaierror, ValueError):
        safe = False
    _DNS_SAFE_CACHE[host] = safe
    return safe


def is_safe_write_path(filepath):
    """写入路径必须位于项目目录之内（防路径穿越）"""
    try:
        real = os.path.realpath(os.path.abspath(filepath))
        root = os.path.realpath(_PROJECT_ROOT)
        return os.path.commonpath([real, root]) == root
    except (ValueError, OSError):
        return False


# ==================== 网络请求工具 ====================


def safe_request(url, params=None, headers=None, timeout=10, max_retries=2):
    """带重试机制的安全网络请求，失败时返回 None"""
    if not is_safe_public_url(url):
        print("已拒绝不安全的请求地址：{}".format(url))
        return None
    last_error = None
    for attempt in range(1, max_retries + 2):  # 尝试 max_retries+1 次
        try:
            if headers:
                resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            else:
                resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_error = e
            print("网络请求失败（第{0}次尝试）：{1} - {2}".format(attempt, url, e))
            if attempt <= max_retries:
                print("等待 1 秒后重试...")
                time.sleep(1)
    print("网络请求最终失败：{0} - {1}".format(url, last_error))
    return None


# ==================== AI 相关工具 ====================


def ai_not_configured_message():
    """返回 AI 未配置时的提示消息"""
    return (
        "⚠️ 还没有配置 DeepSeek API Key，AI 功能暂时不可用。\n\n"
        "请任选一种方式配置后重启：\n"
        "1. 在项目目录创建 local_env.bat，写入：set DEEPSEEK_API_KEY=你的key\n"
        "2. 设置系统环境变量 DEEPSEEK_API_KEY 后重启终端"
    )


# ==================== 文本分析 ====================


def is_portfolio_query(text):
    """判断用户是不是在问持仓/收益"""
    keywords = ["持仓", "赚了", "收益", "我的基金", "亏了", "赔了", "赚多少", "亏多少", "收益多少", "我有", "我的"]
    for kw in keywords:
        if kw in text:
            return True
    return False


# ==================== 带重试的网络/AkShare 请求 ====================


def request_with_retry(url, params=None, retries=3, timeout=10, headers=None):
    """带重试和参数支持的HTTP GET请求"""
    if not is_safe_public_url(url):
        print("已拒绝不安全的请求地址：{}".format(url))
        return None
    import requests
    from time import sleep
    last_error = None
    for i in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout, headers=headers or {})
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_error = e
            if i < retries - 1:
                sleep(1)
    return None


def call_akshare_with_retry(func, *args, retries=2, timeout=30, **kwargs):
    """调用akshare函数并重试，失败返回 None
    timeout: 每个请求的初始超时时间（秒），超时后立即重试
    """
    from time import sleep, time as now
    for i in range(retries):
        start = now()
        try:
            return func(*args, **kwargs)
        except Exception as e:
            elapsed = now() - start
            print("AkShare调用失败（第{}次尝试，耗时{:.1f}s）：{}".format(i + 1, elapsed, e))
            if i < retries - 1:
                sleep(0.5)
    print("AkShare调用最终失败，已重试{}次".format(retries))
    return None


# ==================== 颜色/符号工具 ====================


def get_color_by_change(change):
    """根据涨跌幅返回颜色字符串"""
    if change is None:
        return "gray"
    try:
        c = float(change)
        if c > 0:
            return "red"
        elif c < 0:
            return "green"
        else:
            return "gray"
    except:
        return "gray"


def get_sign(change):
    """返回涨跌幅的符号（+/-）"""
    if change is None:
        return ""
    try:
        c = float(change)
        if c > 0:
            return "+"
        elif c < 0:
            return "-"
        else:
            return ""
    except:
        return ""


# ==================== 数值清洗与最新报告行（THS 带单位字符串/年份升序防御） ====================

_UNIT_SUFFIXES = (("%", 1.0), ("万亿", 1e12), ("亿元", 1e8), ("亿", 1e8),
                  ("万元", 1e4), ("万", 1e4), ("元", 1.0))


def clean_number(value):
    """清洗"带单位/符号的字符串数值"。

    "91.93%"→91.93、"1,741.44亿"→1.74144e11、"(3.20)"→-3.2、"3,210"→3210.0；
    数字原样转 float；解析失败返回 None（调用方按缺数据处理，勿用 0 冒充）。
    背景：THS 财务摘要接口返回带单位字符串且按年份升序，safe_float_convert
    会把 "91.93%" 转成 0，导致字段全空。
    """
    if value is None or value != value:  # None / NaN
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("，", "")
    if not s or s in ("--", "-", "nan"):
        return None
    sign = 1.0
    if s.startswith("(") and s.endswith(")"):
        sign, s = -1.0, s[1:-1]
    for suffix, mult in _UNIT_SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
            try:
                return sign * float(s) * mult
            except ValueError:
                return None
    try:
        return sign * float(s)
    except ValueError:
        return None


def latest_report_row(df, date_cols=("报告期", "报告日", "截止日期", "日期")):
    """取 DataFrame 中报告期最新的一行。

    优先按日期列解析取 max（THS 按年度为年份升序，iloc[0] 会取到最旧年度——历史 bug）；
    无可解析日期列时保守取最后一行（升序接口的最新数据在尾部）。
    """
    import pandas as pd

    if df is None or df.empty:
        return None
    for col in date_cols:
        if col in df.columns:
            dates = pd.to_datetime(df[col], errors="coerce")
            if dates.notna().any():
                return df.loc[dates.idxmax()]
    return df.iloc[-1]


if __name__ == "__main__":
    # 简单测试
    print("safe_json_parse 测试:", safe_json_parse('{"a": 1}', "测试数据"))
    print("safe_float_convert 测试:", safe_float_convert("3.14", default=0, label="pi"))
    print("safe_get 测试:", safe_get({"name": "test"}, "name", "default"))
    print("safe_get 默认值测试:", safe_get({"name": "test"}, "nonexist", "default"))
    print("safe_get_dict 测试:", safe_get_dict({"a": {"b": {"c": 123}}}, "a", "b", "c"))
    print("calc_profit_rate 测试:", calc_profit_rate(10, 100))
    print("ai_not_configured_message 测试:", ai_not_configured_message()[:20])
    print("is_portfolio_query 测试:", is_portfolio_query("我的持仓收益"))
    print("get_color_by_change 测试:", get_color_by_change(1.5))
    print("get_sign 测试:", get_sign(-2.3))