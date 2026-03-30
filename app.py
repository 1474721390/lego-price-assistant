import os
import re
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st
import plotly.express as px
from supabase import create_client
import pyperclip

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
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"]>0) & (df["价格"]<100000)]
    return df

# ==================== 工具函数 ====================
def is_price_abnormal(price):
    return price < 10 or price > 5000

def extract_remark(line):
    box = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒"]
    bag = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]
    b = next((x for x in box if x in line), None)
    g = next((x for x in bag if x in line), None)
    parts = []
    if b: parts.append(b)
    if g: parts.append(g)
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
    if not is_price_abnormal(price):
        return True, "正常"
    prompt = f"""型号:{model} 价格:{price} 备注:{remark}
只返回JSON：{{"is_valid":true/false,"reason":"原因"}}"""
    try:
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={"model":"glm-4-flash","messages":[{"role":"user","content":prompt}],"temperature":0.1},
            timeout=10
        )
        j = resp.json()
        res = json.loads(j["choices"][0]["message"]["content"])
        return res["is_valid"], res["reason"]
    except:
        return False, "模型异常"

# ==================== 预警 ====================
def get_alerts():
    df = get_clean_data()
    if df.empty: return []
    favs = get_favorites()
    threshold = get_alert_threshold()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2: continue
        first = s.iloc[0]["价格"]
        last = s.iloc[-1]["价格"]
        diff = last - first
        if abs(diff) >= threshold:
            alerts.append({
                "model":m,"diff":diff,"abs_diff":abs(diff),"last":last,
                "trend":"上涨"if diff>0 else"下跌","is_fav":m in favs
            })
    alerts.sort(key=lambda x: (-x["is_fav"], -x["abs_diff"]))
    return alerts

# ==================== 近7/30天排行 ====================
def get_trend(days=7):
    df = get_clean_data()
    if df.empty:
        return []

    trends = []
    for m in df["型号"].unique():
        s = df[df["型号"] == m].sort_values("时间")
        if len(s) < 2:
            continue

        old = s.iloc[0]["价格"]
        new = s.iloc[-1]["价格"]
        diff = new - old

        trends.append({
            "model": m,
            "diff": diff,
            "abs_diff": abs(diff),
            "last": new
        })

    return sorted(trends, key=lambda x: -x["abs_diff"])

# ==================== 增删改 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except: return None

def update_record(id, data):
    try:
        return supabase.table("price_records").update(data).eq("id", id).execute()
    except: return None

def delete_record(id):
    try:
        return supabase.table("price_records").delete().eq("id", id).execute()
    except: return None

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

# ------------------------------
# 收藏面板
# ------------------------------
favs = get_favorites()
if favs:
    with st.expander("⭐ 我的收藏", expanded=True):
        for m in favs:
            s = df[df["型号"]==m]
            if len(s)<2:
                st.write(f"{m} | 数据不足")
                continue
            s = s.sort_values("时间")
            d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
            icon = "📈" if d>0 else "📉"
            st.markdown(f"**{icon} {m}** | {d:+}元 | 当前 {s.iloc[-1]['价格']}元")

st.divider()

# ------------------------------
# 近7/30天排行
# ------------------------------
with st.expander("📈 近7日 / 近30日 涨幅排行", expanded=False):
    c7, c30 = st.columns(2)
    with c7:
        st.markdown("#### 近7天波动TOP10")
        t7 = get_trend(7)
        for item in t7[:10]:
            st.markdown(f"`{item['model']}` | {item['diff']:+} 元 | 当前 {item['last']}")
    with c30:
        st.markdown("#### 近30天波动TOP10")
        t30 = get_trend(30)
        for item in t30[:10]:
            st.markdown(f"`{item['model']}` | {item['diff']:+} 元 | 当前 {item['last']}")

st.divider()

# ------------------------------
# 预警区
# ------------------------------
with st.expander("📊 价格波动预警", expanded=False):
    alerts = get_alerts()
    up = [a for a in alerts if a["trend"]=="上涨"]
    down = [a for a in alerts if a["trend"]=="下跌"]
    c_up, c_down = st.columns(2)
    with c_up:
        st.subheader("📈 上涨")
        for a in up:
            if a["is_fav"]:
                st.success(f"⭐ {a['model']} +{a['abs_diff']} → {a['last']}")
            else:
                st.success(f"📈 {a['model']} +{a['abs_diff']} → {a['last']}")
    with c_down:
        st.subheader("📉 下跌")
        for a in down:
            if a["is_fav"]:
                st.error(f"⭐ {a['model']} -{a['abs_diff']} → {a['last']}")
            else:
                st.error(f"📉 {a['model']} -{a['abs_diff']} → {a['last']}")

st.divider()

# ------------------------------
# 批量录入（最终按你要求修改，逻辑彻底重写）
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    # 初始化输入框状态
    if "input_content" not in st.session_state:
        st.session_state.input_content = ""

    # 输入框：绑定session_state，确保实时更新
    input_area = st.text_area(
        "粘贴内容",
        height=200,
        value=st.session_state.input_content,
        key="input_area_key"
    )

    # 只保留一个按钮：📌 识别粘贴并保存（彻底删除「解析」按钮）
    if st.button("📌 识别粘贴并保存", type="primary", use_container_width=True):
        # 步骤1：读取剪贴板内容
        clipboard_text = pyperclip.paste()
        if not clipboard_text:
            st.warning("⚠️ 剪贴板为空，请先复制要录入的内容！")
            st.stop()

        # 步骤2：清空输入框（立即更新session_state，强制刷新）
        st.session_state.input_content = ""
        st.rerun()

        # 步骤3：解析剪贴板内容
        lines = clipboard_text.strip().splitlines()
        save_records = []
        for line in lines:
            model, price, remark = extract_by_regex(line)
            if not model or not price:
                continue
            ok, _ = llm_verify(model, price, remark)
            if ok:
                save_records.append({
                    "time": datetime.now().isoformat(),
                    "model": model,
                    "price": int(price),
                    "remark": str(remark).strip()
                })

        # 步骤4：保存有效数据到数据库
        if save_records:
            save_batch(save_records)
            st.success(f"✅ 成功保存 {len(save_records)} 条有效数据！")
            # 强制刷新数据缓存
            get_clean_data.clear()

        # 步骤5：把剪贴板内容重新填回输入框
        st.session_state.input_content = clipboard_text
        st.rerun()

st.divider()

# ------------------------------
# 历史管理 + 心理价位
# ------------------------------
st.subheader("📋 历史数据管理")
if not df.empty:
    target = st.selectbox("选择型号", [""] + filtered)
    if target:
        isfav = target in favs
        btn_txt = "⭐ 取消收藏" if isfav else "☆ 收藏"
        if st.button(btn_txt):
            toggle_favorite(target)
            st.rerun()

        rules = get_price_rules()
        rule = rules.get(target, {"buy":0, "sell":0})
        cb, cs = st.columns(2)
        with cb:
            b = st.number_input("💚 可收价格", value=rule["buy"])
        with cs:
            s = st.number_input("❤️ 可出价格", value=rule["sell"])
        if st.button("💾 保存心理价位"):
            save_price_rule(target, b, s)
            st.success("✅ 已保存")
            st.rerun()

        model_data = df[df["型号"]==target].sort_values("时间", ascending=False)
        if not model_data.empty:
            cur = model_data.iloc[0]["价格"]
            tip = ""
            if s>0 and cur>=s:
                tip = "❤️ 可出货"
            elif b>0 and cur<=b:
                tip = "💚 可收货"
            if tip:
                st.info(f"当前价 {cur} → {tip}")

        show = model_data[["id","时间","型号","价格","remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")
        show.rename(columns={"remark":"备注"}, inplace=True)
        show.insert(0,"删除",False)
        ed_table = st.data_editor(show, use_container_width=True, hide_index=True)

        if st.button("保存修改 & 删除"):
            del_ids = ed_table[ed_table["删除"]==True]["id"].tolist()
            for did in del_ids:
                delete_record(did)
            for _, row in ed_table[~ed_table["删除"]].iterrows():
                update_record(row["id"],{
                    "model":str(row["型号"]).strip(),
                    "price":int(row["价格"]),
                    "remark":str(row["备注"]).strip()
                })
            st.success("完成")
            get_clean_data.clear()
            st.rerun()

        st.subheader("价格走势")
        fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无数据")