# -*- coding: utf-8 -*-
"""
诊断数据统一拉取（纯编排，无 st） - 6 引擎打包，页面与 Agent 工具共用一份。

从 pages/stock_diagnosis.py 的 _load_diag_data 搬出（规避 utils → pages 反向依赖，
且 agent_core 注册 get_stock_diagnosis 工具时不拉起重 streamlit 页面模块）。
实现依据：docs/AGENT_MVP_DESIGN.md §3 / §5 实施注意 3。
"""
import sys
from datetime import datetime, timedelta

import pandas as pd

from data.cache import cached, CACHE_FUNDAMENTALS
from data.stock_api import get_stock_info
from data.stock_fundamentals import (
    get_stock_financial_data,
    calculate_fundamental_score,
    get_advantages_and_risks,
)
from data.financial_minefield import (
    get_financial_minefield_data,
    check_minefields,
    calculate_risk_rating,
    get_risk_items,
    get_safe_items,
    get_comprehensive_advice,
)
from data.moat_analysis import get_moat_analysis_data, calculate_moat_scores
from data.stock_valuation import calculate_comprehensive_valuation
from data.financial_report import get_financial_reports, extract_core_financials

# Windows 控制台 GBK 防护：stdout 被接管时 reconfigure 可能不存在，静默跳过
if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _percentile_from_wide(df, pe_col, pb_col, years=5):
    """从带 _date 排序列的宽表算 PE/PB 近 N 年分位"""
    cutoff = datetime.now() - timedelta(days=365 * years)
    d = df[df["_date"] >= cutoff]
    if d.empty:
        return None
    ret = {"pe": None, "pb": None, "n": int(len(d)),
           "latest_date": str(d.iloc[-1]["_date"].date())}
    for key, col in (("pe", pe_col), ("pb", pb_col)):
        if col is None:
            continue
        series = pd.to_numeric(d[col], errors="coerce").dropna()
        if series.empty:
            continue
        current = float(series.iloc[-1])
        ret[key] = round(float((series <= current).mean() * 100), 1)
    return ret


def calc_valuation_percentile(stock_code, years=5):
    """计算近 N 年 PE(TTM)/PB 历史分位（页面与诊断工具共用）。

    数据源：优先 ak.stock_a_lg_indicator（乐咕乐股，个股 PE/PB 宽表）；
    akshare ≥1.17 已移除该接口（实测 1.18.64 无此函数）时回退
    ak.stock_zh_valuation_baidu（百度股市通，分指标历史序列）。
    口径：当前值百分位 = (<= 当前值的样本数 / 总数) × 100。
    返回 {"pe": float|None, "pb": float|None, "n": int, "latest_date": str}；
    全部失败 / 序列不足 → None（页面降级隐藏历史分位列，不阻断）。
    """
    try:
        import akshare as ak

        # 源 1：乐咕乐股宽表
        if hasattr(ak, "stock_a_lg_indicator"):
            df = ak.stock_a_lg_indicator(symbol=stock_code)
            if df is not None and not df.empty:
                df = df.copy()
                date_col = "trade_date" if "trade_date" in df.columns else str(df.columns[0])
                df["_date"] = pd.to_datetime(df[date_col], errors="coerce")
                df = df.dropna(subset=["_date"]).sort_values("_date")
                if not df.empty:
                    pe_col = next((c for c in ("pe_ttm", "pe", "市盈率TTM", "市盈率") if c in df.columns), None)
                    pb_col = next((c for c in ("pb", "市净率") if c in df.columns), None)
                    ret = _percentile_from_wide(df, pe_col, pb_col, years)
                    if ret is not None and (ret["pe"] is not None or ret["pb"] is not None):
                        return ret

        # 源 2：百度股市通（分指标序列，period 按年档位）
        if hasattr(ak, "stock_zh_valuation_baidu"):
            period = {1: "近一年", 3: "近三年", 5: "近五年", 10: "近十年"}.get(years, "近五年")
            pe_df = ak.stock_zh_valuation_baidu(symbol=stock_code, indicator="市盈率(TTM)", period=period)
            pb_df = ak.stock_zh_valuation_baidu(symbol=stock_code, indicator="市净率", period=period)
            ret = {"pe": None, "pb": None, "n": 0, "latest_date": "--"}
            for key, df_ in (("pe", pe_df), ("pb", pb_df)):
                if df_ is None or df_.empty:
                    continue
                d2 = df_.copy()
                d2.columns = [str(c) for c in d2.columns]
                date_col = d2.columns[0]
                val_col = next((c for c in d2.columns if "值" in c), d2.columns[-1])
                series = pd.to_numeric(d2[val_col], errors="coerce").dropna()
                if series.empty:
                    continue
                current = float(series.iloc[-1])
                ret[key] = round(float((series <= current).mean() * 100), 1)
                ret["n"] = max(ret["n"], int(len(series)))
                try:
                    ret["latest_date"] = str(pd.to_datetime(d2[date_col].iloc[-1], errors="coerce").date())
                except Exception:
                    pass
            if ret["pe"] is not None or ret["pb"] is not None:
                return ret

        return None
    except Exception as e:  # noqa: BLE001 - 页面级降级，必须吞掉一切异常
        print("历史分位计算失败（{}）：{}".format(stock_code, e))
        return None


@cached(CACHE_FUNDAMENTALS)
def build_diagnosis_payload(stock_code):
    """一次性统一拉取 6 引擎数据（行情/基本面/财报/排雷/护城河/估值+历史分位）。

    财报引擎无缓存，务必只调一次；首次约 15-40s（本函数整体 24h TTL 缓存，二次秒回）。
    单引擎失败只记录 errors，不影响其他引擎；停牌分支由 stock_info=None 承载。
    纯编排、无 st 依赖 —— 页面与 Agent get_stock_diagnosis 工具共用。
    """
    data = {
        "code": stock_code,
        "stock_info": None,
        "fundamentals": None,
        "fundamental_score": None,
        "adv_risks": None,
        "reports": None,
        "financials": None,
        "minefield": None,
        "minefield_results": None,
        "risk_rating": None,
        "risk_items": None,
        "safe_items": None,
        "minefield_advice": None,
        "moat": None,
        "moat_scores": None,
        "valuation": None,
        "percentile": None,
        "errors": [],
    }

    # 1. 行情（停牌/无行情分支入口）
    try:
        data["stock_info"] = get_stock_info(stock_code)
    except Exception as e:  # noqa: BLE001
        data["errors"].append("行情获取失败：{}".format(e))

    # 2. 基本面评分（营收/净利绝对值由财报引擎 latest 提供，两 tab 共享一份财务数据）
    try:
        data["fundamentals"] = get_stock_financial_data(stock_code)
        if data["fundamentals"]:
            data["fundamental_score"] = calculate_fundamental_score(data["fundamentals"])
            data["adv_risks"] = get_advantages_and_risks(
                data["fundamental_score"], data["fundamentals"])
    except Exception as e:  # noqa: BLE001
        data["errors"].append("基本面获取失败：{}".format(e))

    # 3. 财报三表（模块内无 @cached，仅此一次）
    try:
        data["reports"] = get_financial_reports(stock_code)
        if data["reports"] and not data["reports"].get("error"):
            data["financials"] = extract_core_financials(data["reports"])
    except Exception as e:  # noqa: BLE001
        data["errors"].append("财报获取失败：{}".format(e))

    # 4. 排雷（8 大雷区）
    try:
        data["minefield"] = get_financial_minefield_data(stock_code)
        if data["minefield"]:
            data["minefield_results"] = check_minefields(data["minefield"])
            data["risk_rating"] = calculate_risk_rating(data["minefield_results"])
            data["risk_items"] = get_risk_items(data["minefield_results"])
            data["safe_items"] = get_safe_items(data["minefield_results"])
            data["minefield_advice"] = get_comprehensive_advice(
                data["risk_rating"], data["risk_items"])
    except Exception as e:  # noqa: BLE001
        data["errors"].append("排雷获取失败：{}".format(e))

    # 5. 护城河（行业对比 20 家循环，较慢 10-25s）
    try:
        data["moat"] = get_moat_analysis_data(stock_code)
        if data["moat"]:
            data["moat_scores"] = calculate_moat_scores(data["moat"])
    except Exception as e:  # noqa: BLE001
        data["errors"].append("护城河获取失败：{}".format(e))

    # 6. 估值 + 历史分位补算（失败降级隐藏）
    try:
        data["valuation"] = calculate_comprehensive_valuation(stock_code)
    except Exception as e:  # noqa: BLE001
        data["errors"].append("估值获取失败：{}".format(e))
    data["percentile"] = calc_valuation_percentile(stock_code)

    return data