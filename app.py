import os
import re
import json
import requests
import pandas as pd
from datetime import datetime
import streamlit as st
from supabase import create_client

# ==================== 配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")
MODEL_NAME = "glm-4-flash"

if not all([SUPABASE_URL, SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("请配置完整环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价助手（海量版）", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 核心修复：强制获取全量数据 ====================
# 不管数据量多大（10万条），都能一次性完整捞取
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    
    # 使用循环不断获取，直到返回空
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
            st.write(f"📥 已加载 {len(all_data)} 条...") # 打印加载进度
            
            # 如果返回数据少于 page_size，说明到底了
            if len(data) < page_size:
                break
                
            start += page_size
            
        except Exception as e:
            st.error(f"❌ 查询异常: {e}")
            break
            
    st.success(f"✅ 数据加载完成！总计 {len(all_data)} 条")
    return all_data

# ==================== 数据清洗（专为 405xx 优化） ====================
@st.cache_data(ttl=60, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    
    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    
    # 基础处理
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    
    # 关键修复：移除 .0 尾巴
    df["型号"] = df["型号"].str.replace(r"\.0$", "", regex=True)
    
    # 过滤规则：只保留 5 位数字，且首位非0（包含 405xx、103xx、76xxx 等）
    # 正则 ^[1-9]\d{4}$ 完全匹配 40528
    df = df[df["型号"].str.match(r"^[1-9]\d{4}$", na=False)]
    
    # 去除无效行
    df = df.dropna(subset=["型号", "价格"])
    df = df[df["价格"] > 0]
    
    return df

# ==================== 单个型号查询（极速）====================
@st.cache_data(ttl=30, show_spinner=False)
def get_model_detail(model_val):
    # 直接按型号精确查询，避开全量加载的卡顿
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

# ==================== 保存逻辑 ====================
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
        get_clean_data.clear() # 清除缓存
        return len(res.data) if res.data else 0
    except Exception as e:
        st.error(f"写入失败: {e}")
        return 0

# ==================== 正则提取（省Token）====================
def extract_remark(line):
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "破损", "烂盒", "破盒"]
    bag_keywords = ["纸袋", "M袋", "礼袋", "礼品袋", "M号袋", "S袋", "+袋", "带袋", "有袋", "无袋"]
    
    box_found = next((kw for kw in box_keywords if kw in line), None)
    bag_found = next((kw for kw in bag_keywords if kw in line), None)
    
    if box_found and bag_found:
        return f"{box_found}+{bag_found}"
    elif box_found:
        return box_found
    elif bag_found:
        return "有袋"
    return ""

def extract_with_regex(line):
    line = line.strip()
    if not line:
        return None, None, None

    remark = extract_remark(line)
    # 移除备注干扰词
    cleaned = re.sub(r'好盒|压盒|瑕疵|盒损|破损|烂盒|破盒|纸袋|M袋|礼袋|礼品袋|M号袋|S袋|\+袋|带袋|有袋|无袋', '', line)
    
    # 严格匹配 5 位乐高型号（核心正则）
    model_match = re.search(r'(?<![0-9])([1-9]\d{4})(?![0-9])', cleaned)
    if not model_match:
        return None, None, None
    model = model_match.group(1)
    
    # 提取价格
    price_clean = cleaned.replace(model, "")
    price_match = re.search(r'(\d+)', price_clean)
    if not price_match:
        return None, None, None
        
    try:
        price = int(price_match.group(1))
        return model, price, remark if price > 0 else (None, None, None)
    except:
        return None, None, None

def parse_text(text):
    lines = text.strip().splitlines()
    results = []
    for line in lines:
        m, p, r = extract_with_regex(line)
        if m and p:
            results.append({"model": m, "price": p, "remark": r})
    return results

# ==================== UI 渲染 ====================
st.title("🧩 乐高报价助手 - 全量数据版")

# 1. 获取清洗后的数据
df_clean = get_clean_data()
model_list = sorted(df_clean["型号"].unique()) if not df_clean.empty else []

st.markdown("---")
st.subheader("📝 报价录入")
user_input = st.text_area("粘贴报价内容", height=200)

col1, col2 = st.columns(2)
with col1:
    if st.button("🔍 解析并保存"):
        if user_input.strip():
            parsed = parse_text(user_input)
            if parsed:
                count = save_to_supabase(parsed, user_input)
                st.success(f"💾 成功保存 {count} 条记录！")
            else:
                st.warning("❌ 未识别到有效数据")

# 2. 趋势查询区
st.markdown("---")
st.subheader("📈 价格趋势查询")

# 型号选择器
selected_model = st.selectbox("选择型号", [""] + model_list)

if selected_model:
    st.write(f"🔎 正在查询型号：**{selected_model}**")
    detail_df = get_model_detail(selected_model)
    
    st.write(f"📊 找到 {len(detail_df)} 条历史记录")
    
    if not detail_df.empty:
        import plotly.express as px
        fig = px.line(detail_df, x="时间", y="价格", title=f"{selected_model} 价格走势", markers=True)
        st.plotly_chart(fig, use_container_width=True)
        
        # 展示详细数据表格
        st.subheader("📋 历史记录明细")
        display_df = detail_df[["id", "time", "price", "remark", "raw_text"]].copy()
        display_df["时间"] = pd.to_datetime(display_df["时间"]).dt.strftime("%m-%d %H:%M")
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("ℹ️ 该型号暂无详细交易记录")

st.caption("🚀 已修复全量读取问题 | 正则优先 | 海量数据不卡顿")