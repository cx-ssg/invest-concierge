# -*- coding: utf-8 -*-
"""财务排雷 8 大雷区规则单测（锚定 data/financial_minefield.py 真实阈值）"""
from data.financial_minefield import (
    check_minefields,
    calculate_risk_rating,
    get_risk_items,
    get_safe_items,
)

REQUIRED_KEYS = {'id', 'name', 'level', 'is_risk', 'data_available',
                 'detail', 'explanation', 'suggestion'}


def test_check_minefields_returns_8_items():
    results = check_minefields({})
    assert len(results) == 8
    ids = [r['id'] for r in results]
    assert len(set(ids)) == 8            # 8 个雷区 id 互不重复
    for r in results:
        assert REQUIRED_KEYS <= set(r.keys())


def test_goodwill_over_30_percent_triggers():
    data = {'goodwill': 4e8, 'net_assets': 1e9}   # 商誉4亿 / 净资产10亿 = 40%
    r = [x for x in check_minefields(data) if x['id'] == 'goodwill'][0]
    assert r['is_risk'] is True
    assert '警戒线' in r['detail']


def test_goodwill_below_threshold_safe():
    data = {'goodwill': 1e8, 'net_assets': 1e9}   # 10%，低于 30% 警戒线
    r = [x for x in check_minefields(data) if x['id'] == 'goodwill'][0]
    assert r['is_risk'] is False


def test_goodwill_missing_data_marked_unavailable():
    r = [x for x in check_minefields({'net_assets': 1e9}) if x['id'] == 'goodwill'][0]
    assert r['data_available'] is False
    assert r['is_risk'] is False


def test_deposit_loan_both_over_30_triggers():
    data = {'monetary_capital': 4e9, 'total_assets': 1e10, 'interest_bearing_debt': 4e9}
    r = [x for x in check_minefields(data) if x['id'] == 'deposit_loan_high'][0]
    assert r['is_risk'] is True          # 存 40% 且贷 40%，双高


def test_deposit_loan_single_side_high_safe():
    data = {'monetary_capital': 4e9, 'total_assets': 1e10, 'interest_bearing_debt': 1e9}
    r = [x for x in check_minefields(data) if x['id'] == 'deposit_loan_high'][0]
    assert r['is_risk'] is False         # 只有存>30%，贷未超，不触发


def _mk(idx, level='高', is_risk=False, available=True):
    return {'id': 'x{}'.format(idx), 'name': 'x', 'level': level,
            'is_risk': is_risk, 'data_available': available, 'detail': '',
            'explanation': '', 'suggestion': ''}


def test_rating_all_safe():
    rating = calculate_risk_rating([_mk(i) for i in range(3)])
    assert rating['level'] == '✅ 安全'
    assert rating['safe_count'] == 3


def test_rating_two_high_is_high_risk():
    rating = calculate_risk_rating([_mk(1, '高', True), _mk(2, '高', True), _mk(3)])
    assert rating['level'] == '🔴 高风险'
    assert rating['high_count'] == 2


def test_rating_one_high_is_elevated():
    rating = calculate_risk_rating([_mk(1, '高', True), _mk(2)])
    assert rating['level'] == '🟠 较高风险'


def test_rating_two_medium_is_elevated():
    rating = calculate_risk_rating([_mk(1, '中', True), _mk(2, '中', True)])
    assert rating['level'] == '🟠 较高风险'


def test_rating_one_medium_is_medium():
    rating = calculate_risk_rating([_mk(1, '中', True)])
    assert rating['level'] == '🟡 中风险'


def test_rating_three_low_is_medium():
    rating = calculate_risk_rating([_mk(i, '低', True) for i in range(1, 4)])
    assert rating['level'] == '🟡 中风险'   # 3 个低危升为中风险


def test_rating_single_low_is_low():
    rating = calculate_risk_rating([_mk(1, '低', True)])
    assert rating['level'] == '🟢 低风险'


def test_risk_and_safe_filters():
    results = [_mk(1, '高', True), _mk(2), _mk(3, '低', False, available=False)]
    assert len(get_risk_items(results)) == 1
    assert len(get_safe_items(results)) == 1   # data_available=False 不计入安全项
