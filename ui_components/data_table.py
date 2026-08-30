"""
数据表格组件
可复用的通用数据表格

使用方式:
    from ui_components.data_table import render_data_table, render_fund_table, render_stock_table
    
    # 通用表格
    render_data_table(data, columns, title="我的数据")
    
    # 基金持仓表格（预设格式，适配 database.load_funds 字段）
    render_fund_table(funds_list)
    
    # 股票持仓表格（预设格式，适配 database.get_all_stock_holdings 字段）
    render_stock_table(stocks_list)

列格式类型:
    - 'text':   纯文本
    - 'number': 数字（保留2位小数）
    - 'pct':    百分比（如 12.34%）
    - 'money':  金额（如 ¥1,234.56）

数据兼容:
    - 基金: amount(市值), cost_nav(成本净值), hold_shares(份额)
    - 股票: current_price(现价), cost_price(成本价), shares(持股数)

注意事项:
    - 使用 st.dataframe 渲染，无 DOM 冲突风险
    - 支持搜索过滤和排序功能
    - 空数据显示友好提示，不崩溃
"""
import streamlit as st
import pandas as pd
from typing import List, Dict, Optional, Callable
import logging

logger = logging.getLogger(__name__)


def _format_value(value, format_type: str) -> str:
    """
    格式化单元格值
    
    Args:
        value: 原始值
        format_type: 格式类型 ('text', 'number', 'pct', 'money')
    
    Returns:
        格式化后的字符串
    
    示例:
        _format_value(1234.5678, 'money') -> "¥1,234.57"
        _format_value(0.1234, 'pct')      -> "12.34%"
        _format_value(1234.5678, 'number') -> "1234.57"
    """
    if value is None:
        return "--"
    try:
        if format_type == 'money':
            return "¥{:,.2f}".format(float(value))
        elif format_type == 'pct':
            return "{:.2f}%".format(float(value))
        elif format_type == 'number':
            return "{:.2f}".format(float(value))
        else:
            return str(value)
    except (ValueError, TypeError) as e:
        logger.warning("格式化值失败: value={}, format={}, error={}".format(value, format_type, e))
        return str(value)


def render_data_table(
    data: List[Dict],
    columns: List[Dict],
    title: Optional[str] = None,
    sortable: bool = True,
    searchable: bool = True,
    page_size: int = 10
):
    """
    渲染通用数据表格
    
    Args:
        data: 数据列表，每项为一个字典
        columns: 列定义列表，每项格式:
            {'key': 'name', 'label': '名称', 'format': 'text'}
            支持的 format: text, number, pct, money
        title: 表格标题（可选）
        sortable: 是否允许排序（默认True）
        searchable: 是否显示搜索框（默认True）
        page_size: 每页显示行数（默认10）
    
    示例:
        data = [{'name': '易方达蓝筹', 'nav': 2.3456, 'rate': 5.67}]
        columns = [
            {'key': 'name', 'label': '名称', 'format': 'text'},
            {'key': 'nav', 'label': '净值', 'format': 'money'},
            {'key': 'rate', 'label': '收益率', 'format': 'pct'},
        ]
        render_data_table(data, columns, title="基金列表")
    """
    try:
        if not data:
            st.info("暂无数据")
            return

        # 转换为DataFrame
        df = pd.DataFrame(data)

        # 取需要的列并重命名
        col_keys = [col['key'] for col in columns]
        column_map = {col['key']: col['label'] for col in columns}
        display_df = df[col_keys].rename(columns=column_map)

        if title:
            st.markdown("### {}".format(title))

        # 搜索框
        if searchable:
            search_term = st.text_input(
                "🔍 搜索",
                placeholder="输入关键词过滤...",
                key="dt_search_{}".format(title or 'table')
            )
            if search_term:
                mask = display_df.astype(str).apply(
                    lambda x: x.str.contains(search_term, case=False, na=False)
                ).any(axis=1)
                display_df = display_df[mask]

        # 构造列配置
        col_config = {}
        for col in columns:
            label = col['label']
            fmt = col.get('format', 'text')
            if fmt == 'number':
                col_config[label] = st.column_config.NumberColumn(format="%.2f")
            elif fmt == 'pct':
                col_config[label] = st.column_config.NumberColumn(format="%.2f%%")
            elif fmt == 'money':
                col_config[label] = st.column_config.NumberColumn(format="¥%.2f")
            else:
                col_config[label] = st.column_config.TextColumn()

        # 显示表格
        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config=col_config
        )

        # 显示统计
        st.caption("共 {} 条记录".format(len(display_df)))

    except Exception as e:
        logger.error("渲染数据表格失败: {}".format(e))
        st.error("表格渲染失败: {}".format(e))


def render_fund_table(funds: List[Dict]):
    """
    渲染基金持仓表格（预设格式，适配 database.load_funds 字段）
    
    Args:
        funds: 基金持仓列表（来自 database.load_funds 的 values）
            每项需包含: name, code, amount, cost_nav, hold_shares
    
    显示列:
        名称, 代码, 当前净值, 成本净值, 持有份额, 持仓市值, 盈亏, 收益率
    
    示例:
        funds_dict = load_funds()
        funds = list(funds_dict.values())
        render_fund_table(funds)
    """
    try:
        if not funds:
            st.info("暂无基金持仓数据")
            return

        rows = []
        for f in funds:
            amount = f.get('amount', 0)
            cost_nav = f.get('cost_nav', 0)
            shares = f.get('hold_shares', 0)
            nav = amount / shares if shares > 0 else 0
            cost = cost_nav * shares
            profit = amount - cost
            profit_rate = (profit / cost * 100) if cost > 0 else 0

            rows.append({
                'name': f.get('name', '--'),
                'code': f.get('code', '--'),
                'nav': nav,
                'cost_nav': cost_nav,
                'hold_shares': shares,
                'amount': amount,
                'profit': profit,
                'profit_rate': profit_rate,
            })

        columns = [
            {'key': 'name', 'label': '名称', 'format': 'text'},
            {'key': 'code', 'label': '代码', 'format': 'text'},
            {'key': 'nav', 'label': '当前净值', 'format': 'money'},
            {'key': 'cost_nav', 'label': '成本净值', 'format': 'money'},
            {'key': 'hold_shares', 'label': '持有份额', 'format': 'number'},
            {'key': 'amount', 'label': '持仓市值', 'format': 'money'},
            {'key': 'profit', 'label': '盈亏', 'format': 'money'},
            {'key': 'profit_rate', 'label': '收益率', 'format': 'pct'},
        ]

        render_data_table(rows, columns, title="📋 基金持仓明细")

    except Exception as e:
        logger.error("渲染基金持仓表格失败: {}".format(e))
        st.error("基金持仓表格渲染失败: {}".format(e))


def render_stock_table(stocks: List[Dict]):
    """
    渲染股票持仓表格（预设格式，适配 database.get_all_stock_holdings 字段）
    
    Args:
        stocks: 股票持仓列表（来自 database.get_all_stock_holdings）
            每项需包含: name, code, (current_price/price), (cost_price/cost), (shares/hold_shares)
    
    显示列:
        名称, 代码, 现价, 成本价, 持股数, 持仓市值, 盈亏, 收益率
    
    示例:
        stocks = get_all_stock_holdings()
        render_stock_table(stocks)
    """
    try:
        if not stocks:
            st.info("暂无股票持仓数据")
            return

        rows = []
        for s in stocks:
            amount = s.get('amount', s.get('market_value', 0))
            cost_price = s.get('cost_price', s.get('cost', 0))
            current_price = s.get('current_price', s.get('price', 0))
            shares = s.get('shares', s.get('hold_shares', 0))
            cost = cost_price * shares
            profit = (current_price - cost_price) * shares
            profit_rate = (profit / cost * 100) if cost > 0 else 0

            rows.append({
                'name': s.get('name', '--'),
                'code': s.get('code', '--'),
                'current_price': current_price,
                'cost_price': cost_price,
                'shares': shares,
                'amount': amount,
                'profit': profit,
                'profit_rate': profit_rate,
            })

        columns = [
            {'key': 'name', 'label': '名称', 'format': 'text'},
            {'key': 'code', 'label': '代码', 'format': 'text'},
            {'key': 'current_price', 'label': '现价', 'format': 'money'},
            {'key': 'cost_price', 'label': '成本价', 'format': 'money'},
            {'key': 'shares', 'label': '持股数', 'format': 'number'},
            {'key': 'amount', 'label': '持仓市值', 'format': 'money'},
            {'key': 'profit', 'label': '盈亏', 'format': 'money'},
            {'key': 'profit_rate', 'label': '收益率', 'format': 'pct'},
        ]

        render_data_table(rows, columns, title="📋 股票持仓明细")

    except Exception as e:
        logger.error("渲染股票持仓表格失败: {}".format(e))
        st.error("股票持仓表格渲染失败: {}".format(e))