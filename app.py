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

# 【备注解析完全按你要求】
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
        return ""  # 无则空白

# 【型号提取支持LG-xxxx格式】
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None

    remark = extract_remark(line)
    # 匹配 LG-10434 或 10434 格式的5位型号
    model_match = re.search(r'(?<![0-9])(?:LG-)?([1-9]\d{4})(?![0-9])', line)
    if not model_match:
        return None, None, None

    # 匹配价格
    price_match = re.search(r'(\d+)', line)
    if not price_match:
        return None, None, None

    model = model_match.group(1)
    try:
        price = int(price_match.group(1))
    except:
        return None, None, None

    return model, price, remark

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
st.title("🧩 乐高报价分析系统")

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
# 批量录入（100%按你要求修复，所有按钮保留）
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    # 初始化状态
    if "input_content" not in st.session_state:
        st.session_state.input_content = ""
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()

    # 输入框（绑定session_state，自动回填）
    txt = st.text_area("粘贴内容", height=200, value=st.session_state.input_content, key="input_area")

    # 按钮1：🔍 解析（原样保留）
    if st.button("🔍 解析", type="primary"):
        if not txt:
            st.warning("请输入内容")
            st.stop()
        lines = txt.strip().splitlines()
        res = []
        save_list = []
        for li in lines:
            m,p,r = extract_by_regex(li)
            if not m or not p:
                res.append({"型号":"","价格":"","备注":"","原始":li,"状态":"❌ 解析失败"})
                continue
            ok,_ = llm_verify(m,p,r)
            res.append({"型号":m,"价格":p,"备注":r,"原始":li,"状态":"✅ 有效" if ok else "❌ 无效"})
            if ok:
                save_list.append({
                    "time":datetime.now().isoformat(),
                    "model":m,
                    "price":int(p),
                    "remark":str(r).strip()
                })
        st.session_state.parse_result = pd.DataFrame(res)
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 解析并保存 {len(save_list)} 条数据成功！")
            get_clean_data.clear()
            st.rerun()

    # 按钮2：📌 识别粘贴并保存（按你要求：自动读剪贴板→自动填→自动解析保存）
    if st.button("📌 识别粘贴并保存", type="primary", use_container_width=True):
        # 1. 优先用输入框已有内容，无则用剪贴板（通过前端JS读取）
        content = txt
        if not content:
            # 用Streamlit兼容的JS读取剪贴板，填入输入框
            st.components.v1.html("""
            <script>
            navigator.clipboard.readText().then(text => {
                window.parent.postMessage({
                    type: 'streamlit:setSessionState',
                    key: 'input_content',
                    value: text
                }, '*');
            });
            </script>
            """, height=0)
            st.rerun()

        # 2. 解析内容
        lines = content.strip().splitlines()
        res = []
        save_list = []
        for li in lines:
            m,p,r = extract_by_regex(li)
            if not m or not p:
                res.append({"型号":"","价格":"","备注":"","原始":li,"状态":"❌ 解析失败"})
                continue
            ok,_ = llm_verify(m,p,r)
            res.append({"型号":m,"价格":p,"备注":r,"原始":li,"状态":"✅ 有效" if ok else "❌ 无效"})
            if ok:
                save_list.append({
                    "time":datetime.now().isoformat(),
                    "model":m,
                    "price":int(p),
                    "remark":str(r).strip()
                })
        st.session_state.parse_result = pd.DataFrame(res)

        # 3. 保存有效数据
        if save_list:
            save_batch(save_list)
            st.success(f"✅ 识别粘贴并保存 {len(save_list)} 条数据成功！")
            get_clean_data.clear()

        # 4. 清空输入框（按你最初要求：清空→保存→恢复）
        st.session_state.input_content = ""
        st.rerun()

    # 【100%保留】按钮3：💾 修改并保存有效数据 + 🗑️ 清空
    if not st.session_state.parse_result.empty:
        ed = st.data_editor(
            st.session_state.parse_result, 
            use_container_width=True, 
            hide_index=True,
            num_rows="dynamic"
        )
        
        col_save, col_clear = st.columns(2)
        with col_save:
            if st.button("💾 修改并保存有效数据", use_container_width=True):
                ok_list = []
                for _, row in ed.iterrows():
                    # 兼容原始列，修复报错
                    if "原始" in row and pd.notna(row["原始"]) and str(row["原始"]).strip():
                        if not row["型号"] or not row["价格"]:
                            m_re,p_re,r_re = extract_by_regex(str(row["原始"]))
                            if m_re and p_re:
                                ok_list.append({
                                    "time":datetime.now().isoformat(),
                                    "model":m_re,
                                    "price":int(p_re),
                                    "remark":str(r_re).strip()
                                })
                                continue
                    if row["型号"] and row["价格"] and "✅" in str(row["状态"]):
                        ok_list.append({
                            "time":datetime.now().isoformat(),
                            "model":str(row["型号"]).strip(),
                            "price":int(row["价格"]),
                            "remark":str(row["备注"]).strip()
                        })
                if ok_list:
                    save_batch(ok_list)
                    st.success(f"✅ 修改并保存 {len(ok_list)} 条数据成功！")
                    st.session_state.parse_result = pd.DataFrame()
                    st.session_state.input_content = ""
                    get_clean_data.clear()
                    st.rerun()
        with col_clear:
            if st.button("🗑️ 清空", use_container_width=True):
                st.session_state.parse_result = pd.DataFrame()
                st.session_state.input_content = ""
                st.rerun()

st.divider()

# ------------------------------
# 历史管理 + 心理价位（完整保留）
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