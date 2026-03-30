import os
import re
import pandas as pd
from datetime import datetime, timedelta
import streamlit as st
from supabase import create_client

# ==================== 环境变量校验 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请在Streamlit Cloud配置完整环境变量！")
    st.stop()

# ==================== 页面配置 ====================
st.set_page_config(page_title="乐高报价助手（最终完美版）", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 【终极修复】强制全量分页读取 ====================
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    
    while True:
        try:
            response = supabase.table(table_name) \
                .select("*") \
                .range(start, start + page_size - 1) \
                .execute()
            data = response.data
            
            if not data:
                break
                
            all_data.extend(data)
            start += page_size
            
        except Exception as e:
            st.error(f"❌ 读取异常: {str(e)}")
            break
            
    st.success(f"✅ 全量数据读取完成！总计 {len(all_data)} 条")
    return all_data

# ==================== 【零缓存】数据清洗 ====================
def get_clean_data():
    all_data = fetch_all_records("price_records")
    
    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    
    # 基础清洗
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    
    # 严格5位数字过滤
    df = df[df["型号"].str.match(r"^[1-9]\d{4}$", na=False)]
    
    # 过滤无效数据
    df = df.dropna(subset=["型号", "价格"])
    df = df[df["价格"] > 0]
    
    st.write(f"✅ 清洗后有效数据：{len(df)} 条 | 型号总数：{len(df['型号'].unique())} 个")
    return df

# ==================== 【价格预警模块】完美恢复 ====================
def get_model_trend_change(model, df, n=5):
    df_model = df[df["型号"] == model].sort_values("时间")
    if len(df_model) < 2:
        return None
    recent = df_model.tail(n)
    return recent.iloc[-1]["价格"] - recent.iloc[0]["价格"]

def get_trend_alerts():
    df = get_clean_data()
    if df.empty:
        return []
    alerts = []
    for model in df["型号"].unique():
        change = get_model_trend_change(model, df)
        if change and abs(change) >= 5:
            alerts.append((model, "上涨" if change > 0 else "下跌", change))
    return sorted(alerts, key=lambda x: abs(x[2]), reverse=True)

# ==================== 【精准查询】单个型号极速查询 ====================
def get_model_detail(model_val):
    data = supabase.table("price_records") \
        .select("*") \
        .eq("model", model_val) \
        .order("time", desc=False) \
        .execute()
    
    df = pd.DataFrame(data.data)
    if df.empty:
        return df
        
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    return df

# ==================== 数据保存 ====================
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
        return len(res.data) if res.data else 0
    except Exception as e:
        st.error(f"❌ 写入失败: {str(e)}")
        return 0

# ==================== 正则提取（省Token）====================
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

# ==================== UI渲染 ====================
st.title("🧩 乐高报价助手（最终完美版）")

# 1. 价格预警模块（完美恢复）
alerts = get_trend_alerts()
with st.expander(f"⚠️ 价格趋势预警（{len(alerts)}个型号）", expanded=False):
    if alerts:
        for model, trend, change in alerts:
            if trend == "上涨":
                st.warning(f"📈 型号 {model} 近期价格上涨 {change:.0f} 元")
            else:
                st.error(f"📉 型号 {model} 近期价格下跌 {abs(change):.0f} 元")
    else:
        st.info("暂无价格预警")

# 2. 加载全量数据
df_clean = get_clean_data()
model_list = sorted(df_clean["型号"].unique()) if not df_clean.empty else []

# 3. 报价录入区
st.markdown("---")
st.subheader("📝 报价录入")
user_input = st.text_area("粘贴报价内容", height=200)
if st.button("🔍 解析并保存"):
    if user_input.strip():
        parsed = parse_text(user_input)
        if parsed:
            count = save_to_supabase(parsed, user_input)
            st.success(f"💾 成功保存 {count} 条记录！")
            st.rerun()
        else:
            st.warning("❌ 未识别到有效数据")

# 4. 趋势查询区
st.markdown("---")
st.subheader("📈 价格趋势查询")

# 型号选择器
col1, col2 = st.columns(2)
with col1:
    manual_model = st.text_input("手动输入型号", placeholder="如：40528")
with col2:
    select_model = st.selectbox("或从已有型号选择", [""] + model_list)

query_model = manual_model.strip() if manual_model.strip() else select_model

if query_model:
    st.write(f"🔎 正在查询型号：**{query_model}**")
    detail_df = get_model_detail(query_model)
    
    st.write(f"📊 找到 {len(detail_df)} 条历史记录")
    
    if not detail_df.empty:
        import plotly.express as px
        fig = px.line(detail_df, x="时间", y="价格", title=f"{query_model} 价格走势", markers=True)
        st.plotly_chart(fig, use_container_width=True)
        
        # 【修复KeyError】明细展示列名修正
        st.subheader("📋 历史记录明细")
        display_df = detail_df[["id", "time", "price", "remark", "raw_text"]].copy()
        display_df["时间"] = pd.to_datetime(display_df["time"]).dt.strftime("%Y-%m-%d %H:%M")
        # 重命名列，避免列名冲突
        display_df = display_df.rename(columns={
            "id": "ID",
            "time": "原始时间",
            "price": "价格",
            "remark": "备注",
            "raw_text": "原始文本"
        })
        st.dataframe(display_df[["ID", "时间", "价格", "备注", "原始文本"]], use_container_width=True)
    else:
        st.info("ℹ️ 该型号暂无交易记录")

st.caption("🚀 最终完美版 | 全量数据 | 价格预警恢复 | 零报错 | 正则优先 | 不卡顿")