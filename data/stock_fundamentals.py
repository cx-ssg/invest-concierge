# -*- coding: utf-8 -*-
"""
股票基本面数据获取 - 使用 AkShare 获取财务数据用于基本面评分
"""

import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
import time

from data.stock_api import get_stock_info
from data.cache import cached, CACHE_FUNDAMENTALS


import utils.common
safe_float_convert = getattr(utils.common, "safe_float_convert", lambda x, default=0.0: default)
call_akshare_with_retry = getattr(utils.common, "call_akshare_with_retry", lambda func, **kwargs: None)
clean_number = getattr(utils.common, "clean_number", None)
latest_report_row = getattr(utils.common, "latest_report_row", None)


@cached(CACHE_FUNDAMENTALS)
def get_stock_financial_data(stock_code):
    """获取股票基本面财务数据（综合多个数据源）
    
    返回字典包含：
    - roe: 净资产收益率(%)
    - gross_margin: 毛利率(%)
    - net_margin: 净利率(%)
    - revenue_growth: 营收增长率(%)
    - profit_growth: 净利润增长率(%)
    - debt_ratio: 资产负债率(%)
    - cashflow_ratio: 经营现金流/净利润(%)
    - interest_debt_ratio: 有息负债率(%)
    - pe: 市盈率
    - pb: 市净率
    - dividend_rate: 股息率(%)
    - market_cap: 总市值(亿)
    - industry: 所属行业
    - industry_rank: 行业排名
    """
    result = {
        'roe': None,
        'gross_margin': None,
        'net_margin': None,
        'revenue_growth': None,
        'profit_growth': None,
        'debt_ratio': None,
        'cashflow_ratio': None,
        'interest_debt_ratio': None,
        'pe': None,
        'pb': None,
        'dividend_rate': None,
        'market_cap': None,
        'industry': None,
        'industry_rank': None,
        'stock_name': None,
    }

    try:
        # 1. 先获取实时行情（获取 PE、PB、市值等）
        stock_info = get_stock_info(stock_code)
        if stock_info:
            result['stock_name'] = stock_info.get('name', '')
            result['pe'] = stock_info.get('pe', 0)
            result['pb'] = stock_info.get('pb', 0)
            market_cap = stock_info.get('total_market_cap', 0)
            result['market_cap'] = market_cap / 100000000 if market_cap else 0  # 转为亿
    except Exception as e:
        print("获取 {} 实时行情失败：{}".format(stock_code, e))

    # 2. 获取财务指标（使用 ak.stock_financial_abstract_ths）
    try:
        df_fin = call_akshare_with_retry(ak.stock_financial_abstract_ths, symbol=stock_code, indicator="按年度")
        if df_fin is not None and not df_fin.empty:
            # 取最新一期（THS 按年度为年份升序，iloc[0] 会取到最旧年度；
            # 值带单位字符串如 "91.93%"、"1,741.44亿"，safe_float_convert 会转成 0）
            latest = latest_report_row(df_fin)

            # ROE
            for col in ['净资产收益率', 'ROE', '加权净资产收益率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['roe'] = val
                        break

            # 毛利率
            for col in ['毛利率', '销售毛利率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['gross_margin'] = val
                        break

            # 净利率
            for col in ['净利率', '销售净利率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['net_margin'] = val
                        break

            # 资产负债率
            for col in ['资产负债率', '资产负债比率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['debt_ratio'] = val
                        break

            # 营收增长率
            for col in ['营业收入增长率', '营收增长率', '营业总收入同比增长率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['revenue_growth'] = val
                        break

            # 净利润增长率
            for col in ['净利润增长率', '归属净利润增长率', '归属于母公司所有者的净利润同比增长率']:
                if col in df_fin.columns:
                    val = clean_number(latest.get(col))
                    if val:
                        result['profit_growth'] = val
                        break
    except Exception as e:
        print("获取 {} 财务指标失败：{}".format(stock_code, e))

    # 3. 获取利润表数据（补充毛利率、净利率）
    if result['gross_margin'] is None or result['net_margin'] is None:
        try:
            time.sleep(0.3)
            df_profit = call_akshare_with_retry(ak.stock_profit_sheet_by_report_em, symbol=stock_code)
            if df_profit is not None and not df_profit.empty:
                latest = df_profit.iloc[0]
                
                if result['gross_margin'] is None:
                    revenue = safe_float_convert(latest.get('营业收入'))
                    cost = safe_float_convert(latest.get('营业成本'))
                    if revenue and revenue != 0:
                        result['gross_margin'] = round((revenue - cost) / revenue * 100, 2)
                
                if result['net_margin'] is None:
                    net_profit = safe_float_convert(latest.get('净利润'))
                    revenue = safe_float_convert(latest.get('营业收入'))
                    if net_profit and revenue and revenue != 0:
                        result['net_margin'] = round(net_profit / revenue * 100, 2)
        except Exception as e:
            print("获取 {} 利润表失败：{}".format(stock_code, e))

    # 4. 获取现金流量表（经营现金流/净利润）
    try:
        time.sleep(0.3)
        df_cf = call_akshare_with_retry(ak.stock_cash_flow_sheet_by_report_em, symbol=stock_code)
        if df_cf is not None and not df_cf.empty:
            latest = df_cf.iloc[0]
            operating_cf = safe_float_convert(latest.get('经营活动产生的现金流量净额'))
            net_profit_cf = safe_float_convert(latest.get('净利润'))
            if operating_cf and net_profit_cf and net_profit_cf != 0:
                result['cashflow_ratio'] = round(operating_cf / net_profit_cf * 100, 2)
    except Exception as e:
        print("获取 {} 现金流量表失败：{}".format(stock_code, e))

    # 5. 获取资产负债表（有息负债率）
    if result['interest_debt_ratio'] is None:
        try:
            time.sleep(0.3)
            df_bs = call_akshare_with_retry(ak.stock_balance_sheet_by_report_em, symbol=stock_code)
            if df_bs is not None and not df_bs.empty:
                latest = df_bs.iloc[0]
                total_liab = safe_float_convert(latest.get('负债合计'))
                total_assets = safe_float_convert(latest.get('资产总计'))
                if total_liab and total_assets and total_assets != 0:
                    if result['debt_ratio'] is None:
                        result['debt_ratio'] = round(total_liab / total_assets * 100, 2)
                
                # 有息负债率 = (短期借款 + 长期借款 + 应付债券) / 总资产
                short_loan = safe_float_convert(latest.get('短期借款'))
                long_loan = safe_float_convert(latest.get('长期借款'))
                bonds = safe_float_convert(latest.get('应付债券'))
                interest_debt = short_loan + long_loan + bonds
                if interest_debt and total_assets and total_assets != 0:
                    result['interest_debt_ratio'] = round(interest_debt / total_assets * 100, 2)
        except Exception as e:
            print("获取 {} 资产负债表失败：{}".format(stock_code, e))

    # 6. 获取股息率
    try:
        time.sleep(0.3)
        df_div = call_akshare_with_retry(ak.stock_dividents_cninfo, symbol=stock_code)
        if df_div is not None and not df_div.empty:
            # 取最近一期股息率
            for col in ['股息率', '股利支付率']:
                if col in df_div.columns:
                    val = safe_float_convert(df_div.iloc[0].get(col))
                    if val != 0:
                        result['dividend_rate'] = val
                        break
    except Exception as e:
        print("获取 {} 股息率失败：{}".format(stock_code, e))

    # 如果股息率还没获取到，尝试用另一种方式
    if result['dividend_rate'] is None or result['dividend_rate'] == 0:
        try:
            time.sleep(0.3)
            df_yield = call_akshare_with_retry(ak.stock_a_lg_indicator, symbol=stock_code)
            if df_yield is not None and not df_yield.empty:
                latest = df_yield.iloc[0]
                for col in ['股息率', 'dividend_yield']:
                    if col in df_yield.columns:
                        val = safe_float_convert(latest.get(col))
                        if val != 0:
                            result['dividend_rate'] = val
                            break
        except Exception as e:
            print("获取 {} 股息率(备选)失败：{}".format(stock_code, e))

    # 7. 获取行业信息和排名
    try:
        time.sleep(0.3)
        df_industry = call_akshare_with_retry(ak.stock_board_industry_name_em)
        if df_industry is not None and not df_industry.empty:
            # 获取个股所属行业
            try:
                time.sleep(0.3)
                df_individual = call_akshare_with_retry(ak.stock_individual_info_em, symbol=stock_code)
                if df_individual is not None and not df_individual.empty:
                    for _, row in df_individual.iterrows():
                        if '行业' in str(row.iloc[0]):
                            result['industry'] = str(row.iloc[1])
                            break
            except Exception as e:
                print("获取 {} 行业信息失败：{}".format(stock_code, e))
    except Exception as e:
        print("获取行业板块数据失败：{}".format(e))

    # 8. 获取营收和净利润复合增长率（近3年）
    try:
        time.sleep(0.3)
        df_growth = call_akshare_with_retry(ak.stock_growth_ability_em, symbol=stock_code)
        if df_growth is not None and not df_growth.empty:
            for _, row in df_growth.iterrows():
                indicator = str(row.iloc[0])
                if '营业收入同比增长率' in indicator:
                    # 取最近3年的平均值
                    vals = []
                    for col in df_growth.columns[1:4]:  # 近3年
                        v = safe_float_convert(row.get(col))
                        if v != 0:
                            vals.append(v)
                    if vals:
                        result['revenue_growth'] = round(sum(vals) / len(vals), 2)
                elif '净利润同比增长率' in indicator:
                    vals = []
                    for col in df_growth.columns[1:4]:  # 近3年
                        v = safe_float_convert(row.get(col))
                        if v != 0:
                            vals.append(v)
                    if vals:
                        result['profit_growth'] = round(sum(vals) / len(vals), 2)
    except Exception as e:
        print("获取 {} 成长能力数据失败：{}".format(stock_code, e))

    return result


def calculate_fundamental_score(financial_data):
    """根据基本面数据计算综合评分（满分100分）
    
    评分维度：
    1. 盈利能力（25分）：ROE(10) + 毛利率(8) + 净利率(7)
    2. 成长性（25分）：营收增长率(12) + 净利润增长率(13)
    3. 财务健康（20分）：资产负债率(8) + 现金流/净利润(7) + 有息负债率(5)
    4. 估值水平（20分）：PE(10) + PB(5) + 股息率(5)
    5. 行业地位（10分）：市值排名(5) + 市值规模(5)
    """
    scores = {}
    details = {}

    # ===== 1. 盈利能力（25分） =====
    profit_score = 0
    profit_details = []

    # ROE（10分）
    roe = financial_data.get('roe')
    if roe is not None and roe > 0:
        if roe > 20:
            roe_score = 10
            roe_desc = "优秀（>20%）"
        elif roe > 15:
            roe_score = 8
            roe_desc = "良好（15-20%）"
        elif roe > 10:
            roe_score = 6
            roe_desc = "中等（10-15%）"
        elif roe > 5:
            roe_score = 3
            roe_desc = "一般（5-10%）"
        else:
            roe_score = 0
            roe_desc = "较差（<5%）"
    else:
        roe_score = 0
        roe_desc = "数据缺失"
    profit_score += roe_score
    profit_details.append(("ROE", roe, roe_score, roe_desc))

    # 毛利率（8分）
    gross_margin = financial_data.get('gross_margin')
    if gross_margin is not None and gross_margin > 0:
        if gross_margin > 50:
            gm_score = 8
            gm_desc = "优秀（>50%）"
        elif gross_margin > 30:
            gm_score = 6
            gm_desc = "良好（30-50%）"
        elif gross_margin > 20:
            gm_score = 4
            gm_desc = "中等（20-30%）"
        elif gross_margin > 10:
            gm_score = 2
            gm_desc = "一般（10-20%）"
        else:
            gm_score = 0
            gm_desc = "较差（<10%）"
    else:
        gm_score = 0
        gm_desc = "数据缺失"
    profit_score += gm_score
    profit_details.append(("毛利率", gross_margin, gm_score, gm_desc))

    # 净利率（7分）
    net_margin = financial_data.get('net_margin')
    if net_margin is not None and net_margin > 0:
        if net_margin > 20:
            nm_score = 7
            nm_desc = "优秀（>20%）"
        elif net_margin > 15:
            nm_score = 6
            nm_desc = "良好（15-20%）"
        elif net_margin > 10:
            nm_score = 4
            nm_desc = "中等（10-15%）"
        elif net_margin > 5:
            nm_score = 2
            nm_desc = "一般（5-10%）"
        else:
            nm_score = 0
            nm_desc = "较差（<5%）"
    else:
        nm_score = 0
        nm_desc = "数据缺失"
    profit_score += nm_score
    profit_details.append(("净利率", net_margin, nm_score, nm_desc))

    scores['盈利能力'] = profit_score
    details['盈利能力'] = {'score': profit_score, 'max_score': 25, 'items': profit_details}

    # ===== 2. 成长性（25分） =====
    growth_score = 0
    growth_details = []

    # 营收增长率（12分）
    revenue_growth = financial_data.get('revenue_growth')
    if revenue_growth is not None:
        if revenue_growth > 30:
            rg_score = 12
            rg_desc = "优秀（>30%）"
        elif revenue_growth > 20:
            rg_score = 10
            rg_desc = "良好（20-30%）"
        elif revenue_growth > 10:
            rg_score = 7
            rg_desc = "中等（10-20%）"
        elif revenue_growth > 0:
            rg_score = 3
            rg_desc = "一般（0-10%）"
        else:
            rg_score = 0
            rg_desc = "负增长"
    else:
        rg_score = 0
        rg_desc = "数据缺失"
    growth_score += rg_score
    growth_details.append(("营收增长率", revenue_growth, rg_score, rg_desc))

    # 净利润增长率（13分）
    profit_growth = financial_data.get('profit_growth')
    if profit_growth is not None:
        if profit_growth > 30:
            pg_score = 13
            pg_desc = "优秀（>30%）"
        elif profit_growth > 20:
            pg_score = 10
            pg_desc = "良好（20-30%）"
        elif profit_growth > 10:
            pg_score = 7
            pg_desc = "中等（10-20%）"
        elif profit_growth > 0:
            pg_score = 3
            pg_desc = "一般（0-10%）"
        else:
            pg_score = 0
            pg_desc = "负增长"
    else:
        pg_score = 0
        pg_desc = "数据缺失"
    growth_score += pg_score
    growth_details.append(("净利润增长率", profit_growth, pg_score, pg_desc))

    scores['成长性'] = growth_score
    details['成长性'] = {'score': growth_score, 'max_score': 25, 'items': growth_details}

    # ===== 3. 财务健康（20分） =====
    health_score = 0
    health_details = []

    # 资产负债率（8分）
    debt_ratio = financial_data.get('debt_ratio')
    if debt_ratio is not None and debt_ratio > 0:
        if debt_ratio < 30:
            dr_score = 8
            dr_desc = "优秀（<30%）"
        elif debt_ratio < 50:
            dr_score = 6
            dr_desc = "良好（30-50%）"
        elif debt_ratio < 70:
            dr_score = 3
            dr_desc = "一般（50-70%）"
        else:
            dr_score = 0
            dr_desc = "较高（>70%）"
    else:
        dr_score = 0
        dr_desc = "数据缺失"
    health_score += dr_score
    health_details.append(("资产负债率", debt_ratio, dr_score, dr_desc))

    # 经营现金流/净利润（7分）
    cashflow_ratio = financial_data.get('cashflow_ratio')
    if cashflow_ratio is not None and cashflow_ratio > 0:
        if cashflow_ratio > 100:
            cf_score = 7
            cf_desc = "优秀（>100%）"
        elif cashflow_ratio > 80:
            cf_score = 5
            cf_desc = "良好（80-100%）"
        elif cashflow_ratio > 50:
            cf_score = 3
            cf_desc = "一般（50-80%）"
        else:
            cf_score = 0
            cf_desc = "较差（<50%）"
    else:
        cf_score = 0
        cf_desc = "数据缺失"
    health_score += cf_score
    health_details.append(("经营现金流/净利润", cashflow_ratio, cf_score, cf_desc))

    # 有息负债率（5分）
    interest_debt_ratio = financial_data.get('interest_debt_ratio')
    if interest_debt_ratio is not None and interest_debt_ratio >= 0:
        if interest_debt_ratio < 10:
            id_score = 5
            id_desc = "优秀（<10%）"
        elif interest_debt_ratio < 30:
            id_score = 3
            id_desc = "良好（10-30%）"
        elif interest_debt_ratio < 50:
            id_score = 1
            id_desc = "一般（30-50%）"
        else:
            id_score = 0
            id_desc = "较高（>50%）"
    else:
        id_score = 0
        id_desc = "数据缺失"
    health_score += id_score
    health_details.append(("有息负债率", interest_debt_ratio, id_score, id_desc))

    scores['财务健康'] = health_score
    details['财务健康'] = {'score': health_score, 'max_score': 20, 'items': health_details}

    # ===== 4. 估值水平（20分） =====
    valuation_score = 0
    valuation_details = []

    # PE（10分）
    pe = financial_data.get('pe')
    if pe is not None and pe > 0:
        if pe < 10:
            pe_score = 10
            pe_desc = "低估（<10）"
        elif pe < 20:
            pe_score = 8
            pe_desc = "合理偏低（10-20）"
        elif pe < 30:
            pe_score = 5
            pe_desc = "合理（20-30）"
        elif pe < 50:
            pe_score = 2
            pe_desc = "偏高（30-50）"
        else:
            pe_score = 0
            pe_desc = "高估（>50）"
    else:
        pe_score = 0
        pe_desc = "数据缺失/负收益"
    valuation_score += pe_score
    valuation_details.append(("市盈率(PE)", pe, pe_score, pe_desc))

    # PB（5分）
    pb = financial_data.get('pb')
    if pb is not None and pb > 0:
        if pb < 1:
            pb_score = 5
            pb_desc = "破净（<1）"
        elif pb < 2:
            pb_score = 4
            pb_desc = "合理偏低（1-2）"
        elif pb < 4:
            pb_score = 2
            pb_desc = "合理（2-4）"
        elif pb < 6:
            pb_score = 1
            pb_desc = "偏高（4-6）"
        else:
            pb_score = 0
            pb_desc = "高估（>6）"
    else:
        pb_score = 0
        pb_desc = "数据缺失"
    valuation_score += pb_score
    valuation_details.append(("市净率(PB)", pb, pb_score, pb_desc))

    # 股息率（5分）
    dividend_rate = financial_data.get('dividend_rate')
    if dividend_rate is not None and dividend_rate > 0:
        if dividend_rate > 5:
            div_score = 5
            div_desc = "优秀（>5%）"
        elif dividend_rate > 3:
            div_score = 4
            div_desc = "良好（3-5%）"
        elif dividend_rate > 2:
            div_score = 2
            div_desc = "中等（2-3%）"
        elif dividend_rate > 1:
            div_score = 1
            div_desc = "一般（1-2%）"
        else:
            div_score = 0
            div_desc = "较低（<1%）"
    else:
        div_score = 0
        div_desc = "数据缺失/不分红"
    valuation_score += div_score
    valuation_details.append(("股息率", dividend_rate, div_score, div_desc))

    scores['估值水平'] = valuation_score
    details['估值水平'] = {'score': valuation_score, 'max_score': 20, 'items': valuation_details}

    # ===== 5. 行业地位（10分） =====
    position_score = 0
    position_details = []

    # 市值规模（5分）
    market_cap = financial_data.get('market_cap')
    if market_cap is not None and market_cap > 0:
        if market_cap > 5000:
            mc_score = 5
            mc_desc = "巨无霸（>5000亿）"
        elif market_cap > 1000:
            mc_score = 4
            mc_desc = "大盘（1000-5000亿）"
        elif market_cap > 500:
            mc_score = 3
            mc_desc = "中大盘（500-1000亿）"
        elif market_cap > 100:
            mc_score = 2
            mc_desc = "中盘（100-500亿）"
        else:
            mc_score = 1
            mc_desc = "小盘（<100亿）"
    else:
        mc_score = 0
        mc_desc = "数据缺失"
    position_score += mc_score
    position_details.append(("市值规模", market_cap, mc_score, mc_desc))

    # 行业排名（5分）- 由于获取行业排名较复杂，简化处理
    # 根据市值规模给予一定的行业地位分
    industry_rank = financial_data.get('industry_rank')
    if industry_rank is not None:
        if industry_rank <= 3:
            ir_score = 5
            ir_desc = "行业前3"
        elif industry_rank <= 10:
            ir_score = 3
            ir_desc = "行业前10"
        elif industry_rank <= 20:
            ir_score = 1
            ir_desc = "行业前20"
        else:
            ir_score = 0
            ir_desc = "其他"
    else:
        # 用市值规模近似判断行业地位
        if market_cap is not None and market_cap > 0:
            if market_cap > 1000:
                ir_score = 4
                ir_desc = "大市值（预估行业前列）"
            elif market_cap > 200:
                ir_score = 2
                ir_desc = "中市值（预估行业中上）"
            else:
                ir_score = 1
                ir_desc = "小市值"
        else:
            ir_score = 0
            ir_desc = "数据缺失"
    position_score += ir_score
    position_details.append(("行业地位", industry_rank, ir_score, ir_desc))

    scores['行业地位'] = position_score
    details['行业地位'] = {'score': position_score, 'max_score': 10, 'items': position_details}

    # ===== 计算总分 =====
    total_score = sum(scores.values())
    
    # 星级评定
    if total_score >= 85:
        stars = 5
        suggestion = "强烈推荐"
        suggestion_color = "#10B981"
    elif total_score >= 70:
        stars = 4
        suggestion = "推荐"
        suggestion_color = "#3B82F6"
    elif total_score >= 55:
        stars = 3
        suggestion = "一般"
        suggestion_color = "#FBBF24"
    elif total_score >= 40:
        stars = 2
        suggestion = "谨慎"
        suggestion_color = "#FF922B"
    else:
        stars = 1
        suggestion = "不推荐"
        suggestion_color = "#EF4444"

    return {
        'total_score': total_score,
        'stars': stars,
        'suggestion': suggestion,
        'suggestion_color': suggestion_color,
        'dimensions': scores,
        'details': details,
    }


def get_advantages_and_risks(score_result, financial_data):
    """分析优势和风险"""
    advantages = []
    risks = []
    summary_parts = []

    details = score_result['details']

    # 分析各维度
    for dim_name, dim_data in details.items():
        for item_name, value, score, desc in dim_data['items']:
            # 得分率 >= 80% 算优势
            max_scores = {
                'ROE': 10, '毛利率': 8, '净利率': 7,
                '营收增长率': 12, '净利润增长率': 13,
                '资产负债率': 8, '经营现金流/净利润': 7, '有息负债率': 5,
                '市盈率(PE)': 10, '市净率(PB)': 5, '股息率': 5,
                '市值规模': 5, '行业地位': 5,
            }
            max_s = max_scores.get(item_name, 10)
            if max_s > 0 and score / max_s >= 0.8:
                if value is not None and value > 0:
                    advantages.append("{}：{}（{:.2f}）".format(item_name, desc, value))
                else:
                    advantages.append("{}：{}".format(item_name, desc))
            elif max_s > 0 and score / max_s < 0.3 and max_s > 0:
                if value is not None and value > 0:
                    risks.append("{}：{}（{:.2f}）".format(item_name, desc, value))
                else:
                    risks.append("{}：{}".format(item_name, desc))

    # 生成一句话总结
    total = score_result['total_score']
    if total >= 80:
        summary_parts.append("基本面优秀，具有较高的投资价值")
    elif total >= 65:
        summary_parts.append("基本面良好，值得关注")
    elif total >= 50:
        summary_parts.append("基本面一般，需进一步分析")
    elif total >= 35:
        summary_parts.append("基本面偏弱，投资需谨慎")
    else:
        summary_parts.append("基本面较差，建议回避")

    # 估值提示
    pe = financial_data.get('pe')
    if pe is not None and pe > 0:
        if pe < 15:
            summary_parts.append("估值偏低")
        elif pe > 40:
            summary_parts.append("估值偏高")

    return {
        'advantages': advantages,
        'risks': risks,
        'summary': "，".join(summary_parts) + "。",
    }