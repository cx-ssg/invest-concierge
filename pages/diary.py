# -*- coding: utf-8 -*-
"""
交易记录页面 - 投资日记
"""
import streamlit as st
from data.database import load_diary, save_diary


def main():
    st.title("📝 交易记录")
    st.markdown("### 投资日记管理")
    
    # 加载日记数据
    entries = load_diary()
    
    if not entries:
        st.info("暂无交易记录")
    else:
        for entry in entries:
            with st.container():
                col1, col2, col3 = st.columns([2, 1, 1])
                with col1:
                    st.write("**{}** - {}".format(entry.get('date', ''), entry.get('fund_name', '')))
                with col2:
                    st.write(entry.get('action', ''))
                with col3:
                    st.write("¥{:,.2f}".format(entry.get('amount', 0)))
                st.caption(entry.get('note', ''))
                st.markdown("---")
    
    st.markdown("---")
    st.caption("⚠️ 风险提示：本工具仅供个人投资参考，不构成投资建议。")


if __name__ == "__main__":
    main()