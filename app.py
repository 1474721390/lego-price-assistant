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

st.set_page_config(page_title="乐高报价系统", layout="centered")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 收藏功能 ====================
@st.cache_data(ttl=30)
def get_favorites():
    # ✅ 修复：表名是 user_favorites，不是 favorites
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
@st.cache_data(ttl=30)
def get_price_rules():
    res = supabase.table("price_rules").select("model", "buy", "sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    return rules

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()

# ==================== 阈值设置 ====================
@st.cache_data(ttl=30)
def get_alert_threshold():
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

def set_alert_threshold(v):
    supabase.table("settings").upsert(
        {"id": 1, "alert_threshold": v}, on_conflict="id"
    ).execute()

# ==================== 数据读取 ====================
@st.cache_data(ttl=10)
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        res = supabase.table(table_name).select("*").range(start, start+page_size-1).execute()
        if not res.data:
            break
        all_data.extend(res.data)
        start += page_size
    return all_data

@st.cache_data(ttl=10)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[(df["价格"]>0) & (df["价格"]<100000)]
    return df

# ==================== 异常价格判断 ====================
def is_price_abnormal(model, current_price):
    df = get_clean_data()
    if df.empty or model not in df["型号"].values:
        return False
    model_prices = df[df["型号"] == model]["价格"]
    avg_price = model_prices.mean()
    if avg_price < 10:
        return False
    return current_price < avg_price * 0.5 or current_price > avg_price * 2.5

# ==================== AI 提取（按乐高官网型号规则）====================
def extract_by_ai(line):
    try:
        prompt = f'''
从乐高报价文本提取信息，只返回JSON，不要解释：
文本：{line}
规则：
1. model：乐高官方套装号
   - 主流：5位数字
   - 收藏/旗舰：6~7位数字
   - 老款：4位数字
   - 可带子型号如 75313-1
2. price：单价≥10，个位数为数量不算价格
3. remark：只保留盒况、袋子尺码（S/M/L/XL/SS/XXL等），地址、快递、地区一律不要
输出格式：
{"models":[],"prices":[],"remarks":[]}
'''
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=8
        )
        res = resp.json()
        content = re.sub(r'```.*?```', '', res["choices"][0]["message"]["content"].strip())
        data = json.loads(content)

        valid = []
        for m, p, r in zip(data.get("models", []), data.get("prices", []), data.get("remarks", [])):
            m = str(m).strip()
            if not re.match(r'^[1-9]\d{3,6}(-\d+)?$', m):
                continue
            try:
                p = int(p)
                if p < 10:
                    continue
            except:
                continue
            valid.append((m, p, str(r).strip()))
        return valid
    except:
        return []

# ==================== 备注提取（乐高袋子全尺码）====================
def extract_remark(line):
    line = line.lower()
    box_pat = r'好盒|压盒|瑕疵|盒损|烂盒|破盒|全新|微压|盒'
    box = ''.join(re.findall(box_pat, line))

    bag_pat = r'ss袋|xs袋|s袋|m袋|l袋|xl袋|xxl袋|超小袋|加小袋|小号袋|中号袋|大号袋|加大袋|超大袋|纸袋|礼袋|袋子|袋'
    bag = ''.join(re.findall(bag_pat, line))

    return (box + bag).strip()

# ==================== 正则提取（官网乐高型号规则）====================
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return []

    # 乐高官方型号：4/5/6/7位，支持 -1 -2
    model_pattern = r'(?<![0-9])([1-9]\d{3,6}(-\d+)?)(?![0-9])'
    model_matches = [m[0] for m in re.findall(model_pattern, line)]
    if not model_matches:
        return []

    # 提取价格：排除型号本身，≥10
    all_digits = re.findall(r'\b\d+\b', line)
    price_candidates = []
    for d in all_digits:
        if any(d in m for m in model_matches):
            continue
        try:
            num = int(d)
            if num >= 10:
                price_candidates.append(num)
        except:
            continue

    remark = extract_remark(line)
    res = []
    for i, m in enumerate(model_matches):
        p = price_candidates[i] if i < len(price_candidates) else None
        res.append((m, p, remark))
    return res

# ==================== 智能解析 ====================
def smart_extract(line):
    regex = extract_by_regex(line)
    final = []

    for m, p, r in regex:
        if m and p:
            if p < 10 or p > 5000 or is_price_abnormal(m, p):
                ai = extract_by_ai(line)
                for am, ap, ar in ai:
                    if am == m:
                        final.append((am, ap, ar))
                        break
                else:
                    final.append((m, p, r))
            else:
                final.append((m, p, r))
        elif m:
            ai = extract_by_ai(line)
            for am, ap, ar in ai:
                if am == m:
                    final.append((am, ap, ar))
                    break
            else:
                final.append((m, None, r))

    if not final:
        if re.search(r'[1-9]\d{3,6}(-\d+)?', line) or any(w in line.lower() for w in ['乐高','盒','袋']):
            final = extract_by_ai(line)
    return final

# ==================== 预警 ====================
@st.cache_data(ttl=10)
def get_alerts():
    df = get_clean_data()
    if df.empty:
        return []
    favs = get_favorites()
    t = get_alert_threshold()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2:
            continue
        d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
        if abs(d) >= t:
            alerts.append({
                "model":m,"diff":d,"abs_diff":abs(d),"last":s.iloc[-1]["价格"],
                "trend":"上涨"if d>0 else"下跌","is_fav":m in favs
            })
    return sorted(alerts, key=lambda x:(-x["is_fav"],-x["abs_diff"]))

# ==================== 涨幅 ====================
@st.cache_data(ttl=10)
def get_trend(days=7):
    df = get_clean_data()
    if df.empty:
        return []
    tr = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2:
            continue
        d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
        tr.append({"model":m,"diff":d,"abs_diff":abs(d),"last":s.iloc[-1]["价格"]})
    return sorted(tr, key=lambda x:-x["abs_diff"])

# ==================== 数据库操作 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except:
        return None

def update_record(id, data):
    try:
        return supabase.table("price_records").update(data).eq("id", id).execute()
    except:
        return None

def delete_record(id):
    try:
        return supabase.table("price_records").delete().eq("id", id).execute()
    except:
        return None

# ==================== 界面 ====================
st.title("🧩 乐高报价分析系统")

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

# ------------------------------ 收藏 ------------------------------
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
            st.markdown(f"**{'📈' if d>0 else '📉'} {m}** | {d:+}元 | 当前 {s.iloc[-1]['价格']}元")

st.divider()

# ------------------------------ 涨幅 ------------------------------
with st.expander("📈 近7日 / 近30日涨幅排行", expanded=False):
    c7, c30 = st.columns(2)
    with c7:
        st.markdown("#### 近7天波动TOP10")
        for item in get_trend(7)[:10]:
            st.markdown(f"`{item['model']}` | {item['diff']:+} 元 | {item['last']}")
    with c30:
        st.markdown("#### 近30天波动TOP10")
        for item in get_trend(30)[:10]:
            st.markdown(f"`{item['model']}` | {item['diff']:+} 元 | {item['last']}")

st.divider()

# ------------------------------ 预警 ------------------------------
with st.expander("📊 价格波动预警", expanded=False):
    alerts = get_alerts()
    up = [a for a in alerts if a["trend"]=="上涨"]
    down = [a for a in alerts if a["trend"]=="下跌"]
    c_up, c_down = st.columns(2)
    with c_up:
        st.subheader("📈 上涨")
        for a in up:
            st.success(f"{'⭐' if a['is_fav'] else '📈'} {a['model']} +{a['abs_diff']} → {a['last']}")
    with c_down:
        st.subheader("📉 下跌")
        for a in down:
            st.error(f"{'⭐' if a['is_fav'] else '📉'} {a['model']} -{a['abs_diff']} → {a['last']}")

st.divider()

# ------------------------------ 批量录入 ------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()

    txt = st.text_area("粘贴内容", height=200)
    if st.button("🔍 解析", type="primary", use_container_width=True):
        if not txt:
            st.warning("请输入内容")
            st.stop()
        res = []
        save_list = []
        for li in txt.strip().splitlines():
            for m,p,r in smart_extract(li):
                if m and p and p >=10:
                    res.append({"型号":m,"价格":p,"备注":r,"原始":li,"状态":"✅ 有效"})
                    save_list.append({"time":datetime.now().isoformat(),"model":m,"price":p,"remark":r})
                else:
                    res.append({"型号":m or "","价格":p or "","备注":r or "","原始":li,"状态":"❌ 解析失败"})
        st.session_state.parse_result = pd.DataFrame(res)
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 解析并保存 {len(save_list)} 条")
            get_clean_data.clear()

    if not st.session_state.parse_result.empty:
        ed = st.data_editor(
            st.session_state.parse_result,
            use_container_width=True, hide_index=True,
            column_config={
                "型号": st.column_config.TextColumn("型号"),
                "价格": st.column_config.NumberColumn("价格", min_value=10),
                "备注": st.column_config.TextColumn("备注"),
                "原始": st.column_config.TextColumn("原始", disabled=True),
                "状态": st.column_config.TextColumn("状态", disabled=True),
            }
        )
        if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True):
            ok = []
            for _, row in ed.iterrows():
                m = str(row["型号"]).strip()
                p = row["价格"]
                if m and p and "✅" in row["状态"] and re.match(r'^[1-9]\d{3,6}(-\d+)?$', m) and int(p)>=10:
                    ok.append({"time":datetime.now().isoformat(),"model":m,"price":int(p),"remark":str(row["备注"]).strip()})
            if ok:
                save_batch(ok)
                st.success(f"✅ 保存成功 {len(ok)} 条")
                st.session_state.parse_result = pd.DataFrame()
                get_clean_data.clear()
                st.rerun()

st.divider()

# ------------------------------ 历史数据 ------------------------------
st.subheader("📋 历史数据管理")
if not df.empty:
    target = st.selectbox("选择型号", [""] + filtered)
    if target:
        btn_text = "⭐ 取消收藏" if target in favs else "☆ 收藏"
        if st.button(btn_text):
            toggle_favorite(target)
            st.rerun()

        rule = get_price_rules().get(target,{"buy":0,"sell":0})
        cb, cs = st.columns(2)
        with cb:
            b = st.number_input("💚 可收价格", value=rule["buy"])
        with cs:
            s = st.number_input("❤️ 可出价格", value=rule["sell"])
        if st.button("💾 保存心理价位"):
            save_price_rule(target,b,s)
            st.success("✅ 已保存")
            st.rerun()

        md = df[df["型号"]==target].sort_values("时间", ascending=False)
        if not md.empty:
            cur = md.iloc[0]["价格"]
            tip = ""
            if s>0 and cur>=s:
                tip = "❤️ 可出货"
            elif b>0 and cur<=b:
                tip = "💚 可收货"
            if tip:
                st.info(f"当前价 {cur} → {tip}")

        show = md[["id","时间","型号","价格","remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")
        show.rename(columns={"remark":"备注"}, inplace=True)
        show.insert(0,"删除",False)

        ed_table = st.data_editor(
            show, use_container_width=True, hide_index=True,
            column_config={
                "型号": st.column_config.TextColumn("型号"),
                "价格": st.column_config.NumberColumn("价格", min_value=10),
                "备注": st.column_config.TextColumn("备注"),
            }
        )

        if st.button("保存修改 & 删除"):
            for _, row in ed_table[ed_table["删除"]].iterrows():
                delete_record(row["id"])
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
        st.plotly_chart(px.line(md.sort_values("时间"), x="时间", y="价格", markers=True), use_container_width=True)
else:
    st.info("暂无数据")