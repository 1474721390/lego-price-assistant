# ===========================================
# 🔒 安全启动配置
# ===========================================
import os
os.environ["STREAMLIT_SERVER_RUNONSAVE"] = "false"
os.environ["STREAMLIT_SERVER_FOLDERWATCHBLACKLIST"] = ".*"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
os.environ["STREAMLIT_SERVER_ENABLE_WEBSOCKET_COMPRESSION"] = "true"

# ===========================================
# 标准导入
# ===========================================
import re
import json
import logging
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import plotly.express as px
from supabase import create_client

# ==================== 日志配置 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请配置环境变量")
    st.stop()

st.set_page_config(
    page_title="乐高报价系统",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==================== 自定义CSS美化 ====================
st.markdown("""
<style>
    /* 全局字体与背景 */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
        background-color: #f5f7fa;
    }
    /* 主标题 */
    .main-title {
        font-size: 2.5rem;
        font-weight: 700;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }
    /* 卡片样式 */
    .card {
        background-color: white;
        border-radius: 16px;
        padding: 24px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        margin-bottom: 20px;
        border: 1px solid #e9ecef;
    }
    /* 按钮样式 */
    .stButton > button {
        border-radius: 10px;
        font-weight: 500;
        transition: all 0.2s ease;
        border: none;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 16px rgba(0,0,0,0.1);
    }
    /* 主要按钮 */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
    }
    /* 输入框 */
    .stTextArea textarea {
        border-radius: 12px;
        border: 1px solid #d1d9e6;
        background-color: white;
    }
    /* 表格 */
    .stDataFrame {
        border-radius: 12px;
        overflow: hidden;
    }
    /* 标签页 */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 10px 20px;
        background-color: #f1f3f5;
    }
    .stTabs [aria-selected="true"] {
        background-color: white !important;
        font-weight: 600;
        box-shadow: 0 -2px 6px rgba(0,0,0,0.05);
    }
    /* 分割线 */
    hr {
        margin: 1.5rem 0;
        border-color: #e9ecef;
    }
    /* 信息提示框 */
    .info-box {
        background-color: #e7f5ff;
        border-left: 4px solid #339af0;
        padding: 16px;
        border-radius: 8px;
        margin: 16px 0;
    }
    /* 成功提示 */
    .success-box {
        background-color: #ebfbee;
        border-left: 4px solid #51cf66;
        padding: 16px;
        border-radius: 8px;
    }
    /* 预警卡片 */
    .alert-card {
        background-color: #fff9db;
        border-left: 4px solid #fcc419;
        padding: 12px 16px;
        border-radius: 8px;
        margin-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 会话状态初始化（极简稳定，避免 SessionInfo 错误） ====================
if "parse_result" not in st.session_state:
    st.session_state.parse_result = pd.DataFrame()
if "original_parse" not in st.session_state:
    st.session_state.original_parse = []
if "selected_model" not in st.session_state:
    st.session_state.selected_model = ""
if "scroll_to_bottom" not in st.session_state:
    st.session_state.scroll_to_bottom = False
if "parsing_in_progress" not in st.session_state:
    st.session_state.parsing_in_progress = False
if "saving_in_progress" not in st.session_state:
    st.session_state.saving_in_progress = False
if "parse_result_status_filter" not in st.session_state:
    st.session_state.parse_result_status_filter = "全部"

# ==================== Supabase 客户端 ====================
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 纯数据层缓存函数（绝不访问 session_state） ====================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_all_records_cached(table_name):
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

@st.cache_data(ttl=120, show_spinner=False)
def get_clean_data():
    all_data = fetch_all_records_cached("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
    df["原始时间"] = df["time"]
    df["时间"] = pd.to_datetime(df["time"], errors='coerce')
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"] > 0) & (df["价格"] < 100000)]
    return df

@st.cache_data(ttl=60, show_spinner=False)
def get_latest_history():
    df = get_clean_data()
    latest = {}
    if df.empty:
        return latest
    for m in df["型号"].unique():
        sub = df[df["型号"] == m].sort_values("时间", ascending=False)
        if not sub.empty:
            row = sub.iloc[0]
            latest[m] = {
                "price": row["价格"],
                "remark": str(row.get("remark", "")).strip(),
                "time": row["时间"].isoformat() if row["时间"] else ""
            }
    return latest

def get_all_price_records_df():
    all_data = fetch_all_records_cached("price_records")
    return pd.DataFrame(all_data) if all_data else pd.DataFrame()

@st.cache_data(ttl=60, show_spinner=False)
def get_price_rules_from_db():
    res = supabase.table("price_rules").select("model, buy, sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    return rules

# ==================== 业务函数 ====================
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
    fetch_all_records_cached.clear()

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()
    get_price_rules_from_db.clear()
    st.rerun()

def get_alert_threshold():
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

def set_alert_threshold(v):
    supabase.table("settings").upsert({"id": 1, "alert_threshold": v}, on_conflict="id").execute()

def extract_remark(line):
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒", "全新", "微压"]
    bag_keywords = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "M号袋", "S袋", "XL袋", "L袋", "大袋", "小袋", "有袋", "无袋", "袋子"]
    box = next((b for b in box_keywords if b in line), None)
    bag = next((bg for bg in bag_keywords if bg in line), None)
    if box and bag:
        return f"{box}+{bag}"
    elif box:
        return box
    elif bag:
        return bag
    return ""

def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    remark = extract_remark(line)
    all_digits = re.findall(r'\d+', line)
    if len(all_digits) < 2:
        return None, None, None
    model_candidates = [d for d in all_digits if len(d) == 5 and d[0] != '0']
    if not model_candidates:
        return None, None, None
    model = model_candidates[0]
    price_candidates = [int(p) for p in all_digits if p != model]
    valid_prices = [p for p in price_candidates if 10 <= p <= 8000]
    price = max(valid_prices) if valid_prices else (max(price_candidates) if price_candidates else None)
    if price is None:
        return None, None, None
    return model, price, remark

def extract_by_llm_batch(lines):
    if not lines:
        return []
    prompt = """你是乐高价格信息提取专家。请解析以下多条用户输入，每条输入可能包含乐高型号（5位数字）、价格（数字）和备注（盒况/袋况等）。
返回一个JSON数组，每个元素对应一条输入，格式为：
{"model": "字符串或null", "price": 数字或null, "remark": "字符串"}
若无法提取，model和price设为null。

输入文本列表：
"""
    for i, line in enumerate(lines):
        prompt += f"{i+1}. {line}\n"
    for attempt in range(2):
        try:
            response = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
                json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
                timeout=30
            )
            if response.status_code == 200:
                j = response.json()
                content = j["choices"][0]["message"]["content"].strip()
                json_match = re.search(r'\[.*\]', content, re.DOTALL)
                if json_match:
                    results = json.loads(json_match.group())
                    if isinstance(results, list) and len(results) == len(lines):
                        parsed = []
                        for res in results:
                            model = res.get("model")
                            price = res.get("price")
                            remark = res.get("remark", "")
                            if (isinstance(model, str) and len(model) == 5 and model.isdigit() and model[0] != '0'
                                and isinstance(price, (int, float)) and 10 <= price <= 8000):
                                parsed.append((model, int(price), str(remark)))
                            else:
                                parsed.append((None, None, ""))
                        return parsed
        except Exception as e:
            logger.error(f"批量AI调用异常：{e}")
            continue
    return [(None, None, "")] * len(lines)

def should_use_ai_fallback(model, price, line):
    latest = get_latest_history()
    if not (10 <= price <= 8000):
        return True
    if not (model and len(model) == 5 and model.isdigit() and model[0] != '0'):
        return True
    if model in latest:
        last_price = latest[model]["price"]
        if abs(price - last_price) > 200:
            return True
    return False

def batch_calculate_trends_and_changes(df_clean, model_price_pairs):
    results = {}
    models = list(set(m for m, _ in model_price_pairs))
    model_histories = {}
    for model in models:
        past = df_clean[df_clean["型号"] == model]
        if len(past) >= 2:
            past_sorted = past.sort_values("时间", ascending=False)
            model_histories[model] = past_sorted
    for model, current_price in model_price_pairs:
        if model not in model_histories:
            results[model] = {"trend": "—", "change": "—"}
            continue
        past_sorted = model_histories[model]
        last_price = past_sorted.iloc[1]["价格"]
        if current_price > last_price:
            trend = "📈"
        elif current_price < last_price:
            trend = "📉"
        else:
            trend = "—"
        diff = current_price - last_price
        change = f"+¥{diff}" if diff > 0 else f"-¥{abs(diff)}" if diff < 0 else "±¥0"
        results[model] = {"trend": trend, "change": change}
    return results

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
        trends.append({"model": m, "diff": diff, "abs_diff": abs(diff), "last": new})
    return sorted(trends, key=lambda x: -x["abs_diff"])

def save_batch_one_by_one(records):
    success_count = 0
    for record in records:
        try:
            supabase.table("price_records").insert(record).execute()
            success_count += 1
        except Exception as e:
            logger.error(f"保存失败: {e}")
            continue
    if success_count > 0:
        get_clean_data.clear()
        fetch_all_records_cached.clear()
    return success_count

def update_record(id, data):
    try:
        return supabase.table("price_records").update(data).eq("id", id).execute()
    except Exception as e:
        logger.error(f"更新失败 id={id}: {e}")
        return None

def delete_record(id):
    try:
        return supabase.table("price_records").delete().eq("id", id).execute()
    except Exception as e:
        logger.error(f"删除失败 id={id}: {e}")
        return None

def render_grid_buttons(items, columns=3, prefix=""):
    if not items:
        return
    for i in range(0, len(items), columns):
        cols = st.columns(columns)
        for j, col in enumerate(cols):
            idx = i + j
            if idx < len(items):
                label, model = items[idx]
                key = f"{prefix}_{model}_{idx}"
                if col.button(label, key=key, use_container_width=True):
                    st.session_state.selected_model = model
                    st.session_state.scroll_to_bottom = True

def paginate(items, page_size, current_page):
    start_idx = (current_page - 1) * page_size
    end_idx = start_idx + page_size
    return items[start_idx:end_idx]

# ==================== 主界面 ====================
st.markdown('<div class="main-title">🧩 乐高报价分析系统</div>', unsafe_allow_html=True)

# --- 批量录入卡片 ---
with st.container():
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("📝 批量录入报价")
    txt = st.text_area("粘贴报价文本（支持多行）", height=200, placeholder="例如：70收普快40675克隆人指挥官科迪 浙江\n1390收顺丰21341魔法屋 福建", key="batch_input_text")
    
    col1, col2, col3 = st.columns([1, 1, 4])
    with col1:
        parse_clicked = st.button("🔍 开始解析", type="primary", disabled=st.session_state.parsing_in_progress, use_container_width=True)
    with col2:
        if st.button("🧹 清空结果", use_container_width=True):
            st.session_state.parse_result = pd.DataFrame()
            st.session_state.original_parse = []
    
    if parse_clicked and not st.session_state.parsing_in_progress:
        st.session_state.parsing_in_progress = True
        try:
            if not txt or txt.strip() == "":
                st.warning("⚠️ 请输入内容")
            else:
                lines = txt.strip().splitlines()
                total_lines = len(lines)
                res = [None] * total_lines
                
                progress_bar = st.progress(0, text="🔍 正在解析...")
                status_text = st.empty()
                
                regex_results = []
                for idx, li in enumerate(lines):
                    m, p, r = extract_by_regex(li)
                    regex_results.append((m, p, r, li))
                    if not m or not p:
                        res[idx] = {"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 解析失败"}
                
                progress_bar.progress(0.3, text="🤔 检查可疑项...")
                ai_indices = []
                for idx, (m, p, r, li) in enumerate(regex_results):
                    if m and p and should_use_ai_fallback(m, p, li):
                        ai_indices.append(idx)
                
                if ai_indices:
                    progress_bar.progress(0.5, text=f"🤖 批量调用AI处理 {len(ai_indices)} 条可疑数据...")
                    ai_lines = [regex_results[i][3] for i in ai_indices]
                    ai_results = extract_by_llm_batch(ai_lines)
                    for i, idx in enumerate(ai_indices):
                        ai_model, ai_price, ai_remark = ai_results[i]
                        m_old, p_old, r_old, li = regex_results[idx]
                        if ai_model and ai_price:
                            res[idx] = {"型号": ai_model, "价格": ai_price, "备注": ai_remark, "原始": li, "状态": "✅ 有效（AI修正）"}
                        else:
                            res[idx] = {"型号": m_old, "价格": p_old, "备注": r_old, "原始": li, "状态": "⚠️ 需手动核实"}
                
                for idx, (m, p, r, li) in enumerate(regex_results):
                    if res[idx] is None:
                        res[idx] = {"型号": m, "价格": p, "备注": r, "原始": li, "状态": "✅ 有效"}
                
                progress_bar.progress(0.8, text="🗂️ 去重与当日检查...")
                valid_entries = []
                for entry in res:
                    if entry and entry["型号"] and entry["价格"] > 0:
                        valid_entries.append({"model": entry["型号"], "price": entry["价格"], "remark": entry["备注"], "raw": entry["原始"]})
                
                unique_batch = {}
                for item in valid_entries:
                    key = f"{item['model']}_{item['price']}_{item['remark']}"
                    if key not in unique_batch:
                        unique_batch[key] = item
                
                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                today_str = today.strftime("%Y-%m-%d")
                all_records_df = get_all_price_records_df()
                today_set = set()
                for _, row in all_records_df.iterrows():
                    time_str = row.get("time", "")
                    if time_str and time_str[:10] == today_str:
                        today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))
                
                save_list = []
                for key, item in unique_batch.items():
                    m, p, r = item["model"], item["price"], item["remark"]
                    if (m, p, r) in today_set:
                        for idx, entry in enumerate(res):
                            if entry and entry.get("型号") == m and entry.get("价格") == p:
                                res[idx]["状态"] = "⏭️ 已跳过（当天重复）"
                                break
                        continue
                    status = next((e["状态"] for e in res if e and e.get("型号") == m and e.get("价格") == p), "")
                    if "✅ 有效" in status:
                        save_list.append({"time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                          "model": m, "price": int(p), "remark": str(r).strip()})
                        today_set.add((m, p, r))
                
                progress_bar.progress(1.0, text="✅ 解析完成")
                status_text.empty()
                progress_bar.empty()
                
                res_filtered = [r for r in res if r is not None]
                priority_order = {"⚠️ 需手动核实":1, "❌ 解析失败":2, "✅ 有效（AI修正）":3, "✅ 有效":4, "⏭️ 已跳过（当天重复）":5}
                res_sorted = sorted(res_filtered, key=lambda x: priority_order.get(x.get("状态", ""), 99))
                
                st.session_state.parse_result = pd.DataFrame(res_sorted)
                st.session_state.original_parse = res_sorted.copy()
                
                if save_list:
                    with st.spinner(f"💾 正在保存 {len(save_list)} 条有效数据..."):
                        saved_count = save_batch_one_by_one(save_list)
                    st.success(f"✅ 解析并自动保存 {saved_count} 条有效数据")
                else:
                    if res_filtered:
                        st.info("📭 解析完成，没有新的有效数据需要保存")
                    else:
                        st.warning("⚠️ 没有解析到任何有效数据")
        except Exception as e:
            logger.error(f"解析异常: {e}")
            st.error(f"❌ 解析出错：{e}")
        finally:
            st.session_state.parsing_in_progress = False
    
    st.markdown('</div>', unsafe_allow_html=True)

# --- 解析结果展示 ---
parse_df = st.session_state.parse_result
if not parse_df.empty:
    with st.container():
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.subheader("📋 解析结果")
        status_counts = parse_df["状态"].value_counts().to_dict()
        
        # 筛选器
        col_f1, col_f2 = st.columns([3, 1])
        with col_f1:
            options = ["全部"] + list(status_counts.keys())
            current = st.session_state.parse_result_status_filter
            if current not in options:
                current = "全部"
            selected = st.selectbox("📌 按状态筛选", options, index=options.index(current), key="status_filter")
            st.session_state.parse_result_status_filter = selected
        
        filter_status = st.session_state.parse_result_status_filter
        filtered = parse_df if filter_status == "全部" else parse_df[parse_df["状态"] == filter_status]
        
        if not filtered.empty:
            df_trend = filtered.copy()
            valid_rows = df_trend[(df_trend["型号"] != "") & (df_trend["价格"] > 0)]
            if not valid_rows.empty:
                df_clean = get_clean_data()
                pairs = list(zip(valid_rows["型号"], valid_rows["价格"]))
                trends = batch_calculate_trends_and_changes(df_clean, pairs)
                df_trend["趋势"] = "—"
                df_trend["涨跌"] = "—"
                for idx, row in valid_rows.iterrows():
                    if row["型号"] in trends:
                        df_trend.at[idx, "趋势"] = trends[row["型号"]]["trend"]
                        df_trend.at[idx, "涨跌"] = trends[row["型号"]]["change"]
            else:
                df_trend["趋势"] = "—"
                df_trend["涨跌"] = "—"
            
            display_cols = ["型号", "价格", "趋势", "涨跌", "备注", "原始", "状态"]
            edited_df = st.data_editor(
                df_trend[display_cols],
                column_config={
                    "型号": st.column_config.TextColumn("型号", required=True),
                    "价格": st.column_config.NumberColumn("价格", required=True, min_value=0),
                    "趋势": st.column_config.TextColumn("趋势", disabled=True),
                    "涨跌": st.column_config.TextColumn("涨跌", disabled=True),
                    "备注": st.column_config.TextColumn("备注"),
                    "原始": st.column_config.TextColumn("原始", disabled=True),
                    "状态": st.column_config.TextColumn("状态", disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                height=min(400, 35*len(df_trend)+38),
                key="data_editor"
            )
            
            total = len(parse_df)
            valid = status_counts.get("✅ 有效", 0) + status_counts.get("✅ 有效（AI修正）", 0)
            ai = status_counts.get("✅ 有效（AI修正）", 0)
            manual = status_counts.get("⚠️ 需手动核实", 0)
            failed = status_counts.get("❌ 解析失败", 0)
            skipped = status_counts.get("⏭️ 已跳过（当天重复）", 0)
            st.markdown(f"📊 **本轮统计**：总 {total} 条 ｜ ✅ 有效 {valid} ｜ 🤖 AI修正 {ai} ｜ ✏️ 需手动 {manual} ｜ ❌ 失败 {failed} ｜ ⏭️ 跳过 {skipped}")
            
            save_btn = st.button("💾 保存修改后的数据", type="primary", disabled=st.session_state.saving_in_progress)
            if save_btn and not st.session_state.saving_in_progress:
                st.session_state.saving_in_progress = True
                original_dict = {i: row for i, row in enumerate(st.session_state.original_parse)}
                to_save = []
                for idx, (_, row) in enumerate(edited_df.iterrows()):
                    orig = original_dict.get(idx, {})
                    if (row["型号"] != orig.get("型号", "") or row["价格"] != orig.get("价格", 0) or row["备注"] != orig.get("备注", "")):
                        if row["型号"] and len(str(row["型号"]))==5 and str(row["型号"]).isdigit() and row["价格"]>=10:
                            to_save.append({"model": str(row["型号"]), "price": int(row["价格"]), "remark": str(row["备注"]).strip()})
                if to_save:
                    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                    all_records = get_all_price_records_df()
                    today_set = set()
                    for _, r in all_records.iterrows():
                        if r.get("time", "")[:10] == today.strftime("%Y-%m-%d"):
                            today_set.add((r["model"], r["price"], str(r.get("remark", "")).strip()))
                    final = [i for i in to_save if (i["model"], i["price"], i["remark"]) not in today_set]
                    if final:
                        records = [{"time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                                    "model": i["model"], "price": i["price"], "remark": i["remark"]} for i in final]
                        saved = save_batch_one_by_one(records)
                        st.success(f"✅ 成功保存 {saved} 条修正数据")
                        st.session_state.parse_result = pd.DataFrame()
                        st.session_state.original_parse = []
                    else:
                        st.info("所有修改数据当天已存在")
                else:
                    st.warning("没有检测到有效修改")
                st.session_state.saving_in_progress = False
        else:
            st.info("当前筛选条件下无数据")
        st.markdown('</div>', unsafe_allow_html=True)

# --- 设置卡片 ---
with st.expander("⚙️ 系统设置", expanded=False):
    th = get_alert_threshold()
    new_th = st.number_input("⚠️ 价格波动预警阈值（元）", min_value=1, value=th)
    if new_th != th:
        set_alert_threshold(new_th)

# --- 主要功能选项卡（美化后）---
df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []
tab1, tab2, tab3, tab4 = st.tabs(["⭐ 我的收藏", "📈 涨跌排行", "📊 价格预警", "🔎 价格筛选"])

with tab1:
    st.markdown("#### ❤️ 收藏型号快速查看")
    favs = get_favorites()
    if favs:
        fav_items = []
        for m in favs:
            s = df[df["型号"]==m]
            if len(s)>=2:
                s = s.sort_values("时间")
                d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
                icon = "📈" if d>0 else "📉"
                fav_items.append((f"{m} {icon} {d:+}元 | 现价 ¥{s.iloc[-1]['价格']}", m))
            else:
                fav_items.append((f"{m} (数据不足)", m))
        render_grid_buttons(fav_items, columns=1, prefix="fav")
    else:
        st.info("暂无收藏，在历史管理中点击☆添加")

with tab2:
    st.markdown("#### 📊 近7日 / 近30日 波动 TOP10")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**近7天涨幅**")
        t7 = get_trend(7)
        if t7:
            render_grid_buttons([(f"{i['model']} {i['diff']:+}元 | 现价 ¥{i['last']}", i['model']) for i in t7[:10]], 1, "t7")
    with c2:
        st.markdown("**近30天涨幅**")
        t30 = get_trend(30)
        if t30:
            render_grid_buttons([(f"{i['model']} {i['diff']:+}元 | 现价 ¥{i['last']}", i['model']) for i in t30[:10]], 1, "t30")

with tab3:
    st.markdown("#### 🚨 价格波动预警")
    min_p = st.number_input("最低价格", 0, 100, 0, 10, key="alert_min")
    max_p = st.number_input("最高价格", 0, 100, 100, 10, key="alert_max")
    alerts = get_alerts()
    if alerts:
        filtered = [a for a in alerts if (min_p <= a["last"] <= max_p if max_p>0 else a["last"]>=min_p)]
        up = sorted([a for a in filtered if a["trend"]=="上涨"], key=lambda x: -x["abs_diff"])
        down = sorted([a for a in filtered if a["trend"]=="下跌"], key=lambda x: -x["abs_diff"])
        cu, cd = st.columns(2)
        with cu:
            st.markdown("##### 📈 涨价榜")
            if up: render_grid_buttons([(f"{a['model']} +{a['abs_diff']}元", a['model']) for a in up], 1, "up")
        with cd:
            st.markdown("##### 📉 跌价榜")
            if down: render_grid_buttons([(f"{a['model']} -{a['abs_diff']}元", a['model']) for a in down], 1, "down")
    else:
        st.info("暂无预警")

with tab4:
    st.markdown("#### 🔎 价格区间筛选")
    min_p2 = st.number_input("最低价", 0, 100, 0, 10, key="filter_min")
    max_p2 = st.number_input("最高价", 0, 100, 100, 10, key="filter_max")
    if min_p2 < max_p2 or max_p2 == 0:
        if not df.empty:
            latest = df.sort_values('时间').groupby('型号').tail(1)
            if max_p2 > 0:
                latest = latest[(latest['价格'] >= min_p2) & (latest['价格'] <= max_p2)]
            else:
                latest = latest[latest['价格'] >= min_p2]
            latest = latest.sort_values('价格', ascending=False)
            if not latest.empty:
                items = [(f"{r['型号']} | ¥{r['价格']}", r['型号']) for _,r in latest.iterrows()]
                page = st.selectbox("每页", [10,20,50], 1, key="page_size")
                total_pages = max(1, (len(items)+page-1)//page)
                cur = st.slider("页码", 1, total_pages, 1, key="page_slider")
                render_grid_buttons(paginate(items, page, cur), 2, "filter")
                st.caption(f"共 {len(items)} 条，第 {cur}/{total_pages} 页")

# --- 历史数据管理（底部）---
st.divider()
st.subheader("📋 型号详情与历史管理")
if not df.empty:
    idx = 0
    if st.session_state.selected_model in all_models:
        idx = all_models.index(st.session_state.selected_model) + 1
    target = st.selectbox("选择型号", [""] + all_models, index=idx)
    if target:
        st.session_state.selected_model = target
        isfav = target in get_favorites()
        if st.button("⭐ 取消收藏" if isfav else "☆ 收藏"):
            toggle_favorite(target)
        
        rules = get_price_rules_from_db()
        rule = rules.get(target, {"buy":0, "sell":0})
        c1, c2 = st.columns(2)
        with c1: b = st.number_input("💚 收货心理价", value=rule["buy"])
        with c2: s = st.number_input("❤️ 出货心理价", value=rule["sell"])
        if st.button("💾 保存心理价位"):
            save_price_rule(target, b, s)
        
        model_data = df[df["型号"]==target].sort_values("时间", ascending=False)
        if not model_data.empty:
            cur = model_data.iloc[0]["价格"]
            if s>0 and cur>=s: st.info(f"当前价 ¥{cur} → 可出货")
            elif b>0 and cur<=b: st.info(f"当前价 ¥{cur} → 可收货")
            
            show = model_data[["id", "原始时间", "型号", "价格", "remark"]].copy()
            show["日期"] = show["原始时间"].str[:10]
            show.rename(columns={"remark":"备注"}, inplace=True)
            show.insert(0, "删除", False)
            edited = st.data_editor(show, column_config={"删除": st.column_config.CheckboxColumn()}, hide_index=True, num_rows="fixed")
            if st.button("保存修改 & 删除"):
                del_ids = edited[edited["删除"]]["id"].tolist()
                for did in del_ids: delete_record(did)
                for _, row in edited[~edited["删除"]].iterrows():
                    update_record(row["id"], {"model": row["型号"], "price": row["价格"], "remark": row["备注"]})
                get_clean_data.clear()
                fetch_all_records_cached.clear()
                st.success("✅ 操作成功")
            
            fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
            fig.update_layout(margin=dict(l=20, r=20, t=30, b=20))
            st.plotly_chart(fig, use_container_width=True)

if st.session_state.scroll_to_bottom:
    st.components.v1.html("<script>window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});</script>", height=0)
    st.session_state.scroll_to_bottom = False
