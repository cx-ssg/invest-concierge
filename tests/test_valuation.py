# -*- coding: utf-8 -*-
"""估值计算器纯函数单测（锚定 data/stock_valuation.py 真实规则）"""
from data.stock_valuation import (
    calculate_pe_valuation,
    calculate_pb_valuation,
    calculate_peg_valuation,
    calculate_dividend_valuation,
    calculate_dcf_valuation,
)


def test_pe_base_on_roe_over_20():
    r = calculate_pe_valuation({'eps': 5.0, 'pe': 20.0, 'price': 90.0, 'roe': 25.0})
    assert r['valid'] is True
    assert r['base_pe'] == 25.0          # roe>20 → 合理PE 25
    assert r['fair_price'] == 125.0      # 5 × 25
    assert r['pe_percentile'] == 80.0    # 20/25×100
    assert r['deviation'] == -28.0       # (90-125)/125×100


def test_pe_industry_override_bank():
    r = calculate_pe_valuation({'eps': 1.0, 'pe': 6.0, 'price': 7.0, 'industry': '银行'})
    assert r['base_pe'] == 8.0           # 银行业固定 8，覆盖 ROE 规则
    assert r['fair_price'] == 8.0
    assert r['deviation'] == -12.5


def test_pe_invalid_when_eps_missing():
    r = calculate_pe_valuation({'pe': 20.0, 'price': 90.0})
    assert r['valid'] is False
    assert 'PE估值法不适用' in r['reason']


def test_pb_base_on_roe():
    r = calculate_pb_valuation({'bvps': 10.0, 'pb': 2.0, 'price': 18.0, 'roe': 15.0})
    assert r['valid'] is True
    assert r['base_pb'] == 3.0           # roe/100×20 = 3.0
    assert r['fair_price'] == 30.0
    assert r['deviation'] == -40.0


def test_pb_clamp_bounds():
    high = calculate_pb_valuation({'bvps': 1.0, 'pb': 10.0, 'price': 10.0, 'roe': 40.0})
    assert high['base_pb'] == 5.0        # 上限 5
    low = calculate_pb_valuation({'bvps': 1.0, 'pb': 10.0, 'price': 10.0, 'roe': 1.0})
    assert low['base_pb'] == 0.5         # 下限 0.5


def test_pb_industry_override_liquor():
    r = calculate_pb_valuation({'bvps': 10.0, 'pb': 8.0, 'price': 60.0, 'roe': 15.0, 'industry': '白酒'})
    assert r['base_pb'] == 5.0           # 白酒业固定 5.0，覆盖 ROE 规则


def test_peg_reasonable():
    r = calculate_peg_valuation({'pe': 30.0, 'profit_growth': 30.0, 'price': 30.0})
    assert r['peg'] == 1.0
    assert r['peg_desc'] == '合理'
    assert r['fair_price'] == 30.0
    assert r['deviation'] == 0.0


def test_peg_deep_undervalued():
    r = calculate_peg_valuation({'pe': 10.0, 'profit_growth': 40.0, 'price': 20.0})
    assert r['peg'] == 0.25
    assert r['peg_desc'] == '极度低估'
    assert r['fair_price'] == 80.0       # 20/0.25


def test_peg_invalid_negative_growth():
    r = calculate_peg_valuation({'pe': 30.0, 'profit_growth': -5.0, 'price': 30.0})
    assert r['valid'] is False
    assert 'PEG估值法不适用' in r['reason']


def test_dividend_risk_premium_by_market_cap():
    r = calculate_dividend_valuation({'dps': 0.5, 'price': 10.0, 'market_cap': 6000})
    assert r['risk_premium'] == 1.5      # 市值>5000 → 风险溢价 1.5%
    assert r['fair_dividend_rate'] == 4.0   # 无风险 2.5% + 1.5%
    assert r['fair_price'] == 12.5       # 0.5 / 4%
    assert r['deviation'] == -20.0


def test_dividend_invalid_when_no_dps():
    r = calculate_dividend_valuation({'price': 10.0})
    assert r['valid'] is False


def test_dcf_structure_and_defaults():
    r = calculate_dcf_valuation({'eps': 1.0, 'price': 10.0, 'market_cap': 0})
    assert r['valid'] is True
    assert r['discount_rate'] == 10.0    # 默认折现率 10%
    assert r['growth_rate_1'] == 5.0     # 未提供增长率时默认 5%
    assert len(r['cashflows']) == 5      # 折现 5 年现金流
    assert r['intrinsic_value'] > 0
    assert isinstance(r['margin_of_safety'], float)


def test_dcf_invalid_when_eps_missing():
    r = calculate_dcf_valuation({'price': 10.0})
    assert r['valid'] is False
