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

st.set_page_config(page_title="乐高报价助手（备注可空版）", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 全量读取数据（不卡顿）====================
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        try:
            res = supabase.table(table_name).select("*").range(start, start + page_size - 1).execute()
            data = res.data
            if not data:
                break
            all_data.extend(data)
            start += page_size
        except Exception as e:
            st.error(f"❌ 读取数据失败: {str(e)}")
            break
    return all_data

# ==================== 数据清洗（严格乐高规则）====================
@st.cache_data(ttl=120, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    
    df = pd.DataFrame(all_data)
    # 基础类型转换
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    
    # 严格乐高型号规则：5位数字，1~9开头，不允许0开头
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    
    # 过滤无效数据
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"] > 0) & (df["价格"] < 10000)]
    
    st.success(f"✅ 数据加载完成！共 {len(df)} 条有效记录")
    return df

# ==================== 价格异常校验规则（你要求）====================
def is_price_abnormal(price):
    """价格 <10元 或 >5000元 判定为异常，必须大模型审核"""
    return price < 10 or price > 5000

# ==================== 备注提取（核心调整：识别不到则留空）====================
def extract_remark(line):
    """
    只识别以下两类内容，识别不到则返回空字符串
    盒子类：好盒、压盒、瑕疵、盒损、烂盒、破盒
    袋子类：纸袋、M袋、S袋、礼袋、礼品袋、有袋、无袋
    """
    line = line.strip().lower()
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒"]
    bag_keywords = ["纸袋", "m袋", "s袋", "礼袋", "礼品袋", "有袋", "无袋"]
    
    box_found = None
    bag_found = None
    
    # 识别盒子
    for kw in box_keywords:
        if kw in line:
            box_found = kw
            break
    
    # 识别袋子
    for kw in bag_keywords:
        if kw in line:
            bag_found = kw
            break
    
    # 组合备注：有盒子+有袋子才拼接，否则只填存在的，都不存在则返回空
    parts = []
    if box_found:
        parts.append(box_found)
    if bag_found:
        parts.append(bag_found)
    
    return " + ".join(parts) if parts else ""  # 关键调整：识别不到返回空字符串

# ==================== 正则提取型号和价格（严格5位1~9开头）====================
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    
    # 先提取备注
    remark = extract_remark(line)
    
    # 清洗文本：移除备注关键词，避免干扰型号提取
    all_keywords = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒", 
                    "纸袋", "m袋", "s袋", "礼袋", "礼品袋", "有袋", "无袋"]
    cleaned_line = line
    for kw in all_keywords:
        cleaned_line = cleaned_line.replace(kw, "")
    
    # 严格匹配乐高型号：5位数字，1~9开头，前后无数字干扰
    model_match = re.search(r'(?<![0-9])([1-9]\d{4})(?![0-9])', cleaned_line)
    if not model_match:
        return None, None, None
    
    model = model_match.group(1)
    
    # 提取价格
    price_clean = cleaned_line.replace(model, "")
    price_match = re.search(r'(\d+)', price_clean)
    if not price_match:
        return None, None, None
    
    try:
        price = int(price_match.group(1))
        return model, price, remark
    except:
        return None, None, None

# ==================== 大模型分析（异常价格必调用）====================
def llm_verify_price(model, price, remark):
    """价格异常时调用大模型校验，正常价格直接返回正常"""
    if not is_price_abnormal(price):
        return True, "✅ 价格正常（无需大模型校验）"
    
    # 异常价格 → 大模型审核
    prompt = f"""
你是专业乐高价格风控专家，严格按照乐高官网规则判断报价有效性。

【乐高型号规则】
- 必须是5位数字，以1-9开头，绝对不会以0开头（如40528、10300、76218）

【价格规则】
- 乐高正品正常价格范围：10元 ~ 5000元
- 价格 <10元 或 >5000元 几乎都是输入错误，判定为无效报价

【当前待审核信息】
型号：{model}
报价：{price}元
备注：{remark if remark else "无"}

请严格判断：该报价是否为真实有效？
只输出 JSON 格式（不要任何其他内容）：
{{
  "is_valid": true/false,
  "reason": "详细原因说明"
}}
"""

    try:
        resp = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}"},
            json={
                "model": "glm-4-flash",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1
            },
            timeout=15
        )
        
        if resp.status_code != 200:
            return False, f"❌ 模型请求失败（状态码：{resp.status_code}）"
        
        res = resp.json()
        content = res["choices"][0]["message"]["content"]
        
        # 解析模型返回
        try:
            result = json.loads(content)
            return result["is_valid"], result["reason"]
        except json.JSONDecodeError:
            return False, f"❌ 模型返回解析失败：{content[:50]}..."
    
    except Exception as e:
        return False, f"❌ 模型调用异常：{str(e)[:50]}"

# ==================== 价格波动预警（完全恢复你最初的版本）====================
def get_price_alerts():
    """检测价格波动≥10元的型号，返回预警列表"""
    df = get_clean_data()
    if df.empty:
        return []
    
    alerts = []
    for model in df["型号"].unique():
        # 按时间排序，取该型号所有记录
        sub_df = df[df["型号"] == model].sort_values("时间")
        if len(sub_df) < 2:
            continue  # 至少2条记录才计算波动
        
        # 首条和最新价格
        first_price = sub_df.iloc[0]["价格"]
        latest_price = sub_df.iloc[-1]["价格"]
        price_diff = latest_price - first_price
        
        # 波动≥10元触发预警
        if abs(price_diff) >= 10:
            alerts.append({
                "model": model,
                "diff": price_diff,
                "latest_price": latest_price,
                "trend": "上涨" if price_diff > 0 else "下跌"
            })
    
    # 按波动幅度降序排序
    return sorted(alerts, key=lambda x: abs(x["diff"]), reverse=True)

# ==================== 数据保存（批量插入）====================
def save_batch_records(records):
    """批量保存有效记录，返回成功条数"""
    if not records:
        return 0
    
    try:
        res = supabase.table("price_records").insert(records).execute()
        return len(res.data) if res.data else 0
    except Exception as e:
        st.error(f"❌ 批量保存失败：{str(e)}")
        return 0

# ==================== UI 界面渲染 ====================
st.title("🧩 乐高报价分析系统（备注可空版）")

# 1. 价格波动预警（完全恢复）
st.subheader("📊 价格波动预警")
alerts = get_price_alerts()
if alerts:
    st.info(f"共检测到 {len(alerts)} 个型号价格波动≥10元")
    for alert in alerts:
        model = alert["model"]
        diff = alert["diff"]
        trend = alert["trend"]
        price = alert["latest_price"]
        
        if trend == "上涨":
            st.success(f"📈 {model} 【{trend}】{abs(diff)} 元 → 当前价格 {price} 元")
        else:
            st.error(f"📉 {model} 【{trend}】{abs(diff)} 元 → 当前价格 {price} 元")
else:
    st.info("✅ 暂无价格波动≥10元的型号")

st.markdown("---")

# 2. 批量录入区
st.subheader("📝 批量录入报价（支持多行）")
user_input = st.text_area(
    "粘贴报价内容（每行一个型号，支持备注）",
    height=250,
    placeholder="示例：40528 好盒 礼品袋 350元\n示例：10300 压盒 280元\n示例：76218 300元（无备注）"
)

if st.button("🔍 解析并保存数据", type="primary"):
    if not user_input.strip():
        st.warning("⚠️ 请输入报价内容")
    else:
        lines = user_input.strip().splitlines()
        valid_records = []
        total_skipped = 0
        
        with st.spinner("正在解析并校验数据..."):
            for line in lines:
                line = line.strip()
                if not line:
                    total_skipped += 1
                    continue
                
                # 解析型号、价格、备注
                model, price, remark = extract_by_regex(line)
                
                if not model or not price:
                    total_skipped += 1
                    continue
                
                # 大模型校验异常价格
                is_valid, reason = llm_verify_price(model, price, remark)
                if not is_valid:
                    st.warning(f"❌ {model} {price}元 → {reason}")
                    total_skipped += 1
                    continue
                
                # 组装有效记录（备注为空也会保存）
                valid_records.append({
                    "model": model,
                    "price": price,
                    "remark": remark,  # 可空
                    "time": datetime.now().isoformat()
                })
        
        # 保存数据
        if valid_records:
            success_count = save_batch_records(valid_records)
            st.success(f"✅ 成功保存 {success_count} 条数据！")
        
        # 统计信息
        st.info(f"📊 解析完成：成功保存 {len(valid_records)} 条，跳过 {total_skipped} 行")
        
        # 刷新数据
        st.rerun()

st.markdown("---")

# 3. 型号查询区
st.subheader("🔍 单个型号价格查询")
df_clean = get_clean_data()
if not df_clean.empty:
    models = sorted(df_clean["型号"].unique())
    selected_model = st.selectbox("选择乐高型号", [""] + models)
    
    if selected_model:
        # 筛选该型号数据
        model_data = df_clean[df_clean["型号"] == selected_model].sort_values("时间")
        st.write(f"📦 型号 {selected_model} 共 {len(model_data)} 条历史记录")
        
        # 价格趋势图
        fig = px.line(
            model_data,
            x="时间",
            y="价格",
            title=f"{selected_model} 价格走势",
            markers=True,
            template="plotly_white"
        )
        fig.update_layout(
            xaxis_title="时间",
            yaxis_title="价格（元）",
            hovermode="x unified"
        )
        st.plotly_chart(fig, use_container_width=True)
        
        # 明细表格
        st.subheader(f"📋 {selected_model} 历史记录明细")
        display_df = model_data[["时间", "型号", "价格", "remark"]].copy()
        display_df["时间"] = display_df["时间"].dt.strftime("%Y-%m-%d %H:%M:%S")
        # 重命名列，更友好展示
        display_df = display_df.rename(columns={
            "型号": "乐高型号",
            "价格": "报价（元）",
            "remark": "备注"
        })
        st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("ℹ️ 暂无有效数据，请先录入报价")

st.markdown("---")
st.caption("🧩 乐高报价助手 | 严格遵循乐高型号规则 | 备注可空 | 大模型异常校验 | 全量数据")