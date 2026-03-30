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

# ==================== AI 提取（兜底用）====================
def extract_by_ai(line):
    try:
        prompt = f'''
从乐高报价文本提取信息，只返回JSON，不要解释：
文本：{line}
规则：
1. model：乐高官方套装号（5位为主，4位老款，6/7位旗舰，非0开头）
2. price：单价≥10，个位数是数量不算价格
3. remark：只保留盒况（好盒/压盒/全新等），地址/快递/价格/型号一律不要
输出格式：
{{"models": [], "prices": [], "remarks": []}}
'''
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0
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

# ==================== 备注提取 ====================
def extract_remark(line):
    line = line.lower()
    box_pat = r'好盒|压盒|瑕疵|盒损|烂盒|破盒|全新|微压|盒'
    box = ''.join(re.findall(box_pat, line))
    return box.strip()

# ==================== 【终极修复】型号/价格识别逻辑 ====================
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return []

    # 1. 【铁规则】先提取所有数字
    all_numbers = re.findall(r'\b(\d+)\b', line)
    if not all_numbers:
        return []

    # 2. 【铁规则】区分型号和价格
    # 型号：5位数字（乐高主流）、6/7位旗舰、4位老款（非0开头）
    # 价格：≥10，且不是型号的数字
    models = []
    prices = []

    for num in all_numbers:
        # 型号判断：非0开头，4/5/6/7位
        if re.match(r'^[1-9]\d{3,6}$', num):
            # 【核心修复】5位优先当型号，4位只有在没有5位时才当型号（避免价格当型号）
            if len(num) == 5:
                models.append(num)
            elif len(num) == 4:
                # 4位数字：只有当没有5位型号时，才可能是老款型号；否则当价格
                if not any(len(m) == 5 for m in models):
                    models.append(num)
                else:
                    prices.append(int(num))
            elif len(num) in (6,7):
                models.append(num)
        else:
            # 非型号格式，≥10当价格
            try:
                n = int(num)
                if n >= 10:
                    prices.append(n)
            except:
                pass

    # 3. 【铁规则】去重，保证型号唯一
    models = list(dict.fromkeys(models))
    prices = list(dict.fromkeys(prices))

    # 4. 【铁规则】型号和价格严格一一对应
    remark = extract_remark(line)
    results = []
    for i, model in enumerate(models):
        price = prices[i] if i < len(prices) else None
        results.append((model, price, remark))

    return results

# ==================== 智能解析 ====================
def smart_extract(line):
    regex_results = extract_by_regex(line)
    final_results = []

    for m, p, r in regex_results:
        if m and p:
            if p < 10 or p > 50000 or is_price_abnormal(m, p):
                ai_results = extract_by_ai(line)
                for ai_m, ai_p, ai_r in ai_results:
                    if ai_m == m:
                        final_results.append((ai_m, ai_p, ai_r))
                        break
                else:
                    final_results.append((m, p, r))
                continue
            final_results.append((m, p, r))
            continue

        if m and p is None:
            ai_results = extract_by_ai(line)
            for ai_m, ai_p, ai_r in ai_results:
                if ai_m == m:
                    final_results.append((ai_m, ai_p, ai_r))
                    break
            else:
                final_results.append((m, None, r))
            continue

        final_results.append((m, p, r))

    if not final_results:
        if re.search(r'[1-9]\d{4,6}', line) or any(w in line.lower() for w in ['乐高','好盒','收','出']):
            final_results = extract_by_ai(line)

    return final_results

# ==================== 预警 ====================
@st.cache_data(ttl=10)
def get_alerts():
    df = get_clean_data()
    if df.empty:
        return []
    favs = get_favorites()
    threshold = get_alert_threshold()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2:
            continue
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

# ==================== 涨幅 ====================
@st.cache_data(ttl=10)
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
            "model": m, "diff": diff, "abs_diff": abs(diff), "last": new
        })
    return sorted(trends, key=lambda x: -x["abs_diff"])

# ==================== 增删改 ====================
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
            icon = "📈" if d>0 else "📉"
            st.markdown(f"**{icon} {m}** | {d:+}元 | 当前 {s.iloc[-1]['价格']}元")

st.divider()

# ------------------------------ 涨幅 ------------------------------
with st.expander("📈 近7日 / 近30日涨幅排行", expanded=False):
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

# ------------------------------ 预警 ------------------------------
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

# ------------------------------ 批量录入（彻底修复）------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()

    txt = st.text_area("粘贴内容", height=200)

    if st.button("🔍 解析", type="primary", use_container_width=True):
        if not txt:
            st.warning("请输入内容")
            st.stop()
        
        lines = txt.strip().splitlines()
        res = []
        save_list = []
        
        for li in lines:
            extract_results = smart_extract(li)
            for m, p, r in extract_results:
                if m and p and re.match(r'^[1-9]\d{3,6}(-\d+)?$', m) and p >=10:
                    res.append({
                        "型号": m,
                        "价格": p,
                        "备注": r,
                        "原始": li,
                        "状态": "✅ 有效"
                    })
                    save_list.append({
                        "time": datetime.now().isoformat(),
                        "model": m,
                        "price": p,
                        "remark": r
                    })
                else:
                    res.append({
                        "型号": m if m else "",
                        "价格": p if p else "",
                        "备注": r if r else "",
                        "原始": li,
                        "状态": "❌ 解析失败"
                    })
        
        st.session_state.parse_result = pd.DataFrame(res)
        
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 解析并保存 {len(save_list)} 条")
            get_clean_data.clear()

    if not st.session_state.parse_result.empty:
        ed = st.data_editor(
            st.session_state.parse_result,
            use_container_width=True,
            hide_index=True,
            column_config={
                "型号": st.column_config.TextColumn("型号", disabled=False),
                "价格": st.column_config.NumberColumn("价格", disabled=False, min_value=10),
                "备注": st.column_config.TextColumn("备注", disabled=False),
                "原始": st.column_config.TextColumn("原始", disabled=True),
                "状态": st.column_config.TextColumn("状态", disabled=True),
            },
            num_rows="dynamic"
        )

        if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True):
            ok_list = []
            for _, row in ed.iterrows():
                model_str = str(row["型号"]).strip()
                price_val = row["价格"]
                if model_str and price_val and "✅" in row["状态"]:
                    if re.match(r'^[1-9]\d{3,6}(-\d+)?$', model_str) and int(price_val) >=10:
                        ok_list.append({
                            "time": datetime.now().isoformat(),
                            "model": model_str,
                            "price": int(price_val),
                            "remark": str(row["备注"]).strip()
                        })
            if ok_list:
                save_batch(ok_list)
                st.success(f"✅ 保存成功 {len(ok_list)} 条")
                st.session_state.parse_result = pd.DataFrame()
                get_clean_data.clear()
                st.rerun()

st.divider()

# ------------------------------ 历史数据 ------------------------------
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

        ed_table = st.data_editor(
            show,
            use_container_width=True,
            hide_index=True,
            column_config={
                "型号": st.column_config.TextColumn("型号", disabled=False),
                "价格": st.column_config.NumberColumn("价格", disabled=False, min_value=10),
                "备注": st.column_config.TextColumn("备注", disabled=False),
            },
            num_rows="dynamic"
        )

        if st.button("保存修改 & 删除"):
            del_ids = ed_table[ed_table["删除"]==True]["id"].tolist()
            for did in del_ids:
                delete_record(did)
            for _, row in ed_table[~ed_table["删除"]].iterrows():
                update_record(row["id"],{
                    "model": str(row["型号"]).strip(),
                    "price": int(row["价格"]),
                    "remark": str(row["备注"]).strip()
                })
            st.success("完成")
            get_clean_data.clear()
            st.rerun()

        st.subheader("价格走势")
        fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无数据")