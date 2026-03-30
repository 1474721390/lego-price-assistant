import streamlit as st
import pandas as pd
import re
from datetime import datetime
from supabase import create_client
import os

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

st.set_page_config(page_title="乐高报价系统", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 数据读取 ====================
@st.cache_data(ttl=120)
def get_data():
    try:
        data = supabase.table("price_records").select("*").execute()
        return pd.DataFrame(data.data)
    except:
        return pd.DataFrame()

# ==================== 解析函数 ====================
def parse_line(line):
    line = str(line).strip()
    match_model = re.search(r'(\d{5})', line)
    match_price = re.search(r'(\d+)', line)
    if not match_model or not match_price:
        return None
    model = match_model.group(1)
    price = int(match_price.group(1))
    if price < 10 or price > 9999:
        return None
    return {
        "time": datetime.now().isoformat(),
        "model": model,
        "price": price,
        "remark": ""
    }

# ==================== 界面 ====================
st.title("🧩 乐高报价系统")

# ------------------------------
# 批量录入
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "text" not in st.session_state:
        st.session_state.text = ""

    content = st.text_area("粘贴内容", height=200, value=st.session_state.text)

    if st.button("📌 识别粘贴并保存", type="primary", use_container_width=True):
        if not content:
            st.warning("请先粘贴内容")
            st.stop()

        # 清空
        st.session_state.text = ""
        st.rerun()

        # 解析保存
        save_list = []
        for line in content.splitlines():
            item = parse_line(line)
            if item:
                save_list.append(item)

        if save_list:
            supabase.table("price_records").insert(save_list).execute()
            st.success(f"✅ 已保存 {len(save_list)} 条数据")

        # 恢复内容
        st.session_state.text = content
        st.rerun()

st.divider()

# ------------------------------
# 数据展示
# ------------------------------
df = get_data()
if not df.empty:
    st.subheader("📋 历史数据")
    st.dataframe(df.sort_values("time", ascending=False), use_container_width=True)
else:
    st.info("暂无数据")