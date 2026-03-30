import os
import re
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st
import plotly.express as px
from supabase import create_client

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请配置完整环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价分析系统 · 专业版", layout="wide")
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
    for kw in ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒",
               "纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]:
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

# ==================== 预警 & 排行（修复时间比较问题）====================
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

def get_trend(days=7):
    df = get_clean_data()
    if df.empty: return []
    # 修复：将past转为pandas的Timestamp，保证类型一致
    now = pd.Timestamp(datetime.now())
    past = now - pd.Timedelta(days=days)
    trends = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        s_recent = s[s["时间"] >= past]
        if len(s_recent)<2: continue
        old = s_recent.iloc[0]["价格"]
        new = s_recent.iloc[-1]["价格"]
        diff = new - old
        trends.append({"model":m,"diff":diff,"abs_diff":abs(diff),"last":new})
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
st.title("🧩 乐高报价分析系统 · 专业版")

# ------------------------------
# 1. 全局搜索 + 阈值设置（功能1+2）
# ------------------------------
df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

# 顶部搜索栏
col_search, col_th = st.columns([3, 1])
with col_search:
    search_global = st.text_input("🔍 全局搜索型号", placeholder="输入数字快速查找（如40528）")
with col_th:
    current_th = get_alert_threshold()
    new_th = st.number_input("⚠️ 涨跌提醒阈值（元）", min_value=1, value=current_th, step=1)
    if new_th != current_th:
        set_alert_threshold(new_th)
        st.success(f"✅ 阈值已更新为 {new_th} 元")
        st.rerun()

# 过滤搜索结果
filtered_models = [m for m in all_models if search_global.strip() in m] if search_global else all_models

# ------------------------------
# 2. 收藏简报（功能5）
# ------------------------------
favs = get_favorites()
if favs:
    with st.expander("⭐ 我的收藏 · 实时简报", expanded=True):
        for m in favs:
            s = df[df["型号"]==m].sort_values("时间")
            if len(s)<2:
                st.markdown(f"**{m}** | 暂无足够数据")
                continue
            old = s.iloc[0]["价格"]
            new = s.iloc[-1]["价格"]
            diff = new - old
            icon = "📈" if diff>0 else "📉"
            st.markdown(f"**{icon} {m}** | {diff:+} 元 | 当前 {new} 元")

st.divider()

# ------------------------------
# 3. 近7日 / 近30日 涨幅排行（功能3）
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
# 4. 价格波动预警
# ------------------------------
with st.expander("📊 价格波动预警", expanded=False):
    alerts = get_alerts()
    rise = [a for a in alerts if a["trend"]=="上涨"]
    fall = [a for a in alerts if a["trend"]=="下跌"]
    cr, cf = st.columns(2)
    with cr:
        st.subheader("📈 上涨（收藏优先）")
        for a in rise:
            if a["is_fav"]:
                st.success(f"⭐ **{a['model']}** +{a['abs_diff']} → {a['last']}")
            else:
                st.success(f"📈 {a['model']} +{a['abs_diff']} → {a['last']}")
    with cf:
        st.subheader("📉 下跌（收藏优先）")
        for a in fall:
            if a["is_fav"]:
                st.error(f"⭐ **{a['model']}** -{a['abs_diff']} → {a['last']}")
            else:
                st.error(f"📉 {a['model']} -{a['abs_diff']} → {a['last']}")

st.divider()

# ------------------------------
# 5. 批量录入
# ------------------------------
with st.expander("📝 批量录入报价", expanded=False):
    user_input = st.text_area("每行一个型号（支持备注）", height=220)
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()

    if st.button("🔍 解析数据", type="primary", use_container_width=True):
        if not user_input.strip():
            st.warning("请输入内容")
        else:
            lines = user_input.strip().splitlines()
            res = []
            for line in lines:
                line = line.strip()
                if not line: continue
                m,p,r = extract_by_regex(line)
                if not m or not p:
                    res.append({"型号":"","价格":"","备注":"","原始行":line,"状态":"❌ 解析失败"})
                    continue
                ok, reason = llm_verify(m,p,r)
                res.append({"型号":m,"价格":p,"备注":r,"原始行":line,"状态":"✅ 有效" if ok else "❌ 无效"})
            st.session_state.parse_result = pd.DataFrame(res)

    if not st.session_state.parse_result.empty:
        ed = st.data_editor(
            st.session_state.parse_result, num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "型号":st.column_config.TextColumn(required=True),
                "价格":st.column_config.NumberColumn(required=True),
                "备注":st.column_config.TextColumn(),
                "原始行":st.column_config.TextColumn(disabled=True),
                "状态":st.column_config.TextColumn(disabled=True)
            }
        )
        ca, cb = st.columns(2)
        with ca:
            if st.button("💾 保存有效数据", use_container_width=True):
                valid = []
                for _, row in ed.iterrows():
                    if row["型号"] and row["价格"] and "✅" in str(row["状态"]):
                        valid.append({
                            "time":datetime.now().isoformat(),
                            "model":str(row["型号"]).strip(),
                            "price":int(row["价格"]),
                            "remark":str(row["备注"]).strip()
                        })
                if valid:
                    save_batch(valid)
                    st.success(f"✅ 保存 {len(valid)} 条")
                    st.session_state.parse_result = pd.DataFrame()
                    get_clean_data.clear()
                    st.rerun()
        with cb:
            if st.button("🗑️ 清空预览", use_container_width=True):
                st.session_state.parse_result = pd.DataFrame()
                st.rerun()

st.divider()

# ------------------------------
# 6. 历史管理 + 心理价位（功能4）
# ------------------------------
st.subheader("📋 历史数据管理")
if not df.empty:
    target = st.selectbox("选择型号", [""] + filtered_models)
    if target:
        # 收藏按钮
        is_fav = target in favs
        bt = "⭐ 取消收藏" if is_fav else "☆ 收藏型号"
        if st.button(bt):
            toggle_favorite(target)
            st.rerun()

        # 心理价位设置
        rules = get_price_rules()
        rule = rules.get(target, {"buy":0, "sell":0})
        c_buy, c_sell = st.columns(2)
        with c_buy:
            buy = st.number_input("💚 可收价格（低于此价可买）", value=rule["buy"], step=1)
        with c_sell:
            sell = st.number_input("❤️ 可出价格（高于此价可卖）", value=rule["sell"], step=1)
        if st.button("💾 保存心理价位"):
            save_price_rule(target, buy, sell)
            st.success("✅ 心理价位已保存")
            st.rerun()

        # 显示当前价状态
        data_model = df[df["型号"]==target].sort_values("时间", ascending=False)
        if not data_model.empty:
            current = data_model.iloc[0]["价格"]
            tip = ""
            if sell > 0 and current >= sell:
                tip = "❤️ 可出货"
            elif buy > 0 and current <= buy:
                tip = "💚 可收货"
            if tip:
                st.info(f"当前价 {current} 元 → {tip}")

        # 表格编辑
        show = data_model[["id","时间","型号","价格","remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")
        show.rename(columns={"remark":"备注"}, inplace=True)
        show.insert(0,"删除",False)

        edited = st.data_editor(
            show, use_container_width=True, hide_index=True,
            column_config={
                "id":"ID", "时间":st.column_config.TextColumn(disabled=True),
                "型号":"型号", "价格":"价格", "备注":"备注", "删除":st.column_config.CheckboxColumn()
            }
        )

        if st.button("💾 保存修改 & 删除勾选", use_container_width=True):
            del_ids = edited[edited["删除"]==True]["id"].tolist()
            for did in del_ids:
                delete_record(did)
            up = edited[edited["删除"]==False]
            for _, row in up.iterrows():
                update_record(row["id"], {
                    "model":str(row["型号"]).strip(),
                    "price":int(row["价格"]),
                    "remark":str(row["备注"]).strip()
                })
            st.success("✅ 操作完成")
            get_clean_data.clear()
            st.rerun()

        # 价格走势
        st.subheader("📈 价格走势")
        fig = px.line(data_model.sort_values("时间"), x="时间", y="价格", markers=True)
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无数据")