# -*- coding: utf-8 -*-
"""
智能估值计算器 - 多种估值方法计算股票合理价值
"""

from data.cache import cached, CACHE_VALUATION
from data.stock_api import get_stock_info
from data.stock_fundamentals import get_stock_financial_data


@cached(CACHE_VALUATION)
def get_valuation_data(stock_code):
    """获取估值所需的全部数据（估值数据缓存 24 小时）

    数据来源：新浪财经 + AkShare（行情/财务）
    """
    result = {
        'stock_info': None,
        'financial_data': None,
        'eps': None,           # 每股收益
        'bvps': None,          # 每股净资产
        'dps': None,           # 每股股息
        'profit_growth': None, # 净利润增长率(%)
        'revenue_growth': None,# 营收增长率(%)
        'pe': None,            # 市盈率
        'pb': None,            # 市净率
        'roe': None,           # 净资产收益率(%)
        'dividend_rate': None, # 股息率(%)
        'market_cap': None,    # 总市值(亿)
        'industry': None,      # 行业
        'stock_name': None,    # 股票名称
        'price': None,         # 当前股价
    }

    # 1. 获取实时行情
    stock_info = get_stock_info(stock_code)
    if not stock_info:
        return None
    result['stock_info'] = stock_info
    result['stock_name'] = stock_info.get('name', '')
    result['price'] = stock_info.get('price', 0)
    result['pe'] = stock_info.get('pe', 0)
    result['pb'] = stock_info.get('pb', 0)
    market_cap = stock_info.get('total_market_cap', 0)
    result['market_cap'] = market_cap / 100000000 if market_cap else 0

    # 2. 获取财务数据
    financial_data = get_stock_financial_data(stock_code)
    result['financial_data'] = financial_data
    result['profit_growth'] = financial_data.get('profit_growth')
    result['revenue_growth'] = financial_data.get('revenue_growth')
    result['roe'] = financial_data.get('roe')
    result['dividend_rate'] = financial_data.get('dividend_rate')
    result['industry'] = financial_data.get('industry')

    # 3. 计算每股收益 EPS = 股价 / PE
    pe = result['pe']
    price = result['price']
    if pe and pe > 0 and price and price > 0:
        result['eps'] = price / pe

    # 4. 计算每股净资产 BVPS = 股价 / PB
    pb = result['pb']
    if pb and pb > 0 and price and price > 0:
        result['bvps'] = price / pb

    # 5. 计算每股股息 DPS = 股价 * 股息率
    dividend_rate = result['dividend_rate']
    if dividend_rate and dividend_rate > 0 and price and price > 0:
        result['dps'] = price * dividend_rate / 100

    return result


def calculate_pe_valuation(data):
    """PE估值法 - 适用于盈利稳定的公司
    
    计算逻辑：
    - 合理PE = 行业平均PE 或 历史中位PE（这里用无风险利率倒数作为基准）
    - 合理股价 = 每股收益 × 合理PE
    """
    eps = data.get('eps')
    pe = data.get('pe')
    price = data.get('price')

    if not eps or eps <= 0:
        return {
            'valid': False,
            'reason': '每股收益(EPS)数据缺失或为负，PE估值法不适用',
        }

    if not pe or pe <= 0:
        return {
            'valid': False,
            'reason': '市盈率(PE)数据缺失，PE估值法不适用',
        }

    # 基准合理PE：以中国10年期国债收益率约2.5%为基准，合理PE ≈ 1/2.5% = 40
    # 但考虑到A股市场风险溢价，取20-25为合理中枢
    # 根据ROE调整：ROE越高，可给予更高PE
    base_pe = 20.0
    roe = data.get('roe')
    if roe and roe > 0:
        if roe > 20:
            base_pe = 25.0
        elif roe > 15:
            base_pe = 22.0
        elif roe > 10:
            base_pe = 20.0
        elif roe > 5:
            base_pe = 15.0
        else:
            base_pe = 12.0

    # 根据行业调整（简化处理）
    industry = data.get('industry', '')
    industry_pe_map = {
        '白酒': 30, '食品': 28, '医药': 28, '科技': 25,
        '消费': 25, '新能源': 25, '半导体': 30, '银行': 8,
        '保险': 12, '证券': 15, '地产': 10, '钢铁': 10,
        '煤炭': 10, '化工': 15, '汽车': 18, '家电': 18,
        '通信': 20, '计算机': 28, '电子': 25, '传媒': 22,
        '军工': 25, '机械': 18, '建筑': 10, '公用事业': 15,
    }
    for key, val in industry_pe_map.items():
        if key in industry:
            base_pe = val
            break

    # 合理股价
    fair_price = round(eps * base_pe, 2)

    # 当前PE分位（相对合理PE的百分比）
    pe_percentile = round(pe / base_pe * 100, 1) if base_pe > 0 else 0

    # 偏离度
    if price and price > 0:
        deviation = round((price - fair_price) / fair_price * 100, 1)
    else:
        deviation = 0

    return {
        'valid': True,
        'method_name': 'PE估值法（市盈率法）',
        'applicable': '盈利稳定的公司',
        'eps': round(eps, 3),
        'current_pe': round(pe, 2),
        'base_pe': base_pe,
        'fair_price': fair_price,
        'pe_percentile': pe_percentile,
        'deviation': deviation,
        'formula': '合理股价 = 每股收益({:.3f}) × 合理PE({:.1f}) = {:.2f}'.format(
            eps, base_pe, fair_price),
        'description': '基于公司盈利能力和行业平均估值水平，计算合理市盈率倍数',
    }


def calculate_pb_valuation(data):
    """PB估值法 - 适用于重资产、金融、周期股
    
    计算逻辑：
    - 合理PB = 行业平均PB 或 历史中位PB
    - 合理股价 = 每股净资产 × 合理PB
    """
    bvps = data.get('bvps')
    pb = data.get('pb')
    price = data.get('price')
    roe = data.get('roe')

    if not bvps or bvps <= 0:
        return {
            'valid': False,
            'reason': '每股净资产(BVPS)数据缺失或为负，PB估值法不适用',
        }

    if not pb or pb <= 0:
        return {
            'valid': False,
            'reason': '市净率(PB)数据缺失，PB估值法不适用',
        }

    # 基准合理PB
    # PB = ROE * 合理PE / 100（简化关系）
    base_pb = 1.5
    if roe and roe > 0:
        # 用ROE估算合理PB：合理PB = ROE% * 1.2（简化）
        base_pb = round(roe / 100 * 20, 1)
        base_pb = max(0.5, min(base_pb, 5.0))  # 限制在0.5-5之间

    # 行业调整
    industry = data.get('industry', '')
    industry_pb_map = {
        '银行': 0.8, '保险': 1.2, '证券': 1.5, '地产': 0.8,
        '钢铁': 0.8, '煤炭': 1.0, '建筑': 0.8, '公用事业': 1.2,
        '白酒': 5.0, '食品': 3.0, '医药': 3.0, '科技': 3.0,
    }
    for key, val in industry_pb_map.items():
        if key in industry:
            base_pb = val
            break

    # 合理股价
    fair_price = round(bvps * base_pb, 2)

    # 偏离度
    if price and price > 0:
        deviation = round((price - fair_price) / fair_price * 100, 1)
    else:
        deviation = 0

    return {
        'valid': True,
        'method_name': 'PB估值法（市净率法）',
        'applicable': '重资产、金融、周期股',
        'bvps': round(bvps, 3),
        'current_pb': round(pb, 2),
        'base_pb': base_pb,
        'fair_price': fair_price,
        'deviation': deviation,
        'formula': '合理股价 = 每股净资产({:.3f}) × 合理PB({:.1f}) = {:.2f}'.format(
            bvps, base_pb, fair_price),
        'description': '基于公司净资产和行业平均市净率水平，计算合理估值',
    }


def calculate_peg_valuation(data):
    """PEG估值法 - 适用于成长股
    
    计算逻辑：
    - PEG = PE / 净利润增长率
    - PEG = 1 为合理
    - 合理股价 = 当前股价 / PEG
    """
    pe = data.get('pe')
    profit_growth = data.get('profit_growth')
    price = data.get('price')

    if not pe or pe <= 0:
        return {
            'valid': False,
            'reason': '市盈率(PE)数据缺失或为负，PEG估值法不适用',
        }

    if profit_growth is None or profit_growth <= 0:
        return {
            'valid': False,
            'reason': '净利润增长率数据缺失或为负，PEG估值法不适用',
        }

    if not price or price <= 0:
        return {
            'valid': False,
            'reason': '当前股价数据缺失',
        }

    # 计算PEG
    peg = round(pe / profit_growth, 2)

    # PEG = 1 为合理
    # 合理股价 = 当前股价 / PEG（当PEG>0时）
    if peg > 0:
        fair_price = round(price / peg, 2)
    else:
        fair_price = price

    # 偏离度
    deviation = round((price - fair_price) / fair_price * 100, 1) if fair_price > 0 else 0

    # PEG评估
    if peg <= 0.5:
        peg_desc = "极度低估"
    elif peg <= 0.8:
        peg_desc = "低估"
    elif peg <= 1.2:
        peg_desc = "合理"
    elif peg <= 2.0:
        peg_desc = "高估"
    else:
        peg_desc = "极度高估"

    return {
        'valid': True,
        'method_name': 'PEG估值法',
        'applicable': '成长股（高增长公司）',
        'profit_growth': round(profit_growth, 2),
        'current_pe': round(pe, 2),
        'peg': peg,
        'peg_desc': peg_desc,
        'fair_price': fair_price,
        'deviation': deviation,
        'formula': 'PEG = PE({:.2f}) / 净利润增长率({:.2f}%) = {:.2f}\n合理股价 = 当前股价({:.2f}) / PEG({:.2f}) = {:.2f}'.format(
            pe, profit_growth, peg, price, peg, fair_price),
        'description': 'PEG=1为合理，<1低估，>1高估。适用于净利润增长率稳定的成长型公司',
    }


def calculate_dividend_valuation(data):
    """股息估值法 - 适用于高股息、稳定分红的公司
    
    计算逻辑：
    - 合理股息率 = 无风险利率 + 风险溢价
    - 合理股价 = 每股股息 / 合理股息率
    """
    dps = data.get('dps')
    dividend_rate = data.get('dividend_rate')
    price = data.get('price')

    if not dps or dps <= 0:
        return {
            'valid': False,
            'reason': '每股股息(DPS)数据缺失或为0，股息估值法不适用（可能不分红）',
        }

    if not price or price <= 0:
        return {
            'valid': False,
            'reason': '当前股价数据缺失',
        }

    # 无风险利率（中国10年期国债收益率约2.5%）
    risk_free_rate = 2.5

    # 风险溢价：根据市值和行业调整
    risk_premium = 2.0
    market_cap = data.get('market_cap', 0)
    if market_cap > 5000:
        risk_premium = 1.5  # 大盘股风险较低
    elif market_cap > 1000:
        risk_premium = 2.0
    elif market_cap > 100:
        risk_premium = 2.5
    else:
        risk_premium = 3.0  # 小盘股风险较高

    # 合理股息率
    fair_dividend_rate = risk_free_rate + risk_premium

    # 合理股价 = 每股股息 / 合理股息率
    fair_price = round(dps / (fair_dividend_rate / 100), 2)

    # 偏离度
    deviation = round((price - fair_price) / fair_price * 100, 1) if fair_price > 0 else 0

    return {
        'valid': True,
        'method_name': '股息估值法（股息率法）',
        'applicable': '高股息、稳定分红的公司（如银行、公用事业）',
        'dps': round(dps, 3),
        'dividend_rate': round(dividend_rate, 2) if dividend_rate else 0,
        'risk_free_rate': risk_free_rate,
        'risk_premium': risk_premium,
        'fair_dividend_rate': round(fair_dividend_rate, 1),
        'fair_price': fair_price,
        'deviation': deviation,
        'formula': '合理股息率 = 无风险利率({:.1f}%) + 风险溢价({:.1f}%) = {:.1f}%\n合理股价 = 每股股息({:.4f}) / 合理股息率({:.1f}%) = {:.2f}'.format(
            risk_free_rate, risk_premium, fair_dividend_rate,
            dps, fair_dividend_rate, fair_price),
        'description': '基于股息贴现模型，将未来股息按合理股息率折现。适用于分红稳定的成熟公司',
    }


def calculate_dcf_valuation(data):
    """DCF简化估值法（现金流折现）- 适用于现金流稳定的公司
    
    简化计算：
    - 未来3年自由现金流按增长率预测
    - 折现率：8-10%
    - 永续增长率：2-3%
    - 计算内在价值
    """
    eps = data.get('eps')
    profit_growth = data.get('profit_growth')
    price = data.get('price')

    if not eps or eps <= 0:
        return {
            'valid': False,
            'reason': '每股收益(EPS)数据缺失或为负，DCF估值法不适用',
        }

    if not price or price <= 0:
        return {
            'valid': False,
            'reason': '当前股价数据缺失',
        }

    # 用EPS近似自由现金流
    base_fcf = eps

    # 增长率假设
    if profit_growth and profit_growth > 0:
        growth_rate_1 = profit_growth / 100  # 第1-3年增长率
    else:
        growth_rate_1 = 0.05  # 默认5%

    growth_rate_2 = 0.03   # 第4-5年增长率（衰减）
    terminal_growth = 0.02 # 永续增长率

    # 折现率
    discount_rate = 0.10  # 10%
    market_cap = data.get('market_cap', 0)
    if market_cap > 5000:
        discount_rate = 0.08  # 大盘股风险较低
    elif market_cap > 1000:
        discount_rate = 0.09

    # 计算未来现金流
    cashflows = []
    fcf = base_fcf
    for year in range(1, 6):
        if year <= 3:
            fcf *= (1 + growth_rate_1)
        else:
            fcf *= (1 + growth_rate_2)
        discounted_fcf = fcf / ((1 + discount_rate) ** year)
        cashflows.append({
            'year': year,
            'fcf': round(fcf, 3),
            'discounted_fcf': round(discounted_fcf, 3),
        })

    # 终值计算
    terminal_fcf = fcf * (1 + terminal_growth) / (discount_rate - terminal_growth)
    terminal_value = terminal_fcf / ((1 + discount_rate) ** 5)

    # 内在价值 = 现金流折现之和 + 终值折现
    total_discounted = sum(c['discounted_fcf'] for c in cashflows)
    intrinsic_value = round(total_discounted + terminal_value, 2)

    # 安全边际
    if price and price > 0 and intrinsic_value > 0:
        margin_of_safety = round((intrinsic_value - price) / intrinsic_value * 100, 1)
    else:
        margin_of_safety = 0

    # 偏离度
    deviation = round((price - intrinsic_value) / intrinsic_value * 100, 1) if intrinsic_value > 0 else 0

    return {
        'valid': True,
        'method_name': 'DCF简化估值法（现金流折现）',
        'applicable': '现金流稳定的成熟公司',
        'base_fcf': round(base_fcf, 3),
        'growth_rate_1': round(growth_rate_1 * 100, 1),
        'growth_rate_2': round(growth_rate_2 * 100, 1),
        'terminal_growth': round(terminal_growth * 100, 1),
        'discount_rate': round(discount_rate * 100, 1),
        'cashflows': cashflows,
        'terminal_value': round(terminal_value, 2),
        'intrinsic_value': intrinsic_value,
        'margin_of_safety': margin_of_safety,
        'deviation': deviation,
        'formula': 'DCF内在价值 = 未来5年现金流折现之和 + 终值折现\n折现率：{:.1f}%，永续增长率：{:.1f}%'.format(
            discount_rate * 100, terminal_growth * 100),
        'description': '将未来自由现金流按折现率折现，加上终值计算内在价值。适用于现金流可预测的稳定公司',
    }


@cached(CACHE_VALUATION)
def calculate_comprehensive_valuation(stock_code):
    """综合估值 - 使用多种估值方法计算综合合理价格（缓存 24 小时）"""
    # 获取数据
    data = get_valuation_data(stock_code)
    if not data:
        return None

    price = data.get('price', 0)

    # 执行各种估值方法
    results = []

    # 1. PE估值
    pe_result = calculate_pe_valuation(data)
    if pe_result['valid']:
        results.append(pe_result)

    # 2. PB估值
    pb_result = calculate_pb_valuation(data)
    if pb_result['valid']:
        results.append(pb_result)

    # 3. PEG估值
    peg_result = calculate_peg_valuation(data)
    if peg_result['valid']:
        results.append(peg_result)

    # 4. 股息估值
    div_result = calculate_dividend_valuation(data)
    if div_result['valid']:
        results.append(div_result)

    # 5. DCF估值
    dcf_result = calculate_dcf_valuation(data)
    if dcf_result['valid']:
        results.append(dcf_result)

    # 计算综合合理价格
    if results:
        fair_prices = [r['fair_price'] for r in results if r.get('fair_price', 0) > 0]
        if fair_prices:
            # 去掉最高和最低，取中间值的平均
            if len(fair_prices) >= 3:
                sorted_prices = sorted(fair_prices)
                trimmed = sorted_prices[1:-1]
                avg_fair_price = round(sum(trimmed) / len(trimmed), 2)
                median_fair_price = sorted_prices[len(sorted_prices) // 2]
            else:
                avg_fair_price = round(sum(fair_prices) / len(fair_prices), 2)
                median_fair_price = sorted(fair_prices)[0] if fair_prices else 0
        else:
            avg_fair_price = 0
            median_fair_price = 0
    else:
        avg_fair_price = 0
        median_fair_price = 0

    # 估值结论
    if avg_fair_price > 0 and price > 0:
        price_ratio = price / avg_fair_price * 100

        if price_ratio < 70:
            valuation_status = "严重低估"
            valuation_color = "#10B981"
            suggestion = "强烈买入"
            suggestion_level = 5
        elif price_ratio < 90:
            valuation_status = "低估"
            valuation_color = "#34D399"
            suggestion = "买入"
            suggestion_level = 4
        elif price_ratio < 110:
            valuation_status = "合理"
            valuation_color = "#FBBF24"
            suggestion = "持有"
            suggestion_level = 3
        elif price_ratio < 150:
            valuation_status = "高估"
            valuation_color = "#FF922B"
            suggestion = "卖出"
            suggestion_level = 2
        else:
            valuation_status = "严重高估"
            valuation_color = "#EF4444"
            suggestion = "强烈卖出"
            suggestion_level = 1

        margin_of_safety = round((avg_fair_price - price) / avg_fair_price * 100, 1)
    else:
        valuation_status = "数据不足"
        valuation_color = "#9CA3AF"
        suggestion = "无法判断"
        suggestion_level = 0
        margin_of_safety = 0
        price_ratio = 0

    # 买入建议价（合理价的80%）
    buy_price = round(avg_fair_price * 0.8, 2) if avg_fair_price > 0 else 0
    # 卖出建议价（合理价的130%）
    sell_price = round(avg_fair_price * 1.3, 2) if avg_fair_price > 0 else 0

    return {
        'data': data,
        'price': price,
        'results': results,
        'avg_fair_price': avg_fair_price,
        'median_fair_price': median_fair_price,
        'price_ratio': round(price_ratio, 1) if price_ratio else 0,
        'valuation_status': valuation_status,
        'valuation_color': valuation_color,
        'suggestion': suggestion,
        'suggestion_level': suggestion_level,
        'margin_of_safety': margin_of_safety,
        'buy_price': buy_price,
        'sell_price': sell_price,
        'valid_methods': len(results),
        'total_methods': 5,
    }