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

# ==================== 全量读取 ====================
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        res = supabase.table(table_name).select("*").range(start, start + page_size - 1).execute()
        data = res.data
        if not data:
            break
        all_data.extend(data)
        start += page_size
    return all_data

# ==================== 清洗数据 ====================
@st.cache_data(ttl=120, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"] > 0) & (df["价格"] < 10000)]
    return df

# ==================== 价格异常 ====================
def is_price_abnormal(price):
    return price < 10 or price > 5000

# ==================== 备注提取（无则空） ====================
def extract_remark(line):
    box = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒"]
    bag = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]
    b = next((x for x in box if x in line), None)
    g = next((x for x in bag if x in line), None)
    parts = []
    if b: parts.append(b)
    if g: parts.append(g)
    return " + ".join(parts) if parts else ""

# ==================== 正则提取 ====================
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    remark = extract_remark(line)
    clean = line
    for kw in ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒", "纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]:
        clean = clean.replace(kw, "")
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
        return model, price, remark
    except:
        return None, None, None

# ==================== 大模型校验 ====================
def llm_verify(model, price, remark):
    if not is_price_abnormal(price):
        return True, "正常"
    prompt = f"""型号:{model} 价格:{price} 备注:{remark}
判断是否有效，乐高正常10-5000元。
返回JSON: {{"is_valid":true/false,"reason":"原因"}}"""
    try:
        r = requests.post("https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization":f"Bearer {ZHIPU_API_KEY}"},
            json={"model":"glm-4-flash","messages":[{"role":"user","content":prompt}]})
        j = r.json()
        res = json.loads(j["choices"][0]["message"]["content"])
        return res["is_valid"], res["reason"]
    except:
        return False, "模型异常"

# ==================== 波动预警 ====================
def get_alerts():
    df = get_clean_data()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2:continue
        d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
        if abs(d)>=10:
            alerts.append({"model":m,"diff":d,"last":s.iloc[-1]["价格"],"trend":"上涨"if d>0else"下跌"})
    return sorted(alerts, key=lambda x:abs(x["diff"]),reverse=True)

# ==================== 增删改 ====================
def save_one(data):
    try:
        return supabase.table("price_records").insert(data).execute()
    except:
        return None
def update_one(id,data):
    try:
        return supabase.table("price_records").update(data).eq("id",id).execute()
    except:
        return None
def delete_one(id):
    try:
        return supabase.table("price_records").delete().eq("id",id).execute()
    except:
        return None

# ==================== 收藏功能（本地缓存） ====================
if "favorites" not in st.session_state:
    st.session_state.favorites = []

def add_fav(model):
    if model not in st.session_state.favorites:
        st.session_state.favorites.append(model)
def remove_fav(model):
    if model in st.session_state.favorites:
        st.session_state.favorites.remove(model)

# ==================== UI ====================
st.title("🧩 乐高报价分析系统")

# ------------------------------
# 1. 预警区（可折叠 + 搜索）
# ------------------------------
with st.expander("📊 价格波动预警（≥10元）", expanded=False):
    alerts = get_alerts()
    if alerts:
        search_model = st.text_input("搜索预警型号", placeholder="输入型号过滤")
        filtered = [a for a in alerts if search_model.strip()=="" or search_model.strip() == a["model"]]
        for a in filtered:
            if a["trend"]=="上涨":
                st.success(f"📈 {a['model']} 上涨{a['diff']}元 → 当前{a['last']}元")
            else:
                st.error(f"📉 {a['model']} 下跌{abs(a['diff'])}元 → 当前{a['last']}元")
    else:
        st.info("暂无波动预警")

# ------------------------------
# 2. 批量录入（可折叠）
# ------------------------------
with st.expander("📝 批量录入报价", expanded=False):
    txt = st.text_area("粘贴多行内容", height=220)
    if st.button("解析并保存"):
        lines = txt.strip().splitlines()
        ok = 0
        for line in lines:
            m,p,r = extract_by_regex(line)
            if not m or not p: continue
            v,reason = llm_verify(m,p,r)
            if not v:
                st.warning(f"{m} {p}元 - {reason}")
                continue
            save_one({"time":datetime.now().isoformat(),"model":m,"price":p,"remark":r})
            ok +=1
        st.success(f"保存成功 {ok} 条")
        get_clean_data.clear()
        st.rerun()

# ------------------------------
# 3. 收藏夹（快速查看价格）
# ------------------------------
st.markdown("---")
st.subheader("⭐ 收藏型号（快速查看）")
df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

col1, col2 = st.columns([3,1])
with col1:
    fav_select = st.selectbox("选择要收藏的型号", [""]+all_models)
with col2:
    if st.button("添加收藏") and fav_select:
        add_fav(fav_select)
        st.rerun()

if st.session_state.favorites:
    favs = st.session_state.favorites
    tabs = st.tabs(favs)
    for i,tab in enumerate(tabs):
        m = favs[i]
        with tab:
            if st.button("取消收藏",key=f"rm_{i}"):
                remove_fav(m)
                st.rerun()
            sub = df[df["型号"]==m].sort_values("时间")
            if not sub.empty:
                st.metric("当前价格", f"{sub.iloc[-1]['价格']} 元")
                fig = px.line(sub, x="时间", y="价格", markers=True)
                st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无收藏型号")

# ------------------------------
# 4. 历史数据管理（编辑/删除/纠错）
# ------------------------------
st.markdown("---")
st.subheader("📋 历史数据管理（纠错/编辑/删除）")

if not df.empty:
    sel_m = st.selectbox("筛选型号", [""]+all_models, key="manage_model")
    if sel_m:
        sub = df[df["型号"]==sel_m].sort_values("时间", ascending=False)
        show = sub[["id","时间","型号","价格","remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")

        edited = st.data_editor(show, num_rows="fixed", use_container_width=True,
            column_config={
                "id": "ID",
                "时间": st.column_config.TextColumn("时间", disabled=True),
                "型号": "型号",
                "价格": "价格",
                "remark": "备注"
            })

        if st.button("💾 保存修改"):
            for _,row in edited.iterrows():
                update_one(row["id"], {
                    "model": str(row["型号"]).strip(),
                    "price": int(row["价格"]),
                    "remark": str(row["remark"]).strip()
                })
            get_clean_data.clear()
            st.success("已保存")
            st.rerun()

        del_ids = st.multiselect("选择删除ID", sub["id"].tolist())
        if st.button("🗑 删除选中") and del_ids:
            for did in del_ids:
                delete_one(did)
            get_clean_data.clear()
            st.rerun()
else:
    st.info("暂无数据")