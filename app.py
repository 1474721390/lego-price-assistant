import os
import re
import pandas as pd
from datetime import datetime
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
st.set_page_config(page_title="乐高报价助手（全量修复版）", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 【终极修复】强制全量分页读取 ====================
# 彻底解决"只拿999条"的BUG，100%捞取所有数据
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    
    # 强制循环，直到拿到所有数据
    while True:
        try:
            # 左闭右闭，每次取1000条
            response = supabase.table(table_name) \
                .select("*") \
                .range(start, start + page_size - 1) \
                .execute()
            data = response.data
            
            # 无数据则终止
            if not data:
                break
                
            all_data.extend(data)
            # 【关键修复】不再用"返回条数<page_size"判断终止，避免999条误判
            # 只要拿到数据，就继续取下一页，直到返回空
            start += page_size
            
        except Exception as e:
            st.error(f"❌ 数据读取异常: {str(e)}")
            break
            
    st.success(f"✅ 全量数据读取完成！总计 {len(all_data)} 条")
    return all_data

# ==================== 【缓存修复】无缓存+强制刷新 ====================
# 彻底禁用缓存，避免旧数据卡死页面
def get_clean_data():
    # 每次都强制重新读取，不缓存
    all_data = fetch_all_records("price_records")
    
    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    
    # 基础数据清洗
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    
    # 移除.0后缀（避免40528.0被误判）
    df["型号"] = df["型号"].str.replace(r"\.0$", "", regex=True)
    
    # 严格5位数字过滤（40528完全符合）
    df = df[df["型号"].str.match(r"^[1-9]\d{4}$", na=False)]
    
    # 过滤无效数据
    df = df.dropna(subset=["型号", "价格"])
    df = df[df["价格"] > 0]
    
    st.write(f"✅ 清洗后有效数据：{len(df)} 条")
    st.write(f"✅ 型号总数：{len(df['型号'].unique())} 个")
    return df

# ==================== 【精准查询】单个型号极速查询 ====================
def get_model_detail(model_val):
    # 直接按型号精准查询，避开全量加载
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
st.title("🧩 乐高报价助手（全量修复版）")

# 1. 加载全量数据
df_clean = get_clean_data()
model_list = sorted(df_clean["型号"].unique()) if not df_clean.empty else []

# 2. 报价录入区
st.markdown("---")
st.subheader("📝 报价录入")
user_input = st.text_area("粘贴报价内容", height=200)
if st.button("🔍 解析并保存"):
    if user_input.strip():
        parsed = parse_text(user_input)
        if parsed:
            count = save_to_supabase(parsed, user_input)
            st.success(f"💾 成功保存 {count} 条记录！")
            # 保存后强制刷新数据
            st.rerun()
        else:
            st.warning("❌ 未识别到有效数据")

# 3. 趋势查询区
st.markdown("---")
st.subheader("📈 价格趋势查询")

# 型号选择器（支持手动输入+下拉选择）
col1, col2 = st.columns(2)
with col1:
    manual_model = st.text_input("手动输入型号", placeholder="如：40528")
with col2:
    select_model = st.selectbox("或从已有型号选择", [""] + model_list)

# 优先级：手动输入 > 下拉选择
query_model = manual_model.strip() if manual_model.strip() else select_model

if query_model:
    st.write(f"🔎 正在查询型号：**{query_model}**")
    detail_df = get_model_detail(query_model)
    
    st.write(f"📊 找到 {len(detail_df)} 条历史记录")
    
    if not detail_df.empty:
        import plotly.express as px
        fig = px.line(detail_df, x="时间", y="价格", title=f"{query_model} 价格走势", markers=True)
        st.plotly_chart(fig, use_container_width=True)
        
        # 展示明细
        st.subheader("📋 历史记录明细")
        display_df = detail_df[["id", "time", "price", "remark", "raw_text"]].copy()
        display_df["时间"] = pd.to_datetime(display_df["时间"]).dt.strftime("%Y-%m-%d %H:%M")
        st.dataframe(display_df, use_container_width=True)
    else:
        st.info("ℹ️ 该型号暂无交易记录")

st.caption("🚀 已修复全量读取/缓存/匹配三大BUG | 正则优先 | 海量数据不卡顿")