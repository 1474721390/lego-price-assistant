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
        res = supabase.table("price_records").select("*").execute()
        return pd.DataFrame(res.data)
    except:
        return pd.DataFrame()

# ==================== 解析 ====================
def parse_line(line):
    line = str(line).strip()
    model_match = re.search(r'(\d{5})', line)
    price_match = re.search(r'(\d+)', line)
    if not model_match or not price_match:
        return None
    model = model_match.group(1)
    price = int(price_match.group(1))
    if price < 10 or price > 9999:
        return None
    return {
        "time": datetime.now().isoformat(),
        "model": model,
        "price": price,
        "remark": ""
    }

# ==================== 收藏 ====================
def get_favorites():
    try:
        res = supabase.table("user_favorites").select("model").execute()
        return [item["model"] for item in res.data]
    except:
        return []

def toggle_fav(model):
    favs = get_favorites()
    if model in favs:
        supabase.table("user_favorites").delete().eq("model", model).execute()
    else:
        supabase.table("user_favorites").insert({"model": model}).execute()

# ==================== 界面 ====================
st.title("🧩 乐高报价系统")

# 搜索
df = get_data()
search = st.text_input("🔍 搜索型号")

st.divider()

# ------------------------------
# 批量录入（你要的功能：只保留一个按钮）
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "input_content" not in st.session_state:
        st.session_state.input_content = ""

    txt = st.text_area("粘贴内容", height=200, value=st.session_state.input_content)

    # 只保留这一个按钮
    if st.button("📌 识别粘贴并保存", type="primary", use_container_width=True):
        content = txt.strip()
        if not content:
            st.warning("⚠️ 请先粘贴内容")
            st.stop()

        # 清空
        st.session_state.input_content = ""
        st.rerun()

        # 解析保存
        save_list = []
        for line in content.splitlines():
            item = parse_line(line)
            if item:
                save_list.append(item)

        if save_list:
            supabase.table("price_records").insert(save_list).execute()
            st.success(f"✅ 已保存 {len(save_list)} 条")

        # 恢复内容
        st.session_state.input_content = content
        st.rerun()

st.divider()

# ------------------------------
# 历史 & 收藏
# ------------------------------
st.subheader("📋 历史数据")
if not df.empty:
    if search:
        df = df[df["model"].str.contains(search, na=False)]
    df_show = df.sort_values("time", ascending=False)
    st.dataframe(df_show, use_container_width=True)

    # 收藏
    st.subheader("⭐ 收藏")
    fav_model = st.text_input("输入型号收藏")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("添加/取消收藏"):
            toggle_fav(fav_model)
            st.rerun()
    with col2:
        favs = get_favorites()
        st.write("已收藏：", " | ".join(favs) if favs else "无")

else:
    st.info("暂无数据")