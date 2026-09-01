# -*- coding: utf-8 -*-
"""
护城河分析 - 使用 AkShare 获取财务数据，进行 6 大护城河类型评分
"""

import akshare as ak
import pandas as pd
import time
from datetime import datetime


from data.cache import cached, CACHE_MOAT
from utils.common import safe_float_convert


@cached(CACHE_MOAT)
def get_moat_analysis_data(stock_code):
    """获取护城河分析所需的全部数据
    
    返回字典包含所有护城河评分需要的数据项
    """
    result = {
        # 基础财务数据
        'stock_name': None,
        'industry': None,
        'market_cap': None,         # 总市值(亿)
        'revenue': None,            # 营业收入(元)
        'net_profit': None,         # 净利润(元)
        'gross_margin': None,       # 毛利率(%)
        'net_margin': None,         # 净利率(%)
        'roe': None,                # ROE(%)
        'revenue_growth': None,     # 营收增长率(%)
        'profit_growth': None,      # 净利润增长率(%)
        'debt_ratio': None,         # 资产负债率(%)
        'rd_expense': None,         # 研发费用(元)
        'rd_ratio': None,           # 研发费用占营收比例(%)
        'total_assets': None,       # 总资产(元)
        'fixed_assets': None,       # 固定资产(元)
        
        # 行业数据
        'industry_avg_gross_margin': None,  # 行业平均毛利率
        'industry_avg_net_margin': None,    # 行业平均净利率
        'industry_pe': None,                # 行业市盈率
        
        # 原始数据引用
        '_raw_data': {},
    }
    
    # 1. 获取基础财务数据
    _get_basic_financial_data(stock_code, result)
    
    # 2. 获取利润表数据
    _get_income_data(stock_code, result)
    
    # 3. 获取研发费用
    _get_rd_expense(stock_code, result)
    
    # 4. 获取行业对比数据
    _get_industry_data(stock_code, result)
    
    return result


def _get_basic_financial_data(stock_code, result):
    """获取基础财务指标"""
    try:
        time.sleep(0.3)
        df_fin = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
        if df_fin is not None and not df_fin.empty:
            latest = df_fin.iloc[0]
            
            # ROE
            for col in ['净资产收益率', 'ROE', '加权净资产收益率']:
                if col in df_fin.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None and val != 0:
                        result['roe'] = val
                        break
            
            # 毛利率
            for col in ['毛利率', '销售毛利率']:
                if col in df_fin.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None and val != 0:
                        result['gross_margin'] = val
                        break
            
            # 净利率
            for col in ['净利率', '销售净利率']:
                if col in df_fin.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None and val != 0:
                        result['net_margin'] = val
                        break
            
            # 营收增长率
            for col in ['营业收入增长率', '营收增长率', '营业总收入同比增长率']:
                if col in df_fin.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None and val != 0:
                        result['revenue_growth'] = val
                        break
            
            # 净利润增长率
            for col in ['净利润增长率', '归属净利润增长率', '归属于母公司所有者的净利润同比增长率']:
                if col in df_fin.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None and val != 0:
                        result['profit_growth'] = val
                        break
            
            result['_raw_data']['financial_abstract'] = df_fin
    except Exception as e:
        print("获取 {} 财务指标失败：{}".format(stock_code, e))
    
    # 获取个股信息（名称、行业、市值）
    try:
        time.sleep(0.3)
        df_info = ak.stock_individual_info_em(symbol=stock_code)
        if df_info is not None and not df_info.empty:
            for _, row in df_info.iterrows():
                item = str(row.iloc[0])
                val = row.iloc[1]
                if '名称' in item:
                    result['stock_name'] = str(val)
                elif '行业' in item:
                    result['industry'] = str(val)
                elif '总市值' in item:
                    result['market_cap'] = safe_float_convert(val)
    except Exception as e:
        print("获取 {} 个股信息失败：{}".format(stock_code, e))


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
            
            result['_raw_data']['income'] = df_income
    except Exception as e:
        print("获取 {} 利润表失败：{}".format(stock_code, e))


def _get_rd_expense(stock_code, result):
    """获取研发费用"""
    try:
        time.sleep(0.3)
        df_rd = ak.stock_rd_em(symbol=stock_code)
        if df_rd is not None and not df_rd.empty:
            latest = df_rd.iloc[0]
            for col in ['研发费用', '研发费用(元)']:
                if col in df_rd.columns:
                    val = safe_float_convert(latest.get(col))
                    if val is not None:
                        result['rd_expense'] = val
                        break
            
            # 计算研发费用占营收比例
            if result['rd_expense'] is not None and result['revenue'] is not None and result['revenue'] != 0:
                result['rd_ratio'] = round(result['rd_expense'] / result['revenue'] * 100, 2)
            
            result['_raw_data']['rd'] = df_rd
    except Exception as e:
        print("获取 {} 研发费用失败：{}".format(stock_code, e))
    
    # 如果上面没获取到，尝试从利润表获取
    if result['rd_expense'] is None:
        try:
            time.sleep(0.3)
            df_income = ak.stock_profit_sheet_by_report_em(symbol=stock_code)
            if df_income is not None and not df_income.empty:
                latest = df_income.iloc[0]
                for col in ['研发费用', '研发费用(元)']:
                    if col in df_income.columns:
                        val = safe_float_convert(latest.get(col))
                        if val is not None:
                            result['rd_expense'] = val
                            break
                
                if result['rd_expense'] is not None and result['revenue'] is not None and result['revenue'] != 0:
                    result['rd_ratio'] = round(result['rd_expense'] / result['revenue'] * 100, 2)
        except Exception as e:
            print("获取 {} 研发费用(备选)失败：{}".format(stock_code, e))


def _get_industry_data(stock_code, result):
    """获取行业对比数据"""
    industry = result.get('industry')
    if not industry:
        return
    
    try:
        time.sleep(0.3)
        # 获取行业板块成分股
        df_board = ak.stock_board_industry_cons_em(symbol=industry)
        if df_board is not None and not df_board.empty:
            # 获取同行业公司的财务数据
            gross_margins = []
            net_margins = []
            
            # 限制最多取20家公司做对比
            companies = df_board.head(20)
            
            for _, row in companies.iterrows():
                code = str(row.iloc[1])  # 股票代码
                if code == stock_code:
                    continue
                try:
                    time.sleep(0.1)
                    df_fin = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
                    if df_fin is not None and not df_fin.empty:
                        latest = df_fin.iloc[0]
                        
                        for col in ['毛利率', '销售毛利率']:
                            if col in df_fin.columns:
                                val = safe_float_convert(latest.get(col))
                                if val is not None and val > 0:
                                    gross_margins.append(val)
                                break
                        
                        for col in ['净利率', '销售净利率']:
                            if col in df_fin.columns:
                                val = safe_float_convert(latest.get(col))
                                if val is not None and val > 0:
                                    net_margins.append(val)
                                break
                except Exception:
                    continue
            
            if gross_margins:
                result['industry_avg_gross_margin'] = round(sum(gross_margins) / len(gross_margins), 2)
            if net_margins:
                result['industry_avg_net_margin'] = round(sum(net_margins) / len(net_margins), 2)
            
            result['_raw_data']['industry_board'] = df_board
    except Exception as e:
        print("获取 {} 行业数据失败：{}".format(stock_code, e))


def calculate_moat_scores(data):
    """计算6大护城河评分
    
    返回：
    - scores: 各类型得分字典
    - details: 各类型详细分析
    - total_score: 总分(100分制)
    - level: 护城河等级
    - level_color: 等级颜色
    - summary: 一句话总结
    - strengths: 优势分析
    - weaknesses: 不足分析
    - advice: 投资建议
    """
    scores = {}
    details = {}
    
    # ===== 1. 品牌护城河 (0-10分) =====
    brand_score, brand_detail = _score_brand_moat(data)
    scores['品牌护城河'] = brand_score
    details['品牌护城河'] = brand_detail
    
    # ===== 2. 技术/专利护城河 (0-10分) =====
    tech_score, tech_detail = _score_tech_moat(data)
    scores['技术/专利护城河'] = tech_score
    details['技术/专利护城河'] = tech_detail
    
    # ===== 3. 规模效应护城河 (0-10分) =====
    scale_score, scale_detail = _score_scale_moat(data)
    scores['规模效应护城河'] = scale_score
    details['规模效应护城河'] = scale_detail
    
    # ===== 4. 网络效应护城河 (0-10分) =====
    network_score, network_detail = _score_network_moat(data)
    scores['网络效应护城河'] = network_score
    details['网络效应护城河'] = network_detail
    
    # ===== 5. 转换成本护城河 (0-10分) =====
    switch_score, switch_detail = _score_switch_cost_moat(data)
    scores['转换成本护城河'] = switch_score
    details['转换成本护城河'] = switch_detail
    
    # ===== 6. 资源垄断护城河 (0-10分) =====
    resource_score, resource_detail = _score_resource_moat(data)
    scores['资源垄断护城河'] = resource_score
    details['资源垄断护城河'] = resource_detail
    
    # ===== 计算总分 =====
    raw_total = sum(scores.values())  # 满分60分
    total_score = round(raw_total / 60 * 100)  # 转为100分制
    
    # ===== 护城河等级 =====
    if total_score >= 80:
        level = '⭐ 超级护城河'
        level_color = '#8B5CF6'
        summary = '拥有极其强大的竞争优势，竞争对手难以撼动'
    elif total_score >= 60:
        level = '🔵 宽阔护城河'
        level_color = '#3B82F6'
        summary = '拥有宽阔的护城河，竞争优势明显'
    elif total_score >= 40:
        level = '🟢 较宽护城河'
        level_color = '#10B981'
        summary = '拥有一定的护城河，具备竞争优势'
    elif total_score >= 20:
        level = '🟡 微弱护城河'
        level_color = '#FBBF24'
        summary = '护城河较浅，竞争优势不够明显'
    else:
        level = '❌ 没有护城河'
        level_color = '#EF4444'
        summary = '几乎没有护城河，容易被竞争对手超越'
    
    # ===== 优势分析 =====
    strengths = _analyze_strengths(scores, details)
    
    # ===== 不足分析 =====
    weaknesses = _analyze_weaknesses(scores, details)
    
    # ===== 投资建议 =====
    advice = _generate_advice(total_score, level, strengths, weaknesses)
    
    return {
        'scores': scores,
        'details': details,
        'total_score': total_score,
        'level': level,
        'level_color': level_color,
        'summary': summary,
        'strengths': strengths,
        'weaknesses': weaknesses,
        'advice': advice,
    }


def _score_brand_moat(data):
    """品牌护城河评分"""
    gross_margin = data.get('gross_margin')
    net_margin = data.get('net_margin')
    industry_avg_gm = data.get('industry_avg_gross_margin')
    industry_avg_nm = data.get('industry_avg_net_margin')
    market_cap = data.get('market_cap')
    
    score = 0
    reasons = []
    data_items = []
    
    # 毛利率高于行业平均 -> 品牌溢价
    if gross_margin is not None and industry_avg_gm is not None:
        diff = gross_margin - industry_avg_gm
        data_items.append('毛利率：{:.1f}%（行业平均：{:.1f}%）'.format(gross_margin, industry_avg_gm))
        if diff > 20:
            score += 4
            reasons.append('毛利率远超行业平均，说明有很强的品牌溢价能力')
        elif diff > 10:
            score += 3
            reasons.append('毛利率明显高于行业平均，品牌有一定溢价能力')
        elif diff > 0:
            score += 2
            reasons.append('毛利率略高于行业平均，品牌有微弱溢价')
        else:
            reasons.append('毛利率低于行业平均，品牌溢价能力不足')
    elif gross_margin is not None:
        data_items.append('毛利率：{:.1f}%（行业平均数据缺失）'.format(gross_margin))
        if gross_margin > 60:
            score += 3
            reasons.append('毛利率很高（>60%），可能有品牌溢价')
        elif gross_margin > 40:
            score += 2
            reasons.append('毛利率较高（>40%），可能有品牌效应')
        elif gross_margin > 20:
            score += 1
            reasons.append('毛利率一般')
        else:
            reasons.append('毛利率偏低')
    else:
        data_items.append('毛利率数据缺失')
    
    # 净利率高于行业平均
    if net_margin is not None and industry_avg_nm is not None:
        diff = net_margin - industry_avg_nm
        data_items.append('净利率：{:.1f}%（行业平均：{:.1f}%）'.format(net_margin, industry_avg_nm))
        if diff > 10:
            score += 3
            reasons.append('净利率远超行业平均，品牌效应带来高利润')
        elif diff > 5:
            score += 2
            reasons.append('净利率高于行业平均')
        elif diff > 0:
            score += 1
            reasons.append('净利率略高于行业平均')
    elif net_margin is not None:
        data_items.append('净利率：{:.1f}%（行业平均数据缺失）'.format(net_margin))
        if net_margin > 20:
            score += 2
            reasons.append('净利率很高（>20%）')
        elif net_margin > 10:
            score += 1
            reasons.append('净利率尚可')
    
    # 市值规模 -> 品牌知名度
    if market_cap is not None:
        data_items.append('总市值：{:.0f}亿'.format(market_cap))
        if market_cap > 5000:
            score += 3
            reasons.append('超大型公司（>5000亿），品牌知名度极高')
        elif market_cap > 1000:
            score += 2
            reasons.append('大型公司（1000-5000亿），品牌知名度较高')
        elif market_cap > 200:
            score += 1
            reasons.append('中型公司，有一定品牌知名度')
    else:
        data_items.append('市值数据缺失')
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '品牌护城河衡量公司品牌带来的定价权和客户忠诚度。毛利率和净利率高于行业平均说明品牌有溢价能力。',
    }
    
    return score, detail


def _score_tech_moat(data):
    """技术/专利护城河评分"""
    rd_ratio = data.get('rd_ratio')
    gross_margin = data.get('gross_margin')
    industry = data.get('industry', '')
    
    score = 0
    reasons = []
    data_items = []
    
    # 研发费用占比
    if rd_ratio is not None:
        data_items.append('研发费用占营收比例：{:.1f}%'.format(rd_ratio))
        if rd_ratio > 15:
            score += 4
            reasons.append('研发投入极高（>15%），属于技术驱动型公司')
        elif rd_ratio > 10:
            score += 3
            reasons.append('研发投入很高（>10%），重视技术创新')
        elif rd_ratio > 5:
            score += 2
            reasons.append('研发投入较高（>5%），有一定技术积累')
        elif rd_ratio > 3:
            score += 1
            reasons.append('研发投入一般（3-5%）')
        else:
            reasons.append('研发投入较低（<3%），技术壁垒可能不高')
    else:
        data_items.append('研发费用数据缺失')
    
    # 毛利率 -> 技术壁垒
    if gross_margin is not None:
        data_items.append('毛利率：{:.1f}%'.format(gross_margin))
        if gross_margin > 70:
            score += 3
            reasons.append('毛利率极高（>70%），技术壁垒带来高附加值')
        elif gross_margin > 50:
            score += 2
            reasons.append('毛利率很高（>50%），可能有技术优势')
        elif gross_margin > 30:
            score += 1
            reasons.append('毛利率较高（>30%）')
    
    # 行业属性 -> 技术密集型行业加分
    tech_industries = ['半导体', '芯片', '软件', '医药', '生物', '电子', '通信', '计算机',
                       '医疗器械', '新材料', '新能源', '高端装备', '航天', '军工']
    for ti in tech_industries:
        if ti in industry:
            score += 2
            reasons.append('所属行业「{}」属于技术密集型行业'.format(industry))
            break
    
    # 如果研发费用缺失，根据行业和毛利率估算
    if rd_ratio is None and gross_margin is not None:
        if gross_margin > 60:
            score += 1
            reasons.append('高毛利率可能反映技术优势（研发费用数据缺失）')
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '技术/专利护城河衡量公司的技术壁垒和创新能力。高研发投入和高毛利率通常意味着技术优势。',
    }
    
    return score, detail


def _score_scale_moat(data):
    """规模效应护城河评分"""
    market_cap = data.get('market_cap')
    revenue = data.get('revenue')
    gross_margin = data.get('gross_margin')
    industry_avg_gm = data.get('industry_avg_gross_margin')
    industry = data.get('industry', '')
    
    score = 0
    reasons = []
    data_items = []
    
    # 市值规模
    if market_cap is not None:
        data_items.append('总市值：{:.0f}亿'.format(market_cap))
        if market_cap > 5000:
            score += 3
            reasons.append('超大规模（>5000亿），规模效应显著')
        elif market_cap > 1000:
            score += 2
            reasons.append('大规模（1000-5000亿），有一定规模效应')
        elif market_cap > 200:
            score += 1
            reasons.append('中等规模')
    else:
        data_items.append('市值数据缺失')
    
    # 营收规模
    if revenue is not None:
        revenue_yi = revenue / 100000000
        data_items.append('营业收入：{:.0f}亿'.format(revenue_yi))
        if revenue_yi > 1000:
            score += 3
            reasons.append('营收超千亿，规模效应极强')
        elif revenue_yi > 100:
            score += 2
            reasons.append('营收超百亿，规模效应明显')
        elif revenue_yi > 10:
            score += 1
            reasons.append('营收超十亿，有一定规模')
    else:
        data_items.append('营收数据缺失')
    
    # 毛利率高于行业平均 -> 规模带来成本优势
    if gross_margin is not None and industry_avg_gm is not None:
        diff = gross_margin - industry_avg_gm
        if diff > 10:
            score += 3
            reasons.append('毛利率明显高于行业平均，规模效应带来成本优势')
        elif diff > 0:
            score += 2
            reasons.append('毛利率高于行业平均，可能有规模优势')
        elif diff > -5:
            score += 1
            reasons.append('毛利率与行业平均接近')
    elif gross_margin is not None:
        if gross_margin > 40:
            score += 2
            reasons.append('毛利率较高，可能有规模优势')
        elif gross_margin > 20:
            score += 1
    
    # 重资产行业规模效应更明显
    capital_intensive = ['钢铁', '煤炭', '化工', '汽车', '制造', '电力', '能源',
                         '水泥', '玻璃', '造纸', '航空', '物流']
    for ci in capital_intensive:
        if ci in industry:
            score += 1
            reasons.append('「{}」行业规模效应明显'.format(industry))
            break
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '规模效应护城河衡量公司是否通过大规模生产/运营获得成本优势。大市值、高营收且毛利率高于行业平均通常意味着规模效应。',
    }
    
    return score, detail


def _score_network_moat(data):
    """网络效应护城河评分"""
    industry = data.get('industry', '')
    revenue_growth = data.get('revenue_growth')
    gross_margin = data.get('gross_margin')
    market_cap = data.get('market_cap')
    
    score = 0
    reasons = []
    data_items = []
    
    # 行业属性 -> 网络效应强的行业
    network_industries = {
        '互联网': 4, '软件': 3, '计算机': 2, '通信': 2, '传媒': 2,
        '社交': 4, '电商': 4, '平台': 4, '金融': 1, '证券': 1,
        '银行': 1, '保险': 1, '支付': 3, '游戏': 2, '物流': 2,
    }
    for ni, pts in network_industries.items():
        if ni in industry:
            score += pts
            reasons.append('「{}」行业具有网络效应特征'.format(industry))
            break
    
    # 营收增长率 -> 网络效应强的公司增长快
    if revenue_growth is not None:
        data_items.append('营收增长率：{:.1f}%'.format(revenue_growth))
        if revenue_growth > 30:
            score += 3
            reasons.append('营收高速增长（>30%），可能受益于网络效应')
        elif revenue_growth > 15:
            score += 2
            reasons.append('营收较快增长（>15%）')
        elif revenue_growth > 0:
            score += 1
            reasons.append('营收正增长')
    else:
        data_items.append('营收增长率数据缺失')
    
    # 高毛利率 -> 平台型公司特征
    if gross_margin is not None:
        data_items.append('毛利率：{:.1f}%'.format(gross_margin))
        if gross_margin > 70 and score > 0:
            score += 2
            reasons.append('高毛利率（>70%）且属于网络效应行业，平台特征明显')
        elif gross_margin > 50 and score > 0:
            score += 1
    
    # 大市值互联网/平台公司
    if market_cap is not None and market_cap > 1000 and score > 0:
        score += 1
        reasons.append('大市值平台型公司，网络效应已形成规模')
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '网络效应护城河衡量公司是否受益于用户越多价值越大的正向循环。互联网、平台型公司通常具有网络效应。',
    }
    
    return score, detail


def _score_switch_cost_moat(data):
    """转换成本护城河评分"""
    industry = data.get('industry', '')
    gross_margin = data.get('gross_margin')
    net_margin = data.get('net_margin')
    revenue_growth = data.get('revenue_growth')
    profit_growth = data.get('profit_growth')
    
    score = 0
    reasons = []
    data_items = []
    
    # 行业属性 -> 转换成本高的行业
    switch_cost_industries = {
        '软件': 3, '计算机': 2, '金融': 2, '银行': 2, '证券': 2,
        '保险': 2, '通信': 2, '医药': 2, '医疗器械': 2, '军工': 2,
        '航天': 2, '高端装备': 2, '半导体': 1, '芯片': 1,
    }
    for si, pts in switch_cost_industries.items():
        if si in industry:
            score += pts
            reasons.append('「{}」行业客户转换成本较高'.format(industry))
            break
    
    # 高毛利率 -> 客户粘性强
    if gross_margin is not None:
        data_items.append('毛利率：{:.1f}%'.format(gross_margin))
        if gross_margin > 60:
            score += 2
            reasons.append('高毛利率（>60%），说明客户对价格不敏感，转换成本高')
        elif gross_margin > 40:
            score += 1
            reasons.append('毛利率较高（>40%）')
    
    # 高净利率 -> 盈利稳定
    if net_margin is not None:
        data_items.append('净利率：{:.1f}%'.format(net_margin))
        if net_margin > 20:
            score += 2
            reasons.append('高净利率（>20%），盈利能力强且稳定')
        elif net_margin > 10:
            score += 1
    
    # 稳定的营收和利润增长 -> 客户留存率高
    if revenue_growth is not None and profit_growth is not None:
        data_items.append('营收增长率：{:.1f}%，净利润增长率：{:.1f}%'.format(revenue_growth, profit_growth))
        if revenue_growth > 0 and profit_growth > 0:
            score += 1
            reasons.append('营收和利润双增长，客户关系稳定')
    
    # To B 行业转换成本更高
    to_b_industries = ['软件', '计算机', '通信', '军工', '航天', '高端装备',
                       '化工', '机械', '电气', '环保']
    for tb in to_b_industries:
        if tb in industry:
            score += 1
            reasons.append('To B 业务模式，客户转换成本较高')
            break
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '转换成本护城河衡量客户更换供应商的难度。高毛利率、高净利率且客户粘性强的公司转换成本高。',
    }
    
    return score, detail


def _score_resource_moat(data):
    """资源垄断护城河评分"""
    industry = data.get('industry', '')
    gross_margin = data.get('gross_margin')
    market_cap = data.get('market_cap')
    
    score = 0
    reasons = []
    data_items = []
    
    # 行业属性 -> 资源垄断型行业
    resource_industries = {
        '能源': 4, '煤炭': 4, '石油': 4, '天然气': 4, '矿业': 4,
        '有色金属': 3, '钢铁': 2, '电力': 3, '水务': 3, '燃气': 3,
        '公用事业': 3, '交通': 2, '港口': 3, '机场': 3, '铁路': 3,
        '高速公路': 2, '稀土': 4, '盐业': 3, '烟草': 4,
    }
    for ri, pts in resource_industries.items():
        if ri in industry:
            score += pts
            reasons.append('「{}」行业具有资源垄断特征'.format(industry))
            break
    
    # 高毛利率 -> 资源稀缺性
    if gross_margin is not None:
        data_items.append('毛利率：{:.1f}%'.format(gross_margin))
        if gross_margin > 50 and score > 0:
            score += 2
            reasons.append('高毛利率（>50%）且属于资源型行业，资源价值高')
        elif gross_margin > 30 and score > 0:
            score += 1
            reasons.append('毛利率较高（>30%）')
    
    # 大市值资源公司
    if market_cap is not None and market_cap > 1000 and score > 0:
        score += 2
        reasons.append('大型资源型企业（>1000亿），资源储备丰富')
    elif market_cap is not None and market_cap > 200 and score > 0:
        score += 1
    
    if market_cap is not None:
        if '市值' not in ''.join(data_items):
            data_items.append('总市值：{:.0f}亿'.format(market_cap))
    
    # 限制最高10分
    score = min(score, 10)
    
    detail = {
        'score': score,
        'max_score': 10,
        'data_items': data_items,
        'reasons': reasons,
        'description': '资源垄断护城河衡量公司是否拥有稀缺资源或特许经营权。能源、矿产、公用事业等行业通常具有资源垄断优势。',
    }
    
    return score, detail


def _analyze_strengths(scores, details):
    """分析优势"""
    strengths = []
    
    # 按得分排序
    sorted_items = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    for name, score in sorted_items:
        if score >= 7:
            detail = details.get(name, {})
            reasons = detail.get('reasons', [])
            reason_text = '；'.join(reasons[:2]) if reasons else '数据支撑充分'
            strengths.append({
                'name': name,
                'score': score,
                'reason': reason_text,
            })
    
    return strengths


def _analyze_weaknesses(scores, details):
    """分析不足"""
    weaknesses = []
    
    sorted_items = sorted(scores.items(), key=lambda x: x[1])
    
    for name, score in sorted_items:
        if score < 4:
            detail = details.get(name, {})
            reasons = detail.get('reasons', [])
            reason_text = '；'.join(reasons[:2]) if reasons else '数据不足或行业特征不明显'
            weaknesses.append({
                'name': name,
                'score': score,
                'reason': reason_text,
            })
    
    return weaknesses


def _generate_advice(total_score, level, strengths, weaknesses):
    """生成投资建议"""
    advice_parts = []
    
    if total_score >= 80:
        advice_parts.append('⭐ 该公司拥有超级护城河，竞争优势极其强大，是长期持有的优质标的。')
        advice_parts.append('这类公司通常具有不可替代的竞争优势，适合作为核心持仓长期持有。')
    elif total_score >= 60:
        advice_parts.append('🔵 该公司拥有宽阔的护城河，竞争优势明显，值得长期关注。')
        advice_parts.append('建议在合理估值时买入，长期持有享受护城河带来的持续回报。')
    elif total_score >= 40:
        advice_parts.append('🟢 该公司有一定的护城河，但还不够深厚。')
        advice_parts.append('需要持续关注护城河是否在加深，建议结合估值和行业前景综合判断。')
    elif total_score >= 20:
        advice_parts.append('🟡 该公司的护城河较浅，竞争优势不够明显。')
        advice_parts.append('投资这类公司需要密切关注行业竞争格局变化，建议以交易性机会为主。')
    else:
        advice_parts.append('❌ 该公司几乎没有护城河，竞争优势很弱。')
        advice_parts.append('这类公司容易被竞争对手超越，投资风险较高，建议谨慎对待。')
    
    # 优势建议
    if strengths:
        best = strengths[0]
        advice_parts.append('')
        advice_parts.append('🏆 最强护城河：{}（{}/10分）- {}'.format(best['name'], best['score'], best['reason']))
    
    # 不足建议
    if weaknesses:
        worst = weaknesses[0]
        advice_parts.append('')
        advice_parts.append('⚠️ 最弱环节：{}（{}/10分）- {}'.format(worst['name'], worst['score'], worst['reason']))
        advice_parts.append('需要关注这些方面的改善情况。')
    
    # 风险提示
    advice_parts.append('')
    advice_parts.append('⚠️ 风险提示：护城河分析基于公开财务数据和行业特征，仅供参考学习，不构成投资建议。')
    advice_parts.append('护城河是定性+定量的结合，不能保证100%准确。投资有风险，入市需谨慎。')
    
    return '\n'.join(advice_parts)


def moat_pipeline(stock_code):
    """护城河两步链打包（Agent 工具入口）：get_moat_analysis_data → calculate_moat_scores。

    只暴露可读结论（scores/level/summary/strengths/weaknesses/advice），
    丢弃含 DataFrame 的 _raw_data。单段失败不阻断，errors 记录原因（Agent 明说"数据不可得"）。

    实现依据：docs/AGENT_MVP_DESIGN.md §3 首批工具清单（P1）。
    """
    result = {
        "stock_code": stock_code,
        "scores": None,
        "total_score": None,
        "level": "--",
        "summary": "",
        "strengths": [],
        "weaknesses": [],
        "advice": "",
        "errors": [],
    }
    try:
        raw_data = get_moat_analysis_data(stock_code)
    except Exception as e:  # noqa: BLE001
        result["errors"].append("护城河数据获取失败：{}".format(e))
        return result
    if not raw_data:
        result["errors"].append("护城河原始数据不可得（可能停牌或接口无返回）")
        return result

    try:
        scores = calculate_moat_scores(raw_data)
        result["scores"] = scores.get("scores")
        result["total_score"] = scores.get("total_score")
        result["level"] = scores.get("level", "--")
        result["summary"] = scores.get("summary", "")
        result["strengths"] = scores.get("strengths") or []
        result["weaknesses"] = scores.get("weaknesses") or []
        result["advice"] = scores.get("advice", "")
    except Exception as e:  # noqa: BLE001
        result["errors"].append("护城河分析失败：{}".format(e))
    return result
