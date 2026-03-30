import os
import re
import pandas as pd
from datetime import datetime
import streamlit as st
from supabase import create_client

# ==================== 配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")
MODEL_NAME = "glm-4-flash"

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("请配置环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价助手", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 清空缓存，避免卡顿
st.cache_data.clear()

# ==================== 【核心】海量数据优化：只查最新30天 ====================
@st.cache_data(ttl=120, show_spinner=False)
def get_recent_data(days=30):
    # 只查询最近 N 天数据，海量数据也不卡
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    
    all_data = []
    start = 0
    page_size = 1000
    while True:
        res = supabase.table("price_records") \
            .select("*") \
            .gte("time", since) \
            .range(start, start + page_size - 1) \
            .execute()
        data = res.data
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_size:
            break
        start += page_size

    df = pd.DataFrame(all_data)
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r"^[1-9]\d{4}$", na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[df["价格"] > 0]
    return df

# ==================== 只查单个型号（海量数据必备！） ====================
@st.cache_data(ttl=60, show_spinner=False)
def get_model_data(model):
    model = str(model).strip()
    data = supabase.table("price_records") \
        .select("*") \
        .eq("model", model) \
        .order("time") \
        .execute()
    
    df = pd.DataFrame(data.data)
    if df.empty:
        return df
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["价格"])
    return df

# ==================== 保存（支持批量，不卡顿） ====================
def save_to_supabase(data, raw_text):
    if not data:
        return 0
    records = []
    for item in data:
        rec = {
            "time": datetime.now().isoformat(),
            "model": item.get("model"),
            "price": item.get("price"),
            "remark": item.get("remark", ""),
            "raw_text": raw_text if len(records) == 0 else None
        }
        if rec["model"] and rec["price"]:
            records.append(rec)
    if not records:
        return 0
    try:
        res = supabase.table("price_records").insert(records).execute()
        get_recent_data.clear()
        return len(res.data) if res.data else 0
    except:
        return 0

# ==================== 正则提取（100% 省Token） ====================
def extract_remark(line):
    box = ["好盒", "压盒", "瑕疵", "盒损", "破损", "烂盒", "破盒"]
    bag = ["纸袋", "M袋", "礼袋", "礼品袋", "M号袋", "S袋", "+袋", "带袋", "有袋", "无袋"]
    b = next((x for x in box if x in line), None)
    g = next((x for x in bag if x in line), None)
    if b and g:
        return f"{b}+{g}"
    elif b:
        return b
    elif g:
        return "有袋"
    return ""

def extract_with_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    remark = extract_remark(line)
    clean = re.sub(r'好盒|压盒|瑕疵|盒损|破损|烂盒|破盒|纸袋|M袋|礼袋|礼品袋|M号袋|S袋|\+袋|带袋|有袋|无袋', '', line)
    m = re.search(r'(?<![0-9])([1-9]\d{4})(?![0-9])', clean)
    if not m:
        return None, None, None
    model = m.group(1)
    p_clean = clean.replace(model, "")
    p = re.search(r'(\d+)', p_clean)
    if not p:
        return None, None, None
    try:
        price = int(p.group(1))
        return (model, price, remark) if price > 0 else (None, None, None)
    except:
        return None, None, None

def parse_text(text):
    lines = text.strip().splitlines()
    res = []
    for line in lines:
        m, p, r = extract_with_regex(line)
        if m and p:
            res.append({"model": m, "price": p, "remark": r})
    return res

# ==================== 趋势图（只查单个型号，超快） ====================
def show_trend(model):
    if not model:
        return
    df = get_model_data(model)
    if df.empty:
        st.info("无数据")
        return
    import plotly.express as px
    fig = px.line(df, x="时间", y="价格", title=f"{model} 价格趋势", markers=True)
    st.plotly_chart(fig, use_container_width=True)

# ==================== UI 极简极速 ====================
st.title("🧩 乐高报价助手（海量数据版）")

# 型号列表（只从最近数据取，不卡）
df_recent = get_recent_data(days=30)
model_list = sorted(df_recent["型号"].unique()) if not df_recent.empty else []

# 解析保存
st.subheader("输入报价")
user_input = st.text_area("粘贴内容", height=220)
if st.button("🔍 解析并保存"):
    if user_input.strip():
        parsed = parse_text(user_input)
        if parsed:
            cnt = save_to_supabase(parsed, user_input)
            st.success(f"✅ 保存成功 {cnt} 条")
        else:
            st.warning("未识别到有效数据")

# 趋势查询
st.markdown("---")
st.subheader("价格趋势")
sel = st.selectbox("选择型号", [""] + model_list)
show_trend(sel)

st.caption("✅ 海量数据专用 | 每天1万条也不卡 | 正则优先 | 不崩不卡")