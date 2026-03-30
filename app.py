import os
import re
import json
import requests
import pandas as pd
from datetime import datetime
import streamlit as st
import plotly.express as px
from supabase import create_client

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请配置环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价系统", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 收藏功能 ====================
def get_favorites():
    res = supabase.table("user_favorites").select("model").execute()
    return {item["model"] for item in res.data} if res.data else set()

def toggle_favorite(model):
    favs = get_favorites()
    if model in favs:
        supabase.table("user_favorites").delete().eq("model", model).execute()
    else:
        supabase.table("user_favorites").insert({"model": model}).execute()
    get_clean_data.clear()

# ==================== 心理价位 ====================
def get_price_rules():
    res = supabase.table("price_rules").select("model, buy, sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    return rules

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()

# ==================== 阈值设置 ====================
def get_alert_threshold():
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

def set_alert_threshold(v):
    supabase.table("settings").upsert(
        {"id": 1, "alert_threshold": v}, on_conflict="id"
    ).execute()

# ==================== 数据读取 ====================
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        res = supabase.table(table_name).select("*").range(start, start+page_size-1).execute()
        if not res.data: break
        all_data.extend(res.data)
        start += page_size
    return all_data

@st.cache_data(ttl=120, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"]>0) & (df["价格"]<5000)]
    return df

# ==================== 工具函数 ====================
def extract_remark(line):
    box = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒"]
    bag = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]
    parts = []
    for kw in box:
        if kw in line:
            parts.append(kw)
            break
    for kw in bag:
        if kw in line:
            parts.append(kw)
            break
    return " + ".join(parts) if parts else ""

def extract_by_regex(line):
    line = line.strip()
    if not line: return None, None, None
    remark = extract_remark(line)
    clean = line
    for kw in ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒","纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]:
        clean = clean.replace(kw, "")
    m = re.search(r'(?<![0-9])([1-9]\d{4})(?![0-9])', clean)
    if not m: return None, None, None
    model = m.group(1)
    p_clean = clean.replace(model, "")
    p = re.search(r'(\d+)', p_clean)
    if not p: return None, None, None
    try:
        price = int(p.group(1))
        return model, price, remark
    except:
        return None, None, None

def llm_verify(model, price, remark):
    if 10 <= price <= 5000:
        return True
    prompt = f"""型号:{model} 价格:{price} 备注:{remark}
只返回JSON：{{"is_valid":true/false}}"""
    try:
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={"model":"glm-4-flash","messages":[{"role":"user","content":prompt}],"temperature":0.1},
            timeout=8
        )
        res = resp.json()
        return res.get("choices",[{}])[0].get("message",{}).get("content",{}).get("is_valid",False)
    except:
        return True

# ==================== 增删改 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except:
        return None

# ==================== 界面 ====================
st.title("🧩 乐高报价系统")

# ------------------------------
# 全局搜索 + 阈值
# ------------------------------
df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

col1, col2 = st.columns([3,1])
with col1:
    search = st.text_input("🔍 搜索型号")
with col2:
    th = get_alert_threshold()
    new_th = st.number_input("⚠️ 提醒阈值", min_value=1, value=th)
    if new_th != th:
        set_alert_threshold(new_th)
        st.rerun()

filtered = [m for m in all_models if search in m] if search else all_models

st.divider()

# ------------------------------
# 批量录入（最终纯净版 · 零报错）
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "input_text" not in st.session_state:
        st.session_state.input_text = ""

    txt = st.text_area("粘贴内容", height=200, value=st.session_state.input_text)

    # 唯一按钮
    if st.button("📌 识别粘贴并保存", type="primary", use_container_width=True):
        content = txt.strip()
        if not content:
            st.warning("⚠️ 请先粘贴内容")
            st.stop()

        # 清空
        st.session_state.input_text = ""
        st.rerun()

        # 解析
        save_list = []
        for line in content.splitlines():
            m, p, r = extract_by_regex(line)
            if m and p and llm_verify(m,p,r):
                save_list.append({
                    "time": datetime.now().isoformat(),
                    "model": m,
                    "price": int(p),
                    "remark": str(r).strip()
                })

        # 保存
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 已保存 {len(save_list)} 条")
            get_clean_data.clear()

        # 恢复内容
        st.session_state.input_text = content
        st.rerun()

st.divider()

# ------------------------------
# 历史管理
# ------------------------------
st.subheader("📋 历史数据管理")
if not df.empty:
    target = st.selectbox("选择型号", [""] + filtered)
    if target:
        isfav = target in get_favorites()
        btn_txt = "⭐ 取消收藏" if isfav else "☆ 收藏"
        if st.button(btn_txt):
            toggle_favorite(target)
            st.rerun()

        model_data = df[df["型号"]==target].sort_values("时间", ascending=False)
        show = model_data[["id","时间","型号","价格","remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")
        show.rename(columns={"remark":"备注"}, inplace=True)
        st.dataframe(show, use_container_width=True)
else:
    st.info("暂无数据")