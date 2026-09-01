# -*- coding: utf-8 -*-
"""
财务排雷检查 - 使用 AkShare 获取财务数据，进行 8 大雷区检查
"""

import akshare as ak
import pandas as pd
import time
from datetime import datetime


from data.cache import cached, CACHE_MINEFIELD
from utils.common import safe_float_convert, clean_number


@cached(CACHE_MINEFIELD)
def get_financial_minefield_data(stock_code):
    """获取财务排雷所需的全部数据
    
    返回字典包含所有排雷检查需要的数据项
    """
    result = {
        # 存贷双高
        'monetary_capital': None,       # 货币资金
        'total_assets': None,           # 总资产
        'interest_bearing_debt': None,  # 有息负债（短期借款+长期借款+应付债券）
        
        # 商誉占比
        'goodwill': None,               # 商誉
        'net_assets': None,             # 净资产（股东权益）
        
        # 现金流
        'operating_cashflow': None,     # 经营现金流
        'net_profit': None,             # 净利润
        
        # 应收账款
        'accounts_receivable': None,    # 应收账款
        'revenue': None,                # 营业收入
        
        # 毛利率（近3年）
        'gross_margins': [],            # 近3年毛利率列表
        
        # 存货
        'inventory': None,              # 存货
        'inventory_growth': None,       # 存货增长率
        'revenue_growth': None,         # 营收增长率
        
        # 大股东质押
        'pledge_ratio': None,           # 大股东质押比例
        
        # 审计意见
        'audit_opinion': None,          # 审计意见类型
        
        # 额外数据（用于计算增长率）
        'prev_revenue': None,           # 上年营收
        'prev_inventory': None,         # 上年存货
        'prev_accounts_receivable': None, # 上年应收账款
        
        # 原始数据引用
        '_raw_data': {},
    }
    
    # 1. 获取资产负债表数据
    _get_balance_sheet_data(stock_code, result)
    
    # 2. 获取利润表数据
    _get_income_data(stock_code, result)
    
    # 3. 获取现金流量表数据
    _get_cashflow_data(stock_code, result)
    
    # 4. 获取大股东质押数据
    _get_pledge_data(stock_code, result)
    
    # 5. 获取审计意见
    _get_audit_opinion(stock_code, result)
    
    # 6. 获取近3年毛利率
    _get_gross_margins_history(stock_code, result)
    
    return result


def _get_balance_sheet_data(stock_code, result):
    """获取资产负债表数据"""
    try:
        time.sleep(0.3)
        df_bs = ak.stock_balance_sheet_by_report_em(symbol=stock_code)
        if df_bs is not None and not df_bs.empty:
            latest = df_bs.iloc[0]
            
            # 货币资金
            for col in ['货币资金', '货币资金(元)']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['monetary_capital'] = val
                        break
            
            # 总资产
            for col in ['资产总计', '资产总计(元)']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['total_assets'] = val
                        break
            
            # 商誉
            for col in ['商誉', '商誉(元)']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['goodwill'] = val
                        break
            
            # 净资产（股东权益）
            for col in ['归属于母公司股东权益合计', '股东权益合计', '股东权益合计(元)', '所有者权益合计']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['net_assets'] = val
                        break
            
            # 应收账款
            for col in ['应收账款', '应收账款(元)']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['accounts_receivable'] = val
                        break
            
            # 存货
            for col in ['存货', '存货(元)']:
                if col in df_bs.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['inventory'] = val
                        break
            
            # 有息负债 = 短期借款 + 长期借款 + 应付债券
            short_loan = safe_float_convert(latest.get('短期借款')) or 0
            long_loan = safe_float_convert(latest.get('长期借款')) or 0
            bonds = safe_float_convert(latest.get('应付债券')) or 0
            result['interest_bearing_debt'] = short_loan + long_loan + bonds
            
            # 获取上年数据（用于计算增长率）
            if len(df_bs) > 1:
                prev = df_bs.iloc[1]
                for col in ['应收账款', '应收账款(元)']:
                    if col in df_bs.columns:
                        val = safe_float_convert(prev.get(col))
                        if val is not None:
                            result['prev_accounts_receivable'] = val
                            break
                for col in ['存货', '存货(元)']:
                    if col in df_bs.columns:
                        val = safe_float_convert(prev.get(col))
                        if val is not None:
                            result['prev_inventory'] = val
                            break
            
            result['_raw_data']['balance_sheet'] = df_bs
    except Exception as e:
        print("获取 {} 资产负债表失败：{}".format(stock_code, e))


def _get_income_data(stock_code, result):
    """获取利润表数据"""
    try:
        time.sleep(0.3)
        df_income = ak.stock_profit_sheet_by_report_em(symbol=stock_code)
        if df_income is not None and not df_income.empty:
            latest = df_income.iloc[0]
            
            # 营业收入
            for col in ['营业收入', '营业收入(元)']:
                if col in df_income.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['revenue'] = val
                        break
            
            # 净利润
            for col in ['净利润', '净利润(元)']:
                if col in df_income.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['net_profit'] = val
                        break
            
            # 获取上年营收（用于计算增长率）
            if len(df_income) > 1:
                prev = df_income.iloc[1]
                for col in ['营业收入', '营业收入(元)']:
                    if col in df_income.columns:
                        val = safe_float_convert(prev.get(col))
                        if val is not None:
                            result['prev_revenue'] = val
                            break
            
            result['_raw_data']['income'] = df_income
    except Exception as e:
        print("获取 {} 利润表失败：{}".format(stock_code, e))


def _get_cashflow_data(stock_code, result):
    """获取现金流量表数据"""
    try:
        time.sleep(0.3)
        df_cf = ak.stock_cash_flow_sheet_by_report_em(symbol=stock_code)
        if df_cf is not None and not df_cf.empty:
            latest = df_cf.iloc[0]
            
            # 经营活动产生的现金流量净额
            for col in ['经营活动产生的现金流量净额', '经营活动产生的现金流量净额(元)']:
                if col in df_cf.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['operating_cashflow'] = val
                        break
            
            result['_raw_data']['cashflow'] = df_cf
    except Exception as e:
        print("获取 {} 现金流量表失败：{}".format(stock_code, e))


def _get_pledge_data(stock_code, result):
    """获取大股东质押数据"""
    try:
        time.sleep(0.3)
        df_pledge = ak.stock_pledge_summary_em(symbol=stock_code)
        if df_pledge is not None and not df_pledge.empty:
            # 取第一大股东的质押比例
            for col in ['质押比例', '质押股份占持股比例', '质押比例(%)']:
                if col in df_pledge.columns:
                    val = safe_float_convert(df_pledge.iloc[0].get(col))
                    if val is not None:
                        result['pledge_ratio'] = val
                        break
            
            # 如果没找到，尝试找包含"比例"的列
            if result['pledge_ratio'] is None:
                for col in df_pledge.columns:
                    if '比例' in str(col) or '质押' in str(col):
                        val = safe_float_convert(df_pledge.iloc[0].get(col))
                        if val is not None and val > 0:
                            result['pledge_ratio'] = val
                            break
            
            result['_raw_data']['pledge'] = df_pledge
    except Exception as e:
        print("获取 {} 质押数据失败：{}".format(stock_code, e))


def _get_audit_opinion(stock_code, result):
    """获取审计意见"""
    try:
        time.sleep(0.3)
        # 使用 ak.stock_yjbb_em 获取年报数据，包含审计意见
        df_report = ak.stock_yjbb_em(date="20241231")
        if df_report is not None and not df_report.empty:
            # 查找对应股票
            code_col = None
            for col in ['股票代码', '代码']:
                if col in df_report.columns:
                    code_col = col
                    break
            
            if code_col:
                match = df_report[df_report[code_col].astype(str).str.contains(stock_code)]
                if not match.empty:
                    for col in ['审计意见', '审计意见类型']:
                        if col in match.columns:
                            val = match.iloc[0].get(col)
                            if val is not None and str(val) != 'nan':
                                result['audit_opinion'] = str(val)
                                break
    except Exception as e:
        print("获取 {} 审计意见失败：{}".format(stock_code, e))
    
    # 如果上面没获取到，尝试另一种方式
    if result['audit_opinion'] is None:
        try:
            time.sleep(0.3)
            # 获取个股年报数据
            df_report_detail = ak.stock_yjkb_em(symbol=stock_code)
            if df_report_detail is not None and not df_report_detail.empty:
                for col in ['审计意见', '审计意见类型']:
                    if col in df_report_detail.columns:
                        val = df_report_detail.iloc[0].get(col)
                        if val is not None and str(val) != 'nan':
                            result['audit_opinion'] = str(val)
                            break
        except Exception as e:
            print("获取 {} 审计意见(备选)失败：{}".format(stock_code, e))


def _get_gross_margins_history(stock_code, result):
    """获取近3年毛利率数据"""
    try:
        time.sleep(0.3)
        df_fin = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
        if df_fin is not None and not df_fin.empty:
            # 取最近3年毛利率（THS 按年度为年份升序，需反转后从头取；值带单位字符串需清洗）
            count = 0
            for _, row in df_fin.iloc[::-1].iterrows():
                if count >= 3:
                    break
                for col in ['毛利率', '销售毛利率']:
                    if col in df_fin.columns:
                        val = clean_number(row.get(col))
                        if val is not None and val != 0:
                            result['gross_margins'].append(val)
                            count += 1
                            break
    except Exception as e:
        print("获取 {} 毛利率历史数据失败：{}".format(stock_code, e))


def check_minefields(data):
    """执行8大雷区检查
    
    返回检查结果列表，每个结果包含：
    - name: 风险名称
    - level: 危险程度（高/中/低）
    - level_color: 颜色代码
    - is_risk: 是否发现风险
    - detail: 具体数据描述
    - explanation: 通俗解释
    - suggestion: 建议
    """
    results = []
    
    # ===== 1. 存贷双高 =====
    result1 = _check_deposit_loan_high(data)
    results.append(result1)
    
    # ===== 2. 商誉占比过高 =====
    result2 = _check_goodwill(data)
    results.append(result2)
    
    # ===== 3. 现金流差 =====
    result3 = _check_cashflow(data)
    results.append(result3)
    
    # ===== 4. 应收账款异常 =====
    result4 = _check_receivables(data)
    results.append(result4)
    
    # ===== 5. 大股东质押过高 =====
    result5 = _check_pledge(data)
    results.append(result5)
    
    # ===== 6. 毛利率异常波动 =====
    result6 = _check_gross_margin_volatility(data)
    results.append(result6)
    
    # ===== 7. 存货异常增长 =====
    result7 = _check_inventory_growth(data)
    results.append(result7)
    
    # ===== 8. 审计意见异常 =====
    result8 = _check_audit_opinion(data)
    results.append(result8)
    
    return results


def _check_deposit_loan_high(data):
    """检查存贷双高"""
    result = {
        'id': 'deposit_loan_high',
        'name': '存贷双高',
        'level': '高',
        'level_color': '#EF4444',
        'level_bg': 'rgba(239, 68, 68, 0.15)',
        'icon': '🔴',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '账上有很多钱，还借了很多高息债，可能资金是假的或者被占用了',
        'suggestion': '建议查看货币资金明细，是否存在大额受限资金或资金被关联方占用的情况',
    }
    
    monetary = data.get('monetary_capital')
    total_assets = data.get('total_assets')
    interest_debt = data.get('interest_bearing_debt')
    
    if monetary is None or total_assets is None or total_assets == 0:
        result['data_available'] = False
        result['detail'] = '数据缺失（需要货币资金、总资产、有息负债数据）'
        return result
    
    if interest_debt is None:
        result['data_available'] = False
        result['detail'] = '数据缺失（需要短期借款、长期借款、应付债券数据）'
        return result
    
    monetary_ratio = monetary / total_assets * 100
    debt_ratio = interest_debt / total_assets * 100
    
    result['detail'] = '货币资金/总资产：{:.1f}%，有息负债/总资产：{:.1f}%'.format(monetary_ratio, debt_ratio)
    
    if monetary_ratio > 30 and debt_ratio > 30:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 两者均超过30%'
    
    return result


def _check_goodwill(data):
    """检查商誉占比过高"""
    result = {
        'id': 'goodwill',
        'name': '商誉占比过高',
        'level': '高',
        'level_color': '#EF4444',
        'level_bg': 'rgba(239, 68, 68, 0.15)',
        'icon': '🔴',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '商誉太高，一旦减值会巨亏。商誉是收购溢价，如果被收购公司业绩不达标就要减值',
        'suggestion': '关注被收购公司的业绩承诺完成情况，以及商誉减值测试的合理性',
    }
    
    goodwill = data.get('goodwill')
    net_assets = data.get('net_assets')
    
    if goodwill is None or net_assets is None or net_assets == 0:
        result['data_available'] = False
        result['detail'] = '数据缺失（需要商誉和净资产数据）'
        return result
    
    goodwill_ratio = goodwill / net_assets * 100
    
    result['detail'] = '商誉：{:.2f}亿，净资产：{:.2f}亿，占比：{:.1f}%'.format(
        goodwill / 100000000, net_assets / 100000000, goodwill_ratio
    )
    
    if goodwill_ratio > 30:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 超过30%警戒线'
    
    return result


def _check_cashflow(data):
    """检查现金流质量"""
    result = {
        'id': 'cashflow',
        'name': '现金流差',
        'level': '中',
        'level_color': '#FF922B',
        'level_bg': 'rgba(255, 146, 43, 0.15)',
        'icon': '🟠',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '赚的都是纸面利润，没收到真金白银。经营现金流远低于净利润，说明利润质量不高',
        'suggestion': '关注应收账款回收情况，是否存在大量赊销或提前确认收入的情况',
    }
    
    operating_cf = data.get('operating_cashflow')
    net_profit = data.get('net_profit')
    
    if operating_cf is None or net_profit is None or net_profit == 0:
        result['data_available'] = False
        result['detail'] = '数据缺失（需要经营现金流和净利润数据）'
        return result
    
    cf_ratio = operating_cf / net_profit * 100
    
    result['detail'] = '经营现金流：{:.2f}亿，净利润：{:.2f}亿，比值：{:.1f}%'.format(
        operating_cf / 100000000, net_profit / 100000000, cf_ratio
    )
    
    if cf_ratio < 50:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 低于50%'
    
    return result


def _check_receivables(data):
    """检查应收账款异常"""
    result = {
        'id': 'receivables',
        'name': '应收账款异常',
        'level': '中',
        'level_color': '#FF922B',
        'level_bg': 'rgba(255, 146, 43, 0.15)',
        'icon': '🟠',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '可能是靠赊账堆出来的收入，有坏账风险。应收账款增长远超营收增长，说明回款能力变差',
        'suggestion': '关注应收账款账龄结构和坏账准备计提是否充分',
    }
    
    accounts_recv = data.get('accounts_receivable')
    prev_recv = data.get('prev_accounts_receivable')
    revenue = data.get('revenue')
    prev_revenue = data.get('prev_revenue')
    
    if (accounts_recv is None or prev_recv is None or prev_recv == 0 or
        revenue is None or prev_revenue is None or prev_revenue == 0):
        result['data_available'] = False
        result['detail'] = '数据缺失（需要本期和上期的应收账款、营业收入数据）'
        return result
    
    recv_growth = (accounts_recv - prev_recv) / prev_recv * 100
    rev_growth = (revenue - prev_revenue) / prev_revenue * 100
    
    result['detail'] = '应收账款增长率：{:.1f}%，营收增长率：{:.1f}%'.format(recv_growth, rev_growth)
    
    if recv_growth > rev_growth * 2 and rev_growth > 0:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 应收账款增长远超营收增长'
    elif recv_growth > 0 and rev_growth <= 0:
        # 营收负增长但应收账款还在增长，也是风险
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 营收下滑但应收账款仍在增长'
    
    return result


def _check_pledge(data):
    """检查大股东质押过高"""
    result = {
        'id': 'pledge',
        'name': '大股东质押过高',
        'level': '中',
        'level_color': '#FF922B',
        'level_bg': 'rgba(255, 146, 43, 0.15)',
        'icon': '🟠',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '大股东缺钱，可能有平仓风险，或者不看好公司。大股东高比例质押说明其资金链紧张',
        'suggestion': '关注质押平仓线价格，以及大股东是否有其他融资渠道',
    }
    
    pledge_ratio = data.get('pledge_ratio')
    
    if pledge_ratio is None:
        result['data_available'] = False
        result['detail'] = '数据缺失（无法获取大股东质押数据）'
        return result
    
    result['detail'] = '大股东质押比例：{:.1f}%'.format(pledge_ratio)
    
    if pledge_ratio > 50:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 超过50%警戒线'
    
    return result


def _check_gross_margin_volatility(data):
    """检查毛利率异常波动"""
    result = {
        'id': 'gross_margin_volatility',
        'name': '毛利率异常波动',
        'level': '低',
        'level_color': '#FBBF24',
        'level_bg': 'rgba(251, 191, 36, 0.15)',
        'icon': '🟡',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '业务不稳定，或者有财务调节嫌疑。毛利率大幅波动说明公司盈利能力不稳定',
        'suggestion': '分析毛利率波动原因，是行业周期还是公司自身经营问题',
    }
    
    gross_margins = data.get('gross_margins', [])
    
    if not gross_margins or len(gross_margins) < 2:
        result['data_available'] = False
        result['detail'] = '数据缺失（需要至少2年毛利率数据）'
        return result
    
    max_margin = max(gross_margins)
    min_margin = min(gross_margins)
    volatility = max_margin - min_margin
    
    margins_str = '、'.join(['{:.1f}%'.format(m) for m in gross_margins])
    result['detail'] = '近{}年毛利率：{}，最大波动：{:.1f}个百分点'.format(
        len(gross_margins), margins_str, volatility
    )
    
    if volatility > 10:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 波动超过10个百分点'
    
    return result


def _check_inventory_growth(data):
    """检查存货异常增长"""
    result = {
        'id': 'inventory_growth',
        'name': '存货异常增长',
        'level': '低',
        'level_color': '#FBBF24',
        'level_bg': 'rgba(251, 191, 36, 0.15)',
        'icon': '🟡',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '产品卖不出去，可能要跌价减值。存货增长远超营收增长，说明产品滞销',
        'suggestion': '关注存货周转天数和存货跌价准备计提情况',
    }
    
    inventory = data.get('inventory')
    prev_inventory = data.get('prev_inventory')
    revenue = data.get('revenue')
    prev_revenue = data.get('prev_revenue')
    
    if (inventory is None or prev_inventory is None or prev_inventory == 0 or
        revenue is None or prev_revenue is None or prev_revenue == 0):
        result['data_available'] = False
        result['detail'] = '数据缺失（需要本期和上期的存货、营业收入数据）'
        return result
    
    inv_growth = (inventory - prev_inventory) / prev_inventory * 100
    rev_growth = (revenue - prev_revenue) / prev_revenue * 100
    
    result['detail'] = '存货增长率：{:.1f}%，营收增长率：{:.1f}%'.format(inv_growth, rev_growth)
    
    if inv_growth > rev_growth * 2 and rev_growth > 0:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 存货增长远超营收增长'
    elif inv_growth > 0 and rev_growth <= 0:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 营收下滑但存货仍在增长'
    
    return result


def _check_audit_opinion(data):
    """检查审计意见"""
    result = {
        'id': 'audit_opinion',
        'name': '审计意见异常',
        'level': '高',
        'level_color': '#EF4444',
        'level_bg': 'rgba(239, 68, 68, 0.15)',
        'icon': '🔴',
        'is_risk': False,
        'data_available': True,
        'detail': '',
        'explanation': '会计师都不敢保证财报是真的。非标准审计意见说明财务报表存在重大问题',
        'suggestion': '仔细阅读审计报告中的强调事项段，了解具体问题所在',
    }
    
    opinion = data.get('audit_opinion')
    
    if opinion is None:
        result['data_available'] = False
        result['detail'] = '数据缺失（无法获取审计意见数据）'
        return result
    
    result['detail'] = '审计意见：{}'.format(opinion)
    
    # 标准无保留意见是安全的
    standard_opinions = ['标准无保留意见', '无保留意见', '标准无保留']
    is_standard = any(keyword in opinion for keyword in standard_opinions)
    
    if not is_standard:
        result['is_risk'] = True
        result['detail'] += ' ⚠️ 非标准无保留意见'
    
    return result


def calculate_risk_rating(results):
    """根据检查结果计算风险评级
    
    返回：
    - level: 风险等级（安全/低风险/中风险/较高风险/高风险）
    - level_color: 对应颜色
    - level_icon: 对应图标
    - summary: 一句话总结
    - high_count: 高危数量
    - medium_count: 中危数量
    - low_count: 低危数量
    - safe_count: 安全项目数
    - total_checked: 已检查项目数
    """
    high_risks = [r for r in results if r['is_risk'] and r['level'] == '高']
    medium_risks = [r for r in results if r['is_risk'] and r['level'] == '中']
    low_risks = [r for r in results if r['is_risk'] and r['level'] == '低']
    
    high_count = len(high_risks)
    medium_count = len(medium_risks)
    low_count = len(low_risks)
    total_risks = high_count + medium_count + low_count
    
    safe_count = len([r for r in results if not r['is_risk'] and r['data_available']])
    total_checked = len([r for r in results if r['data_available']])
    
    # 风险评级
    if total_risks == 0:
        level = '✅ 安全'
        level_color = '#10B981'
        summary = '财务很健康，未发现明显风险点'
    elif high_count >= 2:
        level = '🔴 高风险'
        level_color = '#EF4444'
        summary = '存在{}个高危风险，建议避开'.format(high_count)
    elif high_count >= 1 or medium_count >= 2:
        level = '🟠 较高风险'
        level_color = '#FF922B'
        summary = '存在{}个高危/{}个中危风险，谨慎投资'.format(high_count, medium_count)
    elif medium_count >= 1 or low_count >= 3:
        level = '🟡 中风险'
        level_color = '#FBBF24'
        summary = '存在{}个中危/{}个低危风险，需要关注'.format(medium_count, low_count)
    else:
        level = '🟢 低风险'
        level_color = '#34D399'
        summary = '存在{}个低危风险，小问题基本安全'.format(low_count)
    
    return {
        'level': level,
        'level_color': level_color,
        'summary': summary,
        'high_count': high_count,
        'medium_count': medium_count,
        'low_count': low_count,
        'total_risks': total_risks,
        'safe_count': safe_count,
        'total_checked': total_checked,
    }


def get_safe_items(results):
    """获取安全（未触发风险）的项目列表"""
    safe_items = []
    for r in results:
        if not r['is_risk'] and r['data_available']:
            safe_items.append(r)
    return safe_items


def get_risk_items(results):
    """获取触发风险的项目列表"""
    risk_items = []
    for r in results:
        if r['is_risk']:
            risk_items.append(r)
    return risk_items


def get_comprehensive_advice(risk_rating, risk_items):
    """生成综合建议"""
    level = risk_rating['level']
    high_count = risk_rating['high_count']
    medium_count = risk_rating['medium_count']
    low_count = risk_rating['low_count']
    
    advice_parts = []
    
    if '安全' in level:
        advice_parts.append('✅ 该公司财务状况健康，各项指标正常。')
        advice_parts.append('但投资仍需结合行业前景、估值水平等因素综合判断。')
    elif '低风险' in level:
        advice_parts.append('🟢 该公司财务状况基本健康，存在少量小问题。')
        advice_parts.append('建议关注这些低风险项目的变化趋势，目前不影响整体判断。')
    elif '中风险' in level:
        advice_parts.append('🟡 该公司存在一些需要关注的财务问题。')
        if medium_count > 0:
            advice_parts.append('中危风险需要重点关注，建议深入研究相关细节。')
        advice_parts.append('建议结合其他分析工具（基本面评分、估值等）综合判断。')
    elif '较高风险' in level:
        advice_parts.append('🟠 该公司存在较多财务风险，投资需格外谨慎。')
        if high_count > 0:
            advice_parts.append('高危风险项目需要高度重视，建议仔细阅读相关财报附注。')
        advice_parts.append('如果已经持有，建议密切关注风险变化；如果尚未买入，建议谨慎考虑。')
    elif '高风险' in level:
        advice_parts.append('🔴 该公司存在严重财务风险，强烈建议避开！')
        advice_parts.append('多个高危风险同时存在，说明公司财务质量堪忧。')
        advice_parts.append('除非有非常特殊的理由，否则不建议投资此类公司。')
    
    # 重点关注
    focus_items = []
    for r in risk_items:
        if r['level'] == '高':
            focus_items.append('🔴 {}：{}'.format(r['name'], r['explanation']))
        elif r['level'] == '中':
            focus_items.append('🟠 {}：{}'.format(r['name'], r['explanation']))
    
    return {
        'advice': '\n\n'.join(advice_parts),
        'focus_items': focus_items,
    }


def minefield_pipeline(stock_code):
    """排雷五连链打包（Agent 工具入口）：data → check_minefields → calculate_risk_rating
    → get_risk_items / get_safe_items → get_comprehensive_advice。

    只暴露对 LLM 有用的可读结论（原始 dict 含 DataFrame 的 _raw_data 对 LLM 无用）。
    单段失败不阻断，errors 记录原因（Agent 依此明说"数据不可得"）。

    实现依据：docs/AGENT_MVP_DESIGN.md §3 首批工具清单（P0）。
    """
    result = {
        "stock_code": stock_code,
        "risk_rating": None,
        "risk_items": [],
        "safe_items": [],
        "advice": None,
        "checked_count": 0,
        "errors": [],
    }
    try:
        raw_data = get_financial_minefield_data(stock_code)
    except Exception as e:  # noqa: BLE001
        result["errors"].append("排雷数据获取失败：{}".format(e))
        return result
    if not raw_data:
        result["errors"].append("排雷原始数据不可得（可能停牌或接口无返回）")
        return result

    try:
        results = check_minefields(raw_data)
        result["risk_rating"] = calculate_risk_rating(results)
        result["risk_items"] = get_risk_items(results)
        result["safe_items"] = get_safe_items(results)
        result["checked_count"] = len(results)
        result["advice"] = get_comprehensive_advice(result["risk_rating"], result["risk_items"])
    except Exception as e:  # noqa: BLE001
        result["errors"].append("排雷分析失败：{}".format(e))
    return result
