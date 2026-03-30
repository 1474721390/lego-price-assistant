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

# ==================== 数据读取 ====================
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

# ==================== 规则函数（不变） ====================
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

def llm_verify(model, price, remark):
    if not is_price_abnormal(price):
        return True, "正常"
    prompt = f"""型号:{model} 价格:{price} 备注:{remark}
乐高正常价格10-5000元，判断是否有效。
返回JSON：{{"is_valid":true/false,"reason":"原因"}}"""
    try:
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=10
        )
        j = resp.json()
        res = json.loads(j["choices"][0]["message"]["content"])
        return res["is_valid"], res["reason"]
    except:
        return False, "模型异常"

def get_alerts():
    df = get_clean_data()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"] == m].sort_values("时间")
        if len(s) < 2: continue
        first = s.iloc[0]["价格"]
        last = s.iloc[-1]["价格"]
        diff = last - first
        if abs(diff) >= 10:
            alerts.append({
                "model": m, "diff": diff, "abs_diff": abs(diff), "last": last,
                "trend": "上涨" if diff > 0 else "下跌"
            })
    return sorted(alerts, key=lambda x: x["abs_diff"], reverse=True)

# ==================== 数据库操作（不变） ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except: return None
def update_record(record_id, new_data):
    try:
        return supabase.table("price_records").update(new_data).eq("id", record_id).execute()
    except: return None
def delete_record(record_id):
    try:
        return supabase.table("price_records").delete().eq("id", record_id).execute()
    except: return None

# ==================== 界面优化开始 ====================
st.title("🧩 乐高报价分析系统")
st.divider()

# ------------------------------
# 1. 预警区（界面更清爽，分列不变）
# ------------------------------
with st.expander("📊 价格波动预警（≥10元）", expanded=False):
    all_alerts = get_alerts()
    rise = [a for a in all_alerts if a["trend"] == "上涨"]
    fall = [a for a in all_alerts if a["trend"] == "下跌"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 📈 上涨预警")
        if rise:
            for a in rise:
                st.success(f"`{a['model']}` 上涨 **{a['abs_diff']}** 元 | 当前 {a['last']} 元")
        else:
            st.info("暂无上涨预警")
    with c2:
        st.markdown("#### 📉 下跌预警")
        if fall:
            for a in fall:
                st.error(f"`{a['model']}` 下跌 **{a['abs_diff']}** 元 | 当前 {a['last']} 元")
        else:
            st.info("暂无下跌预警")

st.divider()

# ------------------------------
# 2. 批量录入（卡片式优化）
# ------------------------------
with st.expander("📝 批量录入报价", expanded=False):
    user_input = st.text_area("粘贴内容（每行一个型号）", height=220)
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
                m, p, r = extract_by_regex(line)
                if not m or not p:
                    res.append({"型号": "", "价格": "", "备注": "", "原始行": line, "状态": "❌ 解析失败"})
                    continue
                ok, reason = llm_verify(m, p, r)
                status = "✅ 有效" if ok else f"❌ 无效"
                res.append({"型号": m, "价格": p, "备注": r, "原始行": line, "状态": status})
            st.session_state.parse_result = pd.DataFrame(res)

    if not st.session_state.parse_result.empty:
        st.markdown("### 📋 解析预览（可编辑）")
        edited = st.data_editor(
            st.session_state.parse_result,
            num_rows="dynamic", use_container_width=True, hide_index=True,
            column_config={
                "型号": st.column_config.TextColumn(required=True),
                "价格": st.column_config.NumberColumn(required=True),
                "备注": st.column_config.TextColumn(),
                "原始行": st.column_config.TextColumn(disabled=True),
                "状态": st.column_config.TextColumn(disabled=True)
            }
        )
        ca, cb = st.columns(2)
        with ca:
            if st.button("💾 保存有效数据", use_container_width=True):
                valid = []
                for _, row in edited.iterrows():
                    if row["型号"] and row["价格"] and "✅" in str(row["状态"]):
                        valid.append({
                            "time": datetime.now().isoformat(),
                            "model": str(row["型号"]).strip(),
                            "price": int(row["价格"]),
                            "remark": str(row["备注"]).strip()
                        })
                if valid:
                    save_batch(valid)
                    st.success(f"✅ 保存成功 {len(valid)} 条")
                    st.session_state.parse_result = pd.DataFrame()
                    get_clean_data.clear()
                    st.rerun()
        with cb:
            if st.button("🗑️ 清空预览", use_container_width=True):
                st.session_state.parse_result = pd.DataFrame()
                st.rerun()

st.divider()

# ------------------------------
# 3. 历史管理（更整洁）
# ------------------------------
st.markdown("### 📋 历史数据管理")
df_clean = get_clean_data()

if not df_clean.empty:
    models = sorted(df_clean["型号"].unique())
    sel = st.selectbox("选择型号", [""] + models, label_visibility="collapsed")
    if sel:
        data = df_clean[df_clean["型号"] == sel].sort_values("时间", ascending=False)
        st.caption(f"共 {len(data)} 条记录")

        show = data[["id", "时间", "型号", "价格", "remark"]].copy()
        show["时间"] = show["时间"].dt.strftime("%m-%d %H:%M")
        show.rename(columns={"remark": "备注"}, inplace=True)
        show.insert(0, "删除", False)

        edited = st.data_editor(
            show, use_container_width=True, hide_index=True,
            column_config={
                "id": "ID",
                "时间": st.column_config.TextColumn(disabled=True),
                "型号": st.column_config.TextColumn(required=True),
                "价格": st.column_config.NumberColumn(required=True),
                "备注": st.column_config.TextColumn(),
                "删除": st.column_config.CheckboxColumn("删除", default=False)
            }
        )

        if st.button("💾 保存修改 & 删除勾选", type="primary", use_container_width=True):
            del_ids = edited[edited["删除"] == True]["id"].tolist()
            for did in del_ids:
                delete_record(did)

            update_df = edited[edited["删除"] == False]
            for _, row in update_df.iterrows():
                update_record(row["id"], {
                    "model": str(row["型号"]).strip(),
                    "price": int(row["价格"]),
                    "remark": str(row["备注"]).strip()
                })
            st.success("✅ 操作完成")
            get_clean_data.clear()
            st.rerun()

        st.markdown("#### 📈 价格走势")
        fig = px.line(data.sort_values("时间"), x="时间", y="价格", markers=True)
        fig.update_layout(margin=dict(t=10, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无历史数据")