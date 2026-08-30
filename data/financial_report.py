# -*- coding: utf-8 -*-
"""
财报解读数据获取 - 使用 AkShare 获取利润表、资产负债表、现金流量表等财务数据
"""

import akshare as ak
import pandas as pd
import time
from datetime import datetime

from data.cache import cached, CACHE_FUNDAMENTALS
from utils.common import safe_float_convert


def safe_int_convert(value, default=0):
    """安全地将值转换为整数"""
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def format_number(value, unit=''):
    """格式化大数字，转为万/亿"""
    if value is None or value == 0:
        return '--'
    abs_val = abs(value)
    if abs_val >= 100000000:
        return '{:.2f}亿{}'.format(value / 100000000, unit)
    elif abs_val >= 10000:
        return '{:.2f}万{}'.format(value / 10000, unit)
    else:
        return '{:.2f}{}'.format(value, unit)


def get_financial_reports(stock_code):
    """获取完整的财报数据（利润表、资产负债表、现金流量表）
    
    返回字典包含：
    - profit_sheet: 利润表（多年数据）
    - balance_sheet: 资产负债表（多年数据）
    - cashflow_sheet: 现金流量表（多年数据）
    - financial_abstract: 财务摘要（多年数据）
    - stock_name: 股票名称
    """
    result = {
        'profit_sheet': None,
        'balance_sheet': None,
        'cashflow_sheet': None,
        'financial_abstract': None,
        'stock_name': None,
        'error': None,
    }

    # 1. 获取利润表
    try:
        time.sleep(0.3)
        df_profit = ak.stock_profit_sheet_by_report_em(symbol=stock_code)
        if df_profit is not None and not df_profit.empty:
            result['profit_sheet'] = df_profit
    except Exception as e:
        print("获取 {} 利润表失败：{}".format(stock_code, e))

    # 2. 获取资产负债表
    try:
        time.sleep(0.3)
        df_balance = ak.stock_balance_sheet_by_report_em(symbol=stock_code)
        if df_balance is not None and not df_balance.empty:
            result['balance_sheet'] = df_balance
    except Exception as e:
        print("获取 {} 资产负债表失败：{}".format(stock_code, e))

    # 3. 获取现金流量表
    try:
        time.sleep(0.3)
        df_cashflow = ak.stock_cash_flow_sheet_by_report_em(symbol=stock_code)
        if df_cashflow is not None and not df_cashflow.empty:
            result['cashflow_sheet'] = df_cashflow
    except Exception as e:
        print("获取 {} 现金流量表失败：{}".format(stock_code, e))

    # 4. 获取财务摘要（成长能力指标）
    try:
        time.sleep(0.3)
        df_abstract = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
        if df_abstract is not None and not df_abstract.empty:
            result['financial_abstract'] = df_abstract
    except Exception as e:
        print("获取 {} 财务摘要失败：{}".format(stock_code, e))

    # 5. 获取股票名称
    try:
        from data.stock_api import get_stock_info
        stock_info = get_stock_info(stock_code)
        if stock_info:
            result['stock_name'] = stock_info.get('name', '')
    except Exception as e:
        print("获取 {} 基本信息失败：{}".format(stock_code, e))

    if all(v is None for v in [result['profit_sheet'], result['balance_sheet'], 
                                result['cashflow_sheet'], result['financial_abstract']]):
        result['error'] = "未获取到财报数据，请检查股票代码是否正确"

    return result


def extract_core_financials(reports):
    """从财报数据中提取核心财务指标
    
    返回字典包含最新一期和近几年的核心数据
    """
    result = {
        'latest': {},       # 最新一期数据
        'history': {},      # 历史数据（用于趋势分析）
        'growth_rates': {}, # 增长率数据
    }

    profit_sheet = reports.get('profit_sheet')
    balance_sheet = reports.get('balance_sheet')
    cashflow_sheet = reports.get('cashflow_sheet')
    financial_abstract = reports.get('financial_abstract')

    # ===== 从利润表提取数据 =====
    if profit_sheet is not None and not profit_sheet.empty:
        # 取最新一期（第一行）
        latest_profit = profit_sheet.iloc[0]
        
        # 营业收入
        revenue = safe_float_convert(latest_profit.get('营业收入'))
        result['latest']['revenue'] = revenue if revenue else None
        
        # 营业成本
        cost = safe_float_convert(latest_profit.get('营业成本'))
        result['latest']['cost'] = cost if cost else None
        
        # 净利润
        net_profit = safe_float_convert(latest_profit.get('净利润'))
        result['latest']['net_profit'] = net_profit if net_profit else None
        
        # 扣非净利润
        deduct_profit = safe_float_convert(latest_profit.get('扣除非经常性损益后的净利润'))
        result['latest']['deduct_profit'] = deduct_profit if deduct_profit else None
        
        # 营业利润
        operating_profit = safe_float_convert(latest_profit.get('营业利润'))
        result['latest']['operating_profit'] = operating_profit if operating_profit else None
        
        # 利润总额
        total_profit = safe_float_convert(latest_profit.get('利润总额'))
        result['latest']['total_profit'] = total_profit if total_profit else None
        
        # 基本每股收益
        eps = safe_float_convert(latest_profit.get('基本每股收益'))
        result['latest']['eps'] = eps if eps else None
        
        # 计算毛利率
        if revenue and revenue != 0 and cost:
            result['latest']['gross_margin'] = round((revenue - cost) / revenue * 100, 2)
        
        # 计算净利率
        if revenue and revenue != 0 and net_profit:
            result['latest']['net_margin'] = round(net_profit / revenue * 100, 2)
        
        # 提取历史数据（近5年）
        history_revenue = []
        history_net_profit = []
        history_deduct_profit = []
        history_gross_margin = []
        history_net_margin = []
        history_eps = []
        history_labels = []
        
        for i in range(min(len(profit_sheet), 5)):
            row = profit_sheet.iloc[i]
            rev = safe_float_convert(row.get('营业收入'))
            np_ = safe_float_convert(row.get('净利润'))
            dp = safe_float_convert(row.get('扣除非经常性损益后的净利润'))
            cst = safe_float_convert(row.get('营业成本'))
            e = safe_float_convert(row.get('基本每股收益'))
            
            # 获取报告期标签
            report_date = str(row.get('报告期', row.get('REPORT_DATE', '')))
            if not report_date or report_date == '0':
                report_date = "第{}期".format(i + 1)
            
            history_labels.append(report_date)
            history_revenue.append(rev if rev else 0)
            history_net_profit.append(np_ if np_ else 0)
            history_deduct_profit.append(dp if dp else 0)
            
            if rev and rev != 0 and cst:
                history_gross_margin.append(round((rev - cst) / rev * 100, 2))
            else:
                history_gross_margin.append(None)
            
            if rev and rev != 0 and np_:
                history_net_margin.append(round(np_ / rev * 100, 2))
            else:
                history_net_margin.append(None)
            
            history_eps.append(e if e else None)
        
        result['history']['labels'] = history_labels
        result['history']['revenue'] = history_revenue
        result['history']['net_profit'] = history_net_profit
        result['history']['deduct_profit'] = history_deduct_profit
        result['history']['gross_margin'] = history_gross_margin
        result['history']['net_margin'] = history_net_margin
        result['history']['eps'] = history_eps
        
        # 计算同比增长率
        if len(history_revenue) >= 2:
            rev_growth = []
            np_growth = []
            for i in range(1, len(history_revenue)):
                if history_revenue[i-1] and history_revenue[i-1] != 0:
                    rg = round((history_revenue[i] - history_revenue[i-1]) / abs(history_revenue[i-1]) * 100, 2)
                else:
                    rg = None
                rev_growth.append(rg)
                
                if history_net_profit[i-1] and history_net_profit[i-1] != 0:
                    ng = round((history_net_profit[i] - history_net_profit[i-1]) / abs(history_net_profit[i-1]) * 100, 2)
                else:
                    ng = None
                np_growth.append(ng)
            
            result['growth_rates']['revenue_growth'] = rev_growth
            result['growth_rates']['net_profit_growth'] = np_growth

    # ===== 从资产负债表提取数据 =====
    if balance_sheet is not None and not balance_sheet.empty:
        latest_bs = balance_sheet.iloc[0]
        
        # 资产总计
        total_assets = safe_float_convert(latest_bs.get('资产总计'))
        result['latest']['total_assets'] = total_assets if total_assets else None
        
        # 负债合计
        total_liab = safe_float_convert(latest_bs.get('负债合计'))
        result['latest']['total_liab'] = total_liab if total_liab else None
        
        # 所有者权益
        equity = safe_float_convert(latest_bs.get('所有者权益合计'))
        result['latest']['equity'] = equity if equity else None
        
        # 归属母公司股东权益
        parent_equity = safe_float_convert(latest_bs.get('归属于母公司股东权益合计'))
        result['latest']['parent_equity'] = parent_equity if parent_equity else None
        
        # 资产负债率
        if total_assets and total_assets != 0 and total_liab:
            result['latest']['debt_ratio'] = round(total_liab / total_assets * 100, 2)
        
        # 每股净资产
        if parent_equity:
            result['latest']['bps'] = parent_equity  # 需要除以总股本，先存着
        
        # 短期借款
        short_loan = safe_float_convert(latest_bs.get('短期借款'))
        result['latest']['short_loan'] = short_loan if short_loan else None
        
        # 长期借款
        long_loan = safe_float_convert(latest_bs.get('长期借款'))
        result['latest']['long_loan'] = long_loan if long_loan else None
        
        # 应付债券
        bonds_payable = safe_float_convert(latest_bs.get('应付债券'))
        result['latest']['bonds_payable'] = bonds_payable if bonds_payable else None
        
        # 有息负债
        interest_debt = (short_loan or 0) + (long_loan or 0) + (bonds_payable or 0)
        result['latest']['interest_debt'] = interest_debt if interest_debt else None
        
        # 有息负债率
        if total_assets and total_assets != 0 and interest_debt:
            result['latest']['interest_debt_ratio'] = round(interest_debt / total_assets * 100, 2)
        
        # 应收账款
        accounts_receivable = safe_float_convert(latest_bs.get('应收账款'))
        result['latest']['accounts_receivable'] = accounts_receivable if accounts_receivable else None
        
        # 存货
        inventory = safe_float_convert(latest_bs.get('存货'))
        result['latest']['inventory'] = inventory if inventory else None
        
        # 货币资金
        cash_equivalents = safe_float_convert(latest_bs.get('货币资金'))
        result['latest']['cash_equivalents'] = cash_equivalents if cash_equivalents else None
        
        # 历史资产负债率
        history_debt_ratio = []
        for i in range(min(len(balance_sheet), 5)):
            row = balance_sheet.iloc[i]
            ta = safe_float_convert(row.get('资产总计'))
            tl = safe_float_convert(row.get('负债合计'))
            if ta and ta != 0 and tl:
                history_debt_ratio.append(round(tl / ta * 100, 2))
            else:
                history_debt_ratio.append(None)
        result['history']['debt_ratio'] = history_debt_ratio

    # ===== 从现金流量表提取数据 =====
    if cashflow_sheet is not None and not cashflow_sheet.empty:
        latest_cf = cashflow_sheet.iloc[0]
        
        # 经营活动现金流净额
        operating_cf = safe_float_convert(latest_cf.get('经营活动产生的现金流量净额'))
        result['latest']['operating_cf'] = operating_cf if operating_cf else None
        
        # 投资活动现金流净额
        investing_cf = safe_float_convert(latest_cf.get('投资活动产生的现金流量净额'))
        result['latest']['investing_cf'] = investing_cf if investing_cf else None
        
        # 筹资活动现金流净额
        financing_cf = safe_float_convert(latest_cf.get('筹资活动产生的现金流量净额'))
        result['latest']['financing_cf'] = financing_cf if financing_cf else None
        
        # 现金净增加额
        net_cash_change = safe_float_convert(latest_cf.get('现金及现金等价物净增加额'))
        result['latest']['net_cash_change'] = net_cash_change if net_cash_change else None
        
        # 经营现金流/净利润
        net_profit = result['latest'].get('net_profit')
        if operating_cf and net_profit and net_profit != 0:
            result['latest']['cf_to_profit'] = round(operating_cf / net_profit * 100, 2)
        
        # 历史经营现金流
        history_operating_cf = []
        for i in range(min(len(cashflow_sheet), 5)):
            row = cashflow_sheet.iloc[i]
            ocf = safe_float_convert(row.get('经营活动产生的现金流量净额'))
            history_operating_cf.append(ocf if ocf else 0)
        result['history']['operating_cf'] = history_operating_cf

    # ===== 从财务摘要提取成长数据 =====
    if financial_abstract is not None and not financial_abstract.empty:
        # 提取营收增长率和净利润增长率
        for col in ['营业收入增长率', '营收增长率', '营业总收入同比增长率']:
            if col in financial_abstract.columns:
                vals = []
                for i in range(min(len(financial_abstract), 5)):
                    v = safe_float_convert(financial_abstract.iloc[i].get(col))
                    if v != 0:
                        vals.append(v)
                if vals:
                    result['latest']['revenue_growth_rate'] = vals[0]
                    result['growth_rates']['abstract_revenue_growth'] = vals
                break
        
        for col in ['净利润增长率', '归属净利润增长率', '归属于母公司所有者的净利润同比增长率']:
            if col in financial_abstract.columns:
                vals = []
                for i in range(min(len(financial_abstract), 5)):
                    v = safe_float_convert(financial_abstract.iloc[i].get(col))
                    if v != 0:
                        vals.append(v)
                if vals:
                    result['latest']['profit_growth_rate'] = vals[0]
                    result['growth_rates']['abstract_profit_growth'] = vals
                break
        
        # 提取ROE
        for col in ['净资产收益率', 'ROE', '加权净资产收益率']:
            if col in financial_abstract.columns:
                vals = []
                for i in range(min(len(financial_abstract), 5)):
                    v = safe_float_convert(financial_abstract.iloc[i].get(col))
                    if v != 0:
                        vals.append(v)
                if vals:
                    result['latest']['roe'] = vals[0]
                    result['history']['roe'] = vals
                break
        
        # 提取毛利率
        for col in ['毛利率', '销售毛利率']:
            if col in financial_abstract.columns:
                vals = []
                for i in range(min(len(financial_abstract), 5)):
                    v = safe_float_convert(financial_abstract.iloc[i].get(col))
                    if v != 0:
                        vals.append(v)
                if vals:
                    if result['latest'].get('gross_margin') is None:
                        result['latest']['gross_margin'] = vals[0]
                    result['history']['gross_margin_abstract'] = vals
                break
        
        # 提取净利率
        for col in ['净利率', '销售净利率']:
            if col in financial_abstract.columns:
                vals = []
                for i in range(min(len(financial_abstract), 5)):
                    v = safe_float_convert(financial_abstract.iloc[i].get(col))
                    if v != 0:
                        vals.append(v)
                if vals:
                    if result['latest'].get('net_margin') is None:
                        result['latest']['net_margin'] = vals[0]
                    result['history']['net_margin_abstract'] = vals
                break

    return result


def analyze_growth_trend(growth_rates):
    """分析成长趋势
    
    返回：
    - trend: 加速增长/稳定增长/减速增长/波动/负增长
    - description: 文字描述
    """
    rev_growth = growth_rates.get('revenue_growth', [])
    np_growth = growth_rates.get('net_profit_growth', [])
    
    if not rev_growth and not np_growth:
        return '数据不足', '暂无足够的连续数据来判断成长趋势'
    
    analysis = []
    
    # 分析营收增长趋势
    if rev_growth and len(rev_growth) >= 2:
        recent = rev_growth[-1] if rev_growth[-1] is not None else 0
        prev = rev_growth[-2] if rev_growth[-2] is not None else 0
        
        if recent > 0 and prev > 0:
            if recent > prev * 1.2:
                analysis.append("营收加速增长（从{:.1f}%→{:.1f}%）".format(prev, recent))
            elif recent > prev * 0.8:
                analysis.append("营收稳定增长（{:.1f}%~{:.1f}%）".format(prev, recent))
            else:
                analysis.append("营收减速增长（从{:.1f}%→{:.1f}%）".format(prev, recent))
        elif recent > 0 and prev <= 0:
            analysis.append("营收扭亏为盈（从{:.1f}%→{:.1f}%）".format(prev, recent))
        elif recent <= 0 and prev > 0:
            analysis.append("营收由增转降（从{:.1f}%→{:.1f}%）".format(prev, recent))
        else:
            analysis.append("营收持续负增长（{:.1f}%~{:.1f}%）".format(prev, recent))
    
    # 分析净利润增长趋势
    if np_growth and len(np_growth) >= 2:
        recent = np_growth[-1] if np_growth[-1] is not None else 0
        prev = np_growth[-2] if np_growth[-2] is not None else 0
        
        if recent > 0 and prev > 0:
            if recent > prev * 1.2:
                analysis.append("净利润加速增长（从{:.1f}%→{:.1f}%）".format(prev, recent))
            elif recent > prev * 0.8:
                analysis.append("净利润稳定增长（{:.1f}%~{:.1f}%）".format(prev, recent))
            else:
                analysis.append("净利润减速增长（从{:.1f}%→{:.1f}%）".format(prev, recent))
        elif recent > 0 and prev <= 0:
            analysis.append("净利润扭亏为盈（从{:.1f}%→{:.1f}%）".format(prev, recent))
        elif recent <= 0 and prev > 0:
            analysis.append("净利润由增转降（从{:.1f}%→{:.1f}%）".format(prev, recent))
        else:
            analysis.append("净利润持续负增长（{:.1f}%~{:.1f}%）".format(prev, recent))
    
    if not analysis:
        return '数据不足', '暂无足够的连续数据来判断成长趋势'
    
    # 综合判断
    all_positive = all(
        (g is not None and g > 0) for g in (rev_growth + np_growth) if g is not None
    )
    all_negative = all(
        (g is not None and g < 0) for g in (rev_growth + np_growth) if g is not None
    )
    
    if all_positive:
        trend = '正向增长'
    elif all_negative:
        trend = '持续下滑'
    else:
        trend = '波动较大'
    
    return trend, '；'.join(analysis)


def analyze_profit_trend(history):
    """分析盈利趋势
    
    返回：
    - trend: 提升/稳定/下降/波动
    - description: 文字描述
    """
    gross_margins = history.get('gross_margin', [])
    net_margins = history.get('net_margin', [])
    roe = history.get('roe', [])
    
    if not gross_margins and not net_margins:
        return '数据不足', '暂无足够的连续数据来判断盈利趋势'
    
    analysis = []
    
    # 分析毛利率趋势
    valid_gm = [g for g in gross_margins if g is not None]
    if len(valid_gm) >= 2:
        if valid_gm[-1] > valid_gm[0] * 1.05:
            analysis.append("毛利率提升（从{:.1f}%→{:.1f}%）".format(valid_gm[0], valid_gm[-1]))
        elif valid_gm[-1] < valid_gm[0] * 0.95:
            analysis.append("毛利率下降（从{:.1f}%→{:.1f}%）".format(valid_gm[0], valid_gm[-1]))
        else:
            analysis.append("毛利率基本稳定（{:.1f}%~{:.1f}%）".format(valid_gm[0], valid_gm[-1]))
    
    # 分析净利率趋势
    valid_nm = [n for n in net_margins if n is not None]
    if len(valid_nm) >= 2:
        if valid_nm[-1] > valid_nm[0] * 1.05:
            analysis.append("净利率提升（从{:.1f}%→{:.1f}%）".format(valid_nm[0], valid_nm[-1]))
        elif valid_nm[-1] < valid_nm[0] * 0.95:
            analysis.append("净利率下降（从{:.1f}%→{:.1f}%）".format(valid_nm[0], valid_nm[-1]))
        else:
            analysis.append("净利率基本稳定（{:.1f}%~{:.1f}%）".format(valid_nm[0], valid_nm[-1]))
    
    # 分析ROE趋势
    valid_roe = [r for r in roe if r is not None]
    if len(valid_roe) >= 2:
        if valid_roe[-1] > valid_roe[0] * 1.05:
            analysis.append("ROE提升（从{:.1f}%→{:.1f}%）".format(valid_roe[0], valid_roe[-1]))
        elif valid_roe[-1] < valid_roe[0] * 0.95:
            analysis.append("ROE下降（从{:.1f}%→{:.1f}%）".format(valid_roe[0], valid_roe[-1]))
        else:
            analysis.append("ROE基本稳定（{:.1f}%~{:.1f}%）".format(valid_roe[0], valid_roe[-1]))
    
    if not analysis:
        return '数据不足', '暂无足够的连续数据来判断盈利趋势'
    
    # 综合判断
    improving = sum(1 for a in analysis if '提升' in a)
    declining = sum(1 for a in analysis if '下降' in a)
    
    if improving >= 2:
        trend = '盈利能力提升'
    elif declining >= 2:
        trend = '盈利能力下降'
    elif improving >= 1 and declining >= 1:
        trend = '盈利能力波动'
    else:
        trend = '盈利能力稳定'
    
    return trend, '；'.join(analysis)


def analyze_financial_health(financials):
    """分析财务健康状况
    
    返回：
    - health: 健康/一般/风险
    - details: 详细分析列表
    """
    details = []
    risks = []
    
    latest = financials.get('latest', {})
    history = financials.get('history', {})
    
    # 1. 资产负债率分析
    debt_ratio = latest.get('debt_ratio')
    if debt_ratio is not None:
        if debt_ratio < 30:
            details.append(("资产负债率", debt_ratio, "较低，财务杠杆保守", "safe"))
        elif debt_ratio < 50:
            details.append(("资产负债率", debt_ratio, "适中，财务结构合理", "normal"))
        elif debt_ratio < 70:
            details.append(("资产负债率", debt_ratio, "偏高，需关注偿债能力", "warning"))
        else:
            details.append(("资产负债率", debt_ratio, "过高，财务风险较大", "danger"))
            risks.append("资产负债率过高（{:.1f}%）".format(debt_ratio))
    
    # 2. 有息负债率分析
    interest_debt_ratio = latest.get('interest_debt_ratio')
    if interest_debt_ratio is not None:
        if interest_debt_ratio < 10:
            details.append(("有息负债率", interest_debt_ratio, "很低，几乎没有有息负债", "safe"))
        elif interest_debt_ratio < 30:
            details.append(("有息负债率", interest_debt_ratio, "适中，债务负担可控", "normal"))
        elif interest_debt_ratio < 50:
            details.append(("有息负债率", interest_debt_ratio, "偏高，利息支出压力较大", "warning"))
        else:
            details.append(("有息负债率", interest_debt_ratio, "过高，债务风险较大", "danger"))
            risks.append("有息负债率过高（{:.1f}%）".format(interest_debt_ratio))
    
    # 3. 现金流分析
    operating_cf = latest.get('operating_cf')
    net_profit = latest.get('net_profit')
    cf_to_profit = latest.get('cf_to_profit')
    
    if cf_to_profit is not None:
        if cf_to_profit > 100:
            details.append(("经营现金流/净利润", cf_to_profit, "现金流充裕，盈利质量好", "safe"))
        elif cf_to_profit > 50:
            details.append(("经营现金流/净利润", cf_to_profit, "现金流正常，盈利质量一般", "normal"))
        elif cf_to_profit > 0:
            details.append(("经营现金流/净利润", cf_to_profit, "现金流偏弱，盈利质量需关注", "warning"))
        else:
            details.append(("经营现金流/净利润", cf_to_profit, "现金流为负，盈利质量差", "danger"))
            risks.append("经营现金流为负，盈利质量差")
    elif operating_cf is not None and net_profit is not None:
        if operating_cf > 0 and net_profit > 0:
            details.append(("经营现金流", operating_cf, "经营现金流为正，但无法计算比率", "normal"))
        elif operating_cf < 0 and net_profit > 0:
            details.append(("经营现金流", operating_cf, "经营现金流为负，利润含金量低", "warning"))
            risks.append("经营现金流为负，利润含金量低")
    
    # 4. 应收账款分析
    accounts_receivable = latest.get('accounts_receivable')
    revenue = latest.get('revenue')
    if accounts_receivable is not None and revenue is not None and revenue != 0:
        ar_ratio = accounts_receivable / revenue * 100
        if ar_ratio < 10:
            details.append(("应收账款/营收", ar_ratio, "应收账款占比低，回款能力强", "safe"))
        elif ar_ratio < 30:
            details.append(("应收账款/营收", ar_ratio, "应收账款占比适中", "normal"))
        elif ar_ratio < 50:
            details.append(("应收账款/营收", ar_ratio, "应收账款占比较高，回款风险增加", "warning"))
        else:
            details.append(("应收账款/营收", ar_ratio, "应收账款占比过高，坏账风险大", "danger"))
            risks.append("应收账款占比过高（{:.1f}%）".format(ar_ratio))
    
    # 5. 存货分析
    inventory = latest.get('inventory')
    if inventory is not None and revenue is not None and revenue != 0:
        inv_ratio = inventory / revenue * 100
        if inv_ratio < 15:
            details.append(("存货/营收", inv_ratio, "存货占比低，周转良好", "safe"))
        elif inv_ratio < 30:
            details.append(("存货/营收", inv_ratio, "存货占比适中", "normal"))
        elif inv_ratio < 50:
            details.append(("存货/营收", inv_ratio, "存货占比较高，需关注周转", "warning"))
        else:
            details.append(("存货/营收", inv_ratio, "存货占比过高，跌价风险大", "danger"))
            risks.append("存货占比过高（{:.1f}%）".format(inv_ratio))
    
    # 综合判断
    danger_count = sum(1 for d in details if d[3] == 'danger')
    warning_count = sum(1 for d in details if d[3] == 'warning')
    
    if danger_count >= 2:
        health = '风险较高'
    elif danger_count >= 1 or warning_count >= 3:
        health = '存在风险'
    elif warning_count >= 1:
        health = '一般'
    else:
        health = '健康'
    
    return health, details, risks


def build_financial_summary_text(financials):
    """构建财报摘要文本（用于AI分析）"""
    latest = financials.get('latest', {})
    history = financials.get('history', {})
    growth_rates = financials.get('growth_rates', {})
    
    lines = []
    lines.append("=== 最新一期财报核心数据 ===")
    
    revenue = latest.get('revenue')
    lines.append("营业收入：{}".format(format_number(revenue, '元') if revenue else '数据缺失'))
    
    net_profit = latest.get('net_profit')
    lines.append("净利润：{}".format(format_number(net_profit, '元') if net_profit else '数据缺失'))
    
    deduct_profit = latest.get('deduct_profit')
    lines.append("扣非净利润：{}".format(format_number(deduct_profit, '元') if deduct_profit else '数据缺失'))
    
    gross_margin = latest.get('gross_margin')
    lines.append("毛利率：{:.2f}%".format(gross_margin) if gross_margin else "毛利率：数据缺失")
    
    net_margin = latest.get('net_margin')
    lines.append("净利率：{:.2f}%".format(net_margin) if net_margin else "净利率：数据缺失")
    
    roe = latest.get('roe')
    lines.append("ROE：{:.2f}%".format(roe) if roe else "ROE：数据缺失")
    
    eps = latest.get('eps')
    lines.append("每股收益：{:.4f}元".format(eps) if eps else "每股收益：数据缺失")
    
    debt_ratio = latest.get('debt_ratio')
    lines.append("资产负债率：{:.2f}%".format(debt_ratio) if debt_ratio else "资产负债率：数据缺失")
    
    operating_cf = latest.get('operating_cf')
    lines.append("经营现金流：{}".format(format_number(operating_cf, '元') if operating_cf else '数据缺失'))
    
    # 增长率
    rev_growth = growth_rates.get('revenue_growth', [])
    if rev_growth:
        lines.append("\n=== 营收同比增长率趋势 ===")
        for i, g in enumerate(rev_growth):
            if g is not None:
                lines.append("第{}期：{:.2f}%".format(i+1, g))
    
    np_growth = growth_rates.get('net_profit_growth', [])
    if np_growth:
        lines.append("\n=== 净利润同比增长率趋势 ===")
        for i, g in enumerate(np_growth):
            if g is not None:
                lines.append("第{}期：{:.2f}%".format(i+1, g))
    
    # 历史数据
    history_revenue = history.get('revenue', [])
    if history_revenue:
        lines.append("\n=== 近5年营业收入趋势 ===")
        for i, v in enumerate(history_revenue):
            label = history.get('labels', [''] * len(history_revenue))[i] if history.get('labels') else "第{}期".format(i+1)
            lines.append("{}：{}".format(label, format_number(v, '元') if v else '数据缺失'))
    
    return "\n".join(lines)
