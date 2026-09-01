# -*- coding: utf-8 -*-
"""clean_number / latest_report_row 单测——锁定 THS 带单位字符串 + 年份升序两个历史 bug 的修复"""
import pandas as pd

from utils.common import clean_number, latest_report_row


def test_clean_number_percent_and_units():
    assert clean_number("91.93%") == 91.93
    assert clean_number("-4.39%") == -4.39
    assert clean_number("1,741.44亿") == 1.74144e11      # 亿 → 元
    assert clean_number("3,210") == 3210.0
    assert clean_number("12.5万元") == 125000.0
    assert clean_number("66.41元") == 66.41


def test_clean_number_paren_negative_and_junk():
    assert clean_number("(3.20)") == -3.2               # 财报负数括号写法
    assert clean_number("-0.68") == -0.68
    assert clean_number("") is None
    assert clean_number("--") is None
    assert clean_number("abc") is None
    assert clean_number(None) is None


def test_clean_number_passthrough_numbers():
    assert clean_number(12.5) == 12.5
    assert clean_number(-3) == -3.0


def test_latest_report_row_ascending_ths():
    """THS 按年度为升序（1998→最新）——iloc[0] 取到最旧年度的历史 bug 必须不复现"""
    df = pd.DataFrame({
        "报告期": ["2022-12-31", "2023-12-31", "2024-12-31"],
        "毛利率": ["91.96%", "91.93%", "91.18%"],
    })
    row = latest_report_row(df)
    assert row["报告期"] == "2024-12-31"
    assert clean_number(row["毛利率"]) == 91.18


def test_latest_report_row_unsorted_dates():
    df = pd.DataFrame({
        "报告期": ["2024-06-30", "2024-12-31", "2023-12-31"],
        "净利润": ["1", "3", "2"],
    })
    assert latest_report_row(df)["净利润"] == "3"


def test_latest_report_row_fallback_last_row():
    """无日期列时保守取最后一行（升序接口最新在尾部）"""
    df = pd.DataFrame({"指标": ["a", "b", "c"], "值": [1, 2, 3]})
    assert latest_report_row(df)["指标"] == "c"
    assert latest_report_row(None) is None
    assert latest_report_row(pd.DataFrame()) is None
