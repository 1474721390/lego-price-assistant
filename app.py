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
    st.error("❌ 请配置完整环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价分析系统（最终版）", layout="wide")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 全量读取数据 ====================
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

# ==================== 数据清洗 ====================
@st.cache_data(ttl=120, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    # 严格乐高型号：5位，1-9开头
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"] > 0) & (df["价格"] < 10000)]
    return df

# ==================== 价格异常规则 ====================
def is_price_abnormal(price):
    return price < 10 or price > 5000

# ==================== 备注提取（无则空）====================
def extract_remark(line):
    box = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒"]
    bag = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "有袋", "无袋"]
    b = next((x for x in box if x in line), None)
    g = next((x for x in bag if x in line), None)
    parts = []
    if b: parts.append(b)
    if g: parts.append(g)
    return " + ".join(parts) if parts else ""

# ==================== 正则提取 ====================
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

# ==================== 大模型校验 ====================
def llm_verify(model, price, remark):
    if not is_price_abnormal(price):
        return True, "正常"
    prompt = f"""型号:{model} 价格:{price} 备注:{remark}
乐高正常价格10-5000元，判断是否有效。
返回JSON: {{"is_valid":true/false,"reason":"原因"}}"""
    try:
        r = requests.post("https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization":f"Bearer {ZHIPU_API_KEY}"},
            json={"model":"glm-4-flash","messages":[{"role":"user","content":prompt}]})
        j = r.json()
        res = json.loads(j["choices"][0]["message"]["content"])
        return res["is_valid"], res["reason"]
    except:
        return False, "模型异常"

# ==================== 波动预警 ====================
def get_alerts():
    df = get_clean_data()
    alerts = []
    for m in df["型号"].unique():
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)<2: continue
        d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
        if abs(d)>=10:
            alerts.append({"model":m,"diff":d,"last":s.iloc[-1]["价格"],"trend":"上涨"if d>0else"下跌"})
    return sorted(alerts, key=lambda x:abs(x["diff"]),reverse=True)

# ==================== 增删改 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except:
        return None
def update_record(record_id, new_data):
    try:
        return supabase.table("price_records").update(new_data).eq("id", record_id).execute()
    except:
        return None
def delete_record(record_id):
    try:
        return supabase.table("price_records").delete().eq("id", record_id).execute()
    except:
        return None

# ==================== UI 主界面 ====================
st.title("🧩 乐高报价分析系统（最终版）")

# ------------------------------
# 1. 预警区（可折叠 + 型号搜索）
# ------------------------------
with st.expander("📊 价格波动预警（≥10元，可折叠/查询）", expanded=False):
    alerts = get_alerts()
    if alerts:
        # 型号搜索框
        search_model = st.text_input("🔍 输入型号查询预警", placeholder="如：42202")
        # 过滤
        filtered_alerts = [a for a in alerts if not search_model.strip() or a["model"] == search_model.strip()]
        st.info(f"共 {len(alerts)} 个预警，当前展示 {len(filtered_alerts)} 个")
        # 渲染
        for a in filtered_alerts:
            if a["trend"] == "上涨":
                st.success(f"📈 {a['model']} 上涨{a['diff']}元 → 当前{a['last']}元")
            else:
                st.error(f"📉 {a['model']} 下跌{abs(a['diff'])}元 → 当前{a['last']}元")
    else:
        st.info("✅ 暂无价格波动预警")

# ------------------------------
# 2. 批量录入（解析后可编辑表格）
# ------------------------------
with st.expander("📝 批量录入报价（可折叠）", expanded=False):
    user_input = st.text_area("粘贴报价内容（每行一个）", height=220)
    
    # 初始化session_state存储解析结果
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()
    
    if st.button("🔍 解析数据", type="primary"):
        if not user_input.strip():
            st.warning("⚠️ 请输入报价内容")
        else:
            lines = user_input.strip().splitlines()
            parse_data = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                model, price, remark = extract_by_regex(line)
                if not model or not price:
                    parse_data.append({"型号":"","价格":"","备注":"","原始行":line,"状态":"❌ 解析失败"})
                    continue
                # 大模型校验
                is_valid, reason = llm_verify(model, price, remark)
                status = "✅ 有效" if is_valid else f"❌ 无效: {reason[:20]}"
                parse_data.append({
                    "型号": model,
                    "价格": price,
                    "备注": remark,
                    "原始行": line,
                    "状态": status
                })
            # 保存到session_state
            st.session_state.parse_result = pd.DataFrame(parse_data)
    
    # 展示可编辑表格
    if not st.session_state.parse_result.empty:
        st.subheader("📋 本次解析结果（可编辑/删除）")
        edited_df = st.data_editor(
            st.session_state.parse_result,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "型号": st.column_config.TextColumn("乐高型号", required=True),
                "价格": st.column_config.NumberColumn("报价", required=True),
                "备注": st.column_config.TextColumn("备注（可空）"),
                "原始行": st.column_config.TextColumn("原始输入", disabled=True),
                "状态": st.column_config.TextColumn("状态", disabled=True)
            },
            hide_index=True
        )
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 确认保存有效数据"):
                # 过滤有效数据
                valid_records = []
                for _, row in edited_df.iterrows():
                    if not row["型号"] or not row["价格"] or "❌" in str(row["状态"]):
                        continue
                    valid_records.append({
                        "time": datetime.now().isoformat(),
                        "model": str(row["型号"]).strip(),
                        "price": int(row["价格"]),
                        "remark": str(row["备注"]).strip()
                    })
                if valid_records:
                    res = save_batch(valid_records)
                    if res:
                        st.success(f"✅ 成功保存 {len(valid_records)} 条数据！")
                        # 清空解析结果
                        st.session_state.parse_result = pd.DataFrame()
                        get_clean_data.clear()
                        st.rerun()
                    else:
                        st.error("❌ 保存失败，请检查数据")
                else:
                    st.warning("⚠️ 无有效数据可保存")
        with col2:
            if st.button("🗑️ 清空解析结果"):
                st.session_state.parse_result = pd.DataFrame()
                st.rerun()

# ------------------------------
# 3. 历史数据管理（明细+编辑/删除）
# ------------------------------
st.markdown("---")
st.subheader("📋 历史数据管理（明细+编辑/删除/纠错）")

df_clean = get_clean_data()
if not df_clean.empty:
    # 型号筛选
    all_models = sorted(df_clean["型号"].unique())
    selected_model = st.selectbox("🔍 选择型号查看明细", [""] + all_models)
    
    if selected_model:
        # 筛选该型号数据
        model_data = df_clean[df_clean["型号"] == selected_model].sort_values("时间", ascending=False)
        st.subheader(f"{selected_model} 历史记录明细（共{len(model_data)}条）")
        
        # 准备可编辑表格
        display_df = model_data[["id", "时间", "型号", "价格", "remark"]].copy()
        display_df["时间"] = display_df["时间"].dt.strftime("%Y-%m-%d %H:%M:%S")
        display_df = display_df.rename(columns={"remark": "备注"})
        
        # 可编辑表格
        edited_df = st.data_editor(
            display_df,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "时间": st.column_config.TextColumn("时间", disabled=True),
                "型号": st.column_config.TextColumn("乐高型号", required=True),
                "价格": st.column_config.NumberColumn("报价", required=True),
                "备注": st.column_config.TextColumn("备注（可空）")
            },
            hide_index=True,
            key="history_editor"
        )
        
        # 保存修改
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 保存所有修改", type="primary"):
                success = 0
                for _, row in edited_df.iterrows():
                    update_data = {
                        "model": str(row["型号"]).strip(),
                        "price": int(row["价格"]),
                        "remark": str(row["备注"]).strip()
                    }
                    res = update_record(row["id"], update_data)
                    if res:
                        success += 1
                if success > 0:
                    st.success(f"✅ 成功更新 {success} 条数据！")
                    get_clean_data.clear()
                    st.rerun()
                else:
                    st.warning("⚠️ 无数据更新")
        
        # 批量删除
        with col2:
            ids_to_delete = st.multiselect(
                "🗑️ 选择要删除的记录ID",
                options=model_data["id"].tolist(),
                format_func=lambda x: f"ID {x} ({model_data[model_data['id']==x]['时间'].iloc[0].strftime('%m-%d %H:%M')})"
            )
            if st.button("🗑️ 删除选中记录"):
                for did in ids_to_delete:
                    delete_record(did)
                st.success(f"✅ 成功删除 {len(ids_to_delete)} 条记录！")
                get_clean_data.clear()
                st.rerun()
        
        # 价格趋势图
        st.subheader(f"{selected_model} 价格走势")
        fig = px.line(
            model_data.sort_values("时间"),
            x="时间",
            y="价格",
            title=f"{selected_model} 价格趋势",
            markers=True,
            template="plotly_white"
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("ℹ️ 暂无历史数据，请先录入报价")

st.caption("✅ 最终版：预警可查/录入可编辑/历史可纠错/全量数据")