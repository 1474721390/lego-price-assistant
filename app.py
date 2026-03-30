import os
import re
import pandas as pd
from datetime import datetime
import streamlit as st
import plotly.express as px
from supabase import create_client

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY]):
    st.error("❌ 请配置环境变量")
    st.stop()

# 正常UI，不宽屏、不大屏
st.set_page_config(page_title="乐高报价系统", layout="centered")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 工具函数 ====================
def extract_remark(line):
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒", "全新", "微压"]
    bag_keywords = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋", "袋子"]
    box = None
    for b in box_keywords:
        if b in line:
            box = b
            break
    bag = None
    for bg in bag_keywords:
        if bg in line:
            bag = bg
            break
    if box and bag:
        return f"{box}+{bag}"
    elif box:
        return box
    elif bag:
        return bag
    else:
        return ""

def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    remark = extract_remark(line)
    model_match = re.search(r'(?<![0-9])(?:LG-)?([1-9]\d{4})(?![0-9])', line)
    price_match = re.search(r'(\d+)', line)
    if not model_match or not price_match:
        return None, None, None
    model = model_match.group(1)
    try:
        price = int(price_match.group(1))
    except:
        return None, None, None
    return model, price, remark

# ==================== 数据库 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except:
        return None

@st.cache_data(ttl=60)
def get_data():
    res = supabase.table("price_records").select("*").execute()
    if not res.data:
        return pd.DataFrame()
    df = pd.DataFrame(res.data)
    df["时间"] = pd.to_datetime(df["time"]).dt.strftime("%m-%d %H:%M")
    return df

# ==================== 主界面 ====================
st.title("🧩 乐高报价系统")

# 初始化状态
if "parse_result" not in st.session_state:
    st.session_state.parse_result = pd.DataFrame()

# 批量录入区域
st.subheader("📝 批量录入")
txt = st.text_area("粘贴内容", height=120)

# 仅保留有用按钮
col1, col2 = st.columns(2)
with col1:
    if st.button("🔍 解析并保存", type="primary", use_container_width=True):
        if not txt:
            st.warning("请粘贴内容")
            st.stop()
        
        lines = txt.strip().splitlines()
        res = []
        save_list = []
        
        for li in lines:
            m, p, r = extract_by_regex(li)
            if m and p:
                res.append({"型号": m, "价格": p, "备注": r, "原始": li, "状态": "✅ 有效"})
                save_list.append({
                    "time": datetime.now().isoformat(),
                    "model": m,
                    "price": p,
                    "remark": r
                })
            else:
                res.append({"型号": "", "价格": "", "备注": "", "原始": li, "状态": "❌ 失败"})
        
        st.session_state.parse_result = pd.DataFrame(res)
        
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 保存成功 {len(save_list)} 条")
            get_data.clear()

with col2:
    if st.button("🗑️ 清空所有", use_container_width=True):
        st.session_state.parse_result = pd.DataFrame()
        st.rerun()

# 解析结果 + 修改保存按钮
if not st.session_state.parse_result.empty:
    st.dataframe(st.session_state.parse_result, use_container_width=True, height=200)
    
    if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True):
        ok_list = []
        for _, row in st.session_state.parse_result.iterrows():
            if row["型号"] and row["价格"] and "✅" in row["状态"]:
                ok_list.append({
                    "time": datetime.now().isoformat(),
                    "model": str(row["型号"]).strip(),
                    "price": int(row["价格"]),
                    "remark": str(row["备注"]).strip()
                })
        if ok_list:
            save_batch(ok_list)
            st.success(f"✅ 修改保存成功 {len(ok_list)} 条")
            st.session_state.parse_result = pd.DataFrame()
            get_data.clear()
            st.rerun()

st.divider()

# 历史数据
st.subheader("📋 历史数据")
df = get_data()
if not df.empty:
    models = sorted(df["model"].unique())
    selected = st.selectbox("选择型号", models)
    show_df = df[df["model"] == selected][["时间", "model", "price", "remark"]]
    show_df.columns = ["时间", "型号", "价格", "备注"]
    st.dataframe(show_df, use_container_width=True, height=200)
else:
    st.info("暂无数据")