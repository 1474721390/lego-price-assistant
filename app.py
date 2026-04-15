# ===========================================
# 🔒 安全启动配置（必须放在最顶部！）
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
import time

# ==================== 日志配置 ====================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ==================== 会话状态管理器 ====================
class SessionStateManager:
    _initialized = False

    @classmethod
    def _force_init(cls):
        if cls._initialized:
            return
        defaults = {
            "selected_model": "",
            "scroll_to_bottom": False,
            "parse_result": pd.DataFrame(),
            "original_parse": [],
            "pending_cache_clear": False,
            "current_page_tab4": 1,
            "parsing_in_progress": False,
            "saving_in_progress": False,
            "temp_price_rules": {},
            "clear_parse_result": False,
            "parse_result_status_filter": "全部",
            "trigger_parse": False,
        }
        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value
        cls._initialized = True

    @classmethod
    def safe_get(cls, key, default=None):
        try:
            if not cls._initialized:
                cls._force_init()
            return st.session_state.get(key, default)
        except Exception:
            return default

    @classmethod
    def safe_set(cls, key, value):
        try:
            if not cls._initialized:
                cls._force_init()
            st.session_state[key] = value
        except Exception:
            pass

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请配置环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价系统", layout="wide")

try:
    SessionStateManager._force_init()
except Exception:
    pass

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 数据层缓存函数 ====================
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
def _get_price_rules_from_db():
    res = supabase.table("price_rules").select("model, buy, sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    return rules

def get_price_rules():
    try:
        rules = _get_price_rules_from_db().copy()
    except Exception:
        rules = {}
    temp = SessionStateManager.safe_get("temp_price_rules", {})
    rules.update(temp)
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
    temp = SessionStateManager.safe_get("temp_price_rules", {})
    temp[model] = {"buy": buy, "sell": sell}
    SessionStateManager.safe_set("temp_price_rules", temp)
    _get_price_rules_from_db.clear()
    st.rerun()

def get_alert_threshold():
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

def set_alert_threshold(v):
    supabase.table("settings").upsert({"id": 1, "alert_threshold": v}, on_conflict="id").execute()
    st.rerun()

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
    max_retries = 2
    for attempt in range(max_retries):
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
            else:
                logger.warning(f"批量AI调用失败，状态码：{response.status_code}")
                continue
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
        SessionStateManager.safe_set("pending_cache_clear", True)
    return success_count

def update_record(id, data):
    try:
        return supabase.table("price_records").update(data).eq("id", id).execute()
    except Exception as e:
        logger.error(f"更新记录失败 id={id}: {e}")
        return None

def delete_record(id):
    try:
        return supabase.table("price_records").delete().eq("id", id).execute()
    except Exception as e:
        logger.error(f"删除记录失败 id={id}: {e}")
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
                    SessionStateManager.safe_set("selected_model", model)
                    SessionStateManager.safe_set("scroll_to_bottom", True)

def paginate(items, page_size, current_page):
    start_idx = (current_page - 1) * page_size
    end_idx = start_idx + page_size
    return items[start_idx:end_idx]

def smart_cache_clear():
    if SessionStateManager.safe_get("pending_cache_clear", False):
        try:
            get_clean_data.clear()
            fetch_all_records_cached.clear()
            _get_price_rules_from_db.clear()
            st.cache_data.clear()
            SessionStateManager.safe_set("pending_cache_clear", False)
        except RuntimeError:
            pass

# ==================== 新增：快速采纳函数 ====================
def quick_adopt_record(model, price, remark):
    """将单条记录保存到数据库，并标记为手动采纳"""
    try:
        record = {
            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "model": str(model).strip(),
            "price": int(price),
            "remark": str(remark).strip()
        }
        supabase.table("price_records").insert(record).execute()
        SessionStateManager.safe_set("pending_cache_clear", True)
        return True
    except Exception as e:
        logger.error(f"快速采纳失败: {e}")
        return False

# ==================== 主界面 ====================
st.title("🧩 乐高报价分析系统")

if SessionStateManager.safe_get("clear_parse_result", False):
    SessionStateManager.safe_set("parse_result", pd.DataFrame())
    SessionStateManager.safe_set("original_parse", [])
    SessionStateManager.safe_set("clear_parse_result", False)

# --- 批量录入区域 ---
with st.expander("📝 批量录入", expanded=True):
    txt = st.text_area("粘贴内容", height=200, key="batch_input_text")
    
    col_btn1, col_btn2 = st.columns([1, 4])
    with col_btn1:
        parse_clicked = st.button("🔍 解析", type="primary", use_container_width=True,
                                 disabled=SessionStateManager.safe_get("parsing_in_progress", False))
    
    if parse_clicked:
        SessionStateManager.safe_set("trigger_parse", True)
    
    if SessionStateManager.safe_get("trigger_parse", False) and not SessionStateManager.safe_get("parsing_in_progress", False):
        SessionStateManager.safe_set("trigger_parse", False)
        SessionStateManager.safe_set("parsing_in_progress", True)
        
        try:
            if not txt or txt.strip() == "":
                st.warning("请输入内容")
                SessionStateManager.safe_set("parsing_in_progress", False)
            else:
                lines = txt.strip().splitlines()
                total_lines = len(lines)
                
                res = [None] * total_lines
                
                progress_bar = st.progress(0, text="开始解析...")
                status_text = st.empty()
                
                regex_results = []
                for idx, li in enumerate(lines):
                    m, p, r = extract_by_regex(li)
                    regex_results.append((m, p, r, li))
                    if not m or not p:
                        res[idx] = {"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 解析失败"}
                
                progress_bar.progress(0.3, text="正则解析完成，检查可疑项...")
                
                ai_indices = []
                for idx, (m, p, r, li) in enumerate(regex_results):
                    if m and p and should_use_ai_fallback(m, p, li):
                        ai_indices.append(idx)
                
                if ai_indices:
                    progress_bar.progress(0.5, text=f"发现 {len(ai_indices)} 条可疑数据，正在批量调用AI...")
                    ai_lines = [regex_results[i][3] for i in ai_indices]
                    ai_results = extract_by_llm_batch(ai_lines)
                    for i, idx in enumerate(ai_indices):
                        ai_model, ai_price, ai_remark = ai_results[i]
                        m_old, p_old, r_old, li = regex_results[idx]
                        if ai_model and ai_price:
                            res[idx] = {
                                "型号": ai_model,
                                "价格": ai_price,
                                "备注": ai_remark,
                                "原始": li,
                                "状态": "✅ 有效（AI修正）"
                            }
                        else:
                            res[idx] = {
                                "型号": m_old,
                                "价格": p_old,
                                "备注": r_old,
                                "原始": li,
                                "状态": "⚠️ 需手动核实"
                            }
                
                for idx, (m, p, r, li) in enumerate(regex_results):
                    if res[idx] is None:
                        res[idx] = {
                            "型号": m,
                            "价格": p,
                            "备注": r,
                            "原始": li,
                            "状态": "✅ 有效"
                        }
                
                progress_bar.progress(0.8, text="去重与当日检查...")
                
                valid_entries = []
                for entry in res:
                    if entry and entry["型号"] and entry["价格"] > 0:
                        valid_entries.append({
                            "model": entry["型号"],
                            "price": entry["价格"],
                            "remark": entry["备注"],
                            "raw": entry["原始"]
                        })
                
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
                        save_list.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": m,
                            "price": int(p),
                            "remark": str(r).strip()
                        })
                        today_set.add((m, p, r))
                
                progress_bar.progress(1.0, text="解析完成")
                status_text.empty()
                progress_bar.empty()
                
                res_filtered = [r for r in res if r is not None]
                
                priority_order = {
                    "⚠️ 需手动核实": 1,
                    "❌ 解析失败": 2,
                    "✅ 有效（AI修正）": 3,
                    "✅ 有效": 4,
                    "⏭️ 已跳过（当天重复）": 5
                }
                res_sorted = sorted(res_filtered, key=lambda x: priority_order.get(x.get("状态", ""), 99))
                
                SessionStateManager.safe_set("parse_result", pd.DataFrame(res_sorted))
                SessionStateManager.safe_set("original_parse", res_sorted.copy())
                
                if save_list:
                    with st.spinner(f"正在保存 {len(save_list)} 条有效数据..."):
                        saved_count = save_batch_one_by_one(save_list)
                    st.success(f"✅ 解析并自动保存 {saved_count} 条有效数据")
                else:
                    if res_filtered:
                        st.info("解析完成，没有新的有效数据需要保存")
                    else:
                        st.warning("没有解析到任何有效数据")
                
                SessionStateManager.safe_set("clear_parse_result", False)
        except Exception as e:
            logger.error(f"解析过程异常: {e}")
            st.error(f"解析出错，请重试。错误信息：{e}")
        finally:
            SessionStateManager.safe_set("parsing_in_progress", False)
            st.rerun()
    
    # --- 显示解析结果表格 ---
    parse_df = SessionStateManager.safe_get("parse_result", pd.DataFrame())
    if not parse_df.empty:
        status_counts = parse_df["状态"].value_counts().to_dict()
        all_statuses = ["全部"] + list(status_counts.keys())
        
        col_filter1, col_filter2 = st.columns([3, 1])
        with col_filter1:
            current_filter = SessionStateManager.safe_get("parse_result_status_filter", "全部")
            if current_filter not in all_statuses:
                current_filter = "全部"
            selected_status = st.selectbox(
                "📌 按状态筛选",
                options=all_statuses,
                index=all_statuses.index(current_filter),
                key="status_filter_select"
            )
            SessionStateManager.safe_set("parse_result_status_filter", selected_status)
        with col_filter2:
            st.caption("点击下方按钮快速筛选")
        
        btn_cols = st.columns(len(status_counts) + 1)
        if btn_cols[0].button("📋 全部", use_container_width=True, key="filter_all"):
            SessionStateManager.safe_set("parse_result_status_filter", "全部")
            st.rerun()
        for i, (status, count) in enumerate(status_counts.items()):
            icon = "✅" if "有效" in status else ("🤖" if "AI" in status else ("⚠️" if "需手动" in status else ("❌" if "失败" in status else "⏭️")))
            label = f"{icon} {status.replace('✅', '').replace('⚠️', '').strip()} ({count})"
            if btn_cols[i+1].button(label, use_container_width=True, key=f"filter_{status}"):
                SessionStateManager.safe_set("parse_result_status_filter", status)
                st.rerun()
        
        filter_status = SessionStateManager.safe_get("parse_result_status_filter", "全部")
        if filter_status != "全部":
            filtered_df = parse_df[parse_df["状态"] == filter_status].copy()
        else:
            filtered_df = parse_df.copy()
        
        if not filtered_df.empty:
            df_with_trend = filtered_df.copy()
            valid_rows = df_with_trend[(df_with_trend["型号"] != "") & (df_with_trend["价格"] > 0)]
            if not valid_rows.empty:
                df_clean_for_calc = get_clean_data()
                model_price_pairs = list(zip(valid_rows["型号"].tolist(), valid_rows["价格"].tolist()))
                batch_results = batch_calculate_trends_and_changes(df_clean_for_calc, model_price_pairs)
                df_with_trend["趋势"] = "—"
                df_with_trend["涨跌"] = "—"
                for idx, row in valid_rows.iterrows():
                    model = row["型号"]
                    if model in batch_results:
                        df_with_trend.at[idx, "趋势"] = batch_results[model]["trend"]
                        df_with_trend.at[idx, "涨跌"] = batch_results[model]["change"]
            else:
                df_with_trend["趋势"] = "—"
                df_with_trend["涨跌"] = "—"
            
            cols_order = ["型号", "价格", "趋势", "涨跌", "备注", "原始", "状态"]
            df_display = df_with_trend[cols_order]
            height = min(400, 35 * len(df_display) + 38)
            
            edited_df = st.data_editor(
                df_display,
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
                height=height,
                key="parse_data_editor"
            )
            
            total = len(parse_df)
            valid = status_counts.get("✅ 有效", 0) + status_counts.get("✅ 有效（AI修正）", 0)
            ai_fixed = status_counts.get("✅ 有效（AI修正）", 0)
            manual = status_counts.get("⚠️ 需手动核实", 0)
            failed = status_counts.get("❌ 解析失败", 0)
            skipped = status_counts.get("⏭️ 已跳过（当天重复）", 0)
            st.markdown(f"📊 **本轮解析**：总 {total} 条｜✅ 有效 {valid}｜🤖 AI修正 {ai_fixed}｜✏️ 需手动 {manual}｜❌ 失败 {failed}｜⏭️ 跳过 {skipped}")
            
                                   # ========== 修改：快速采纳区域独立放置（修复 expander 异常） ==========
            # 只要有需手动核实的数据，无论当前筛选条件如何，都显示快速采纳面板
            if manual > 0:
                # 使用 try-except 防止 expander 上下文异常
                try:
                    with st.expander("⚡ 快速采纳需手动核实的数据", expanded=True):
                        st.markdown("点击下方按钮可直接将对应记录保存到数据库，状态将变为“✅ 有效（手动采纳）”。")
                        
                        # 获取 parse_df 中所有需手动核实的行（不受筛选影响）
                        manual_rows_all = parse_df[parse_df["状态"] == "⚠️ 需手动核实"].copy()
                        
                        if not manual_rows_all.empty:
                            # 批量采纳按钮
                            if st.button("🚀 一键采纳全部需手动核实数据", type="secondary", use_container_width=True, key="batch_adopt_all"):
                                adopted_count = 0
                                for _, row in manual_rows_all.iterrows():
                                    if quick_adopt_record(row["型号"], row["价格"], row["备注"]):
                                        adopted_count += 1
                                if adopted_count > 0:
                                    st.success(f"✅ 成功采纳 {adopted_count} 条数据")
                                    # 更新状态
                                    updated_parse = parse_df.copy()
                                    updated_parse.loc[updated_parse["状态"] == "⚠️ 需手动核实", "状态"] = "✅ 有效（手动采纳）"
                                    SessionStateManager.safe_set("parse_result", updated_parse)
                                    smart_cache_clear()
                                    st.rerun()
                                else:
                                    st.warning("没有成功采纳任何数据")
                            
                            st.divider()
                            st.caption("或逐条采纳：")
                            
                            # 逐条采纳（使用稳定的 key）
                            for idx, row in manual_rows_all.iterrows():
                                # 用原始索引生成唯一 key
                                unique_key = f"adopt_{idx}_{hash(row['型号'] + str(row['价格']) + str(row['备注']))}"
                                col1, col2, col3, col4 = st.columns([2, 1, 2, 1])
                                with col1:
                                    st.write(f"**{row['型号']}**")
                                with col2:
                                    st.write(f"¥{row['价格']}")
                                with col3:
                                    st.write(row['备注'] if row['备注'] else "—")
                                with col4:
                                    if st.button("✅ 采纳", key=unique_key):
                                        if quick_adopt_record(row["型号"], row["价格"], row["备注"]):
                                            # 仅更新当前行的状态
                                            updated_parse = parse_df.copy()
                                            updated_parse.loc[idx, "状态"] = "✅ 有效（手动采纳）"
                                            SessionStateManager.safe_set("parse_result", updated_parse)
                                            smart_cache_clear()
                                            st.success(f"已采纳 {row['型号']}")
                                            st.rerun()
                                        else:
                                            st.error("采纳失败，请重试")
                        else:
                            st.info("当前没有需手动核实的数据")
                except Exception as e:
                    st.warning("快速采纳面板加载失败，请刷新页面或使用下方表格编辑保存。")
                    logger.error(f"快速采纳 expander 异常: {e}")
            # ========== 快速采纳区域结束 ==========
            
            if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True,
                        disabled=SessionStateManager.safe_get("saving_in_progress", False)):
                SessionStateManager.safe_set("saving_in_progress", True)
                original_dict = {i: row for i, row in enumerate(SessionStateManager.safe_get("original_parse", []))}
                save_list_manual = []
                for idx, (_, edited_row) in enumerate(edited_df.iterrows()):
                    original_row = original_dict.get(idx, {})
                    modified = (
                        edited_row["型号"] != original_row.get("型号", "") or
                        edited_row["价格"] != original_row.get("价格", 0) or
                        edited_row["备注"] != original_row.get("备注", "")
                    )
                    if not modified:
                        continue
                    model = str(edited_row["型号"]).strip()
                    price = edited_row["价格"]
                    if not (model and len(model) == 5 and model.isdigit()):
                        continue
                    try:
                        price = int(price)
                    except:
                        continue
                    if price < 10:
                        continue
                    new_item = {"model": model, "price": price, "remark": str(edited_row["备注"]).strip()}
                    save_list_manual.append(new_item)
                
                if not save_list_manual:
                    st.warning("没有检测到任何修改，无需保存")
                else:
                    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                    today_str = today.strftime("%Y-%m-%d")
                    all_records_df = get_all_price_records_df()
                    today_set = set()
                    for _, row in all_records_df.iterrows():
                        time_str = row.get("time", "")
                        if time_str and time_str[:10] == today_str:
                            today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))
                    final_save = []
                    for item in save_list_manual:
                        m, p, r = item["model"], item["price"], item["remark"]
                        if (m, p, r) not in today_set:
                            final_save.append(item)
                            today_set.add((m, p, r))
                    if final_save:
                        records_to_save = [{
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": item["model"],
                            "price": int(item["price"]),
                            "remark": item["remark"]
                        } for item in final_save]
                        saved_count = save_batch_one_by_one(records_to_save)
                        st.success(f"✅ 成功保存 {saved_count} 条修正数据")
                        SessionStateManager.safe_set("clear_parse_result", True)
                        st.rerun()
                    else:
                        st.info("所有修改后的数据当天均已存在，无需保存")
                SessionStateManager.safe_set("saving_in_progress", False)
        else:
            st.info("当前筛选条件下无数据")
    else:
        st.info("暂无解析结果，请粘贴内容后点击“解析”")

# --- 设置折叠面板 ---
with st.expander("⚙️ 设置", expanded=False):
    col1, col2 = st.columns([3,1])
    with col2:
        th = get_alert_threshold()
        new_th = st.number_input("⚠️ 提醒阈值", min_value=1, value=th)
        if new_th != th:
            set_alert_threshold(new_th)

df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

# --- 顶部导航栏 ---
tab1, tab2, tab3, tab4 = st.tabs(["⭐ 我的收藏", "📈 涨跌幅排行", "📊 价格预警", "💰 价格区间筛选"])

with tab1:
    st.markdown("#### ❤️ 点击下方按钮快速查看")
    favs = get_favorites()
    if favs:
        fav_items = []
        for m in favs:
            s = df[df["型号"]==m]
            if len(s)<2:
                fav_items.append((f"{m} (数据不足)", m))
            else:
                s = s.sort_values("时间")
                d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
                icon = "📈" if d>0 else "📉"
                fav_items.append((f"{m} {icon} {d:+}元 | 当前 {s.iloc[-1]['价格']}元", m))
        render_grid_buttons(fav_items, columns=1, prefix="fav_tab")
    else:
        st.info("暂无收藏，请在历史数据管理中添加")

with tab2:
    st.markdown("#### 📊 近7日 / 近30日波动 TOP10")
    c7, c30 = st.columns(2)
    with c7:
        st.markdown("##### 📈 近7天")
        t7 = get_trend(7)
        if t7:
            items = [(f"{item['model']} {item['diff']:+}元 | 当前 {item['last']}元", item['model']) for item in t7[:10]]
            render_grid_buttons(items, columns=1, prefix="trend7")
        else:
            st.caption("暂无数据")
    with c30:
        st.markdown("##### 📉 近30天")
        t30 = get_trend(30)
        if t30:
            items = [(f"{item['model']} {item['diff']:+}元 | 当前 {item['last']}元", item['model']) for item in t30[:10]]
            render_grid_buttons(items, columns=1, prefix="trend30")
        else:
            st.caption("暂无数据")

with tab3:
    st.markdown("#### 🔍 价格筛选")
    col_min, col_max = st.columns(2)
    with col_min:
        min_price_alert = st.number_input("最低价格", min_value=0, value=0, step=10, key="min_price_alert")
    with col_max:
        max_price_alert = st.number_input("最高价格", min_value=0, value=100, step=10, key="max_price_alert")
    st.divider()
    st.markdown("#### 🚨 点击查看详情")
    alerts = get_alerts()
    if alerts:
        filtered_alerts = []
        for a in alerts:
            cp = a["last"]
            if max_price_alert > 0:
                if min_price_alert <= cp <= max_price_alert:
                    filtered_alerts.append(a)
            else:
                if cp >= min_price_alert:
                    filtered_alerts.append(a)
        up_list = [a for a in filtered_alerts if a["trend"] == "上涨"]
        down_list = [a for a in filtered_alerts if a["trend"] == "下跌"]
        up_list.sort(key=lambda x: -x["abs_diff"])
        down_list.sort(key=lambda x: -x["abs_diff"])
        col_up, col_down = st.columns(2)
        with col_up:
            st.markdown("##### 📈 涨价排行")
            if up_list:
                items = [(f"{a['model']} | +{a['abs_diff']}元", a['model']) for a in up_list]
                render_grid_buttons(items, columns=1, prefix="alert_up")
            else:
                st.caption("无上涨预警")
        with col_down:
            st.markdown("##### 📉 跌价排行")
            if down_list:
                items = [(f"{a['model']} | -{a['abs_diff']}元", a['model']) for a in down_list]
                render_grid_buttons(items, columns=1, prefix="alert_down")
            else:
                st.caption("无下跌预警")
    else:
        st.info("暂无价格预警")

with tab4:
    st.markdown("#### 🔍 价格区间筛选")
    col_min, col_max = st.columns(2)
    with col_min:
        min_price = st.number_input("最低价格", min_value=0, value=0, step=10)
    with col_max:
        max_price = st.number_input("最高价格", min_value=0, value=100, step=10)
    if min_price >= max_price and max_price > 0:
        st.warning("最高价格应大于最低价格")
    else:
        df_clean = get_clean_data()
        if not df_clean.empty:
            latest_df = df_clean.sort_values('时间').groupby('型号').tail(1)
            if max_price > 0:
                filtered_df = latest_df[(latest_df['价格'] >= min_price) & (latest_df['价格'] <= max_price)]
            else:
                filtered_df = latest_df[latest_df['价格'] >= min_price]
            filtered_df = filtered_df.sort_values('价格', ascending=False)
            if not filtered_df.empty:
                items = [(f"{row['型号']} | {row['价格']}元", row['型号']) for _, row in filtered_df.iterrows()]
                total_items = len(items)
                page_size = st.selectbox("每页显示", options=[10, 20, 50], index=1, key="page_size_tab4")
                total_pages = max(1, (total_items + page_size - 1) // page_size)
                current_page = SessionStateManager.safe_get("current_page_tab4", 1)
                current_page = st.slider("页码", 1, total_pages, current_page, key="page_slider_tab4")
                SessionStateManager.safe_set("current_page_tab4", current_page)
                paginated_items = paginate(items, page_size, current_page)
                render_grid_buttons(paginated_items, columns=2, prefix="filter_tab4")
                st.caption(f"共 {total_items} 条数据，当前显示第 {current_page} 页，共 {total_pages} 页")
            else:
                st.info("未找到符合条件的数据")
        else:
            st.info("暂无数据")

# --- 历史数据管理 ---
st.divider()
st.subheader("📋 历史数据管理")
if not df.empty:
    idx = 0
    selected = SessionStateManager.safe_get("selected_model", "")
    if selected in all_models:
        idx = all_models.index(selected) + 1
    target = st.selectbox("选择型号", [""] + all_models, index=idx)
    if target:
        SessionStateManager.safe_set("selected_model", target)
        isfav = target in get_favorites()
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
            
            def format_date(t_str):
                return t_str[:10] if t_str and len(t_str) >= 10 else t_str
            
            show = model_data[["id", "原始时间", "型号", "价格", "remark"]].copy()
            show["日期"] = show["原始时间"].apply(format_date)
            show.rename(columns={"remark":"备注"}, inplace=True)
            show.insert(0, "删除", False)
            
            ed_table = st.data_editor(
                show,
                column_config={
                    "删除": st.column_config.CheckboxColumn("删除"),
                    "型号": st.column_config.TextColumn("型号"),
                    "价格": st.column_config.NumberColumn("价格"),
                    "备注": st.column_config.TextColumn("备注"),
                    "日期": st.column_config.TextColumn("日期", disabled=True),
                    "id": st.column_config.NumberColumn("id", disabled=True),
                    "原始时间": st.column_config.TextColumn("原始时间", disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="fixed"
            )
            
            if st.button("保存修改 & 删除"):
                del_ids = ed_table[ed_table["删除"]==True]["id"].tolist()
                for did in del_ids:
                    delete_record(did)
                for _, row in ed_table[~ed_table["删除"]].iterrows():
                    update_record(row["id"], {
                        "model": str(row["型号"]).strip(),
                        "price": int(row["价格"]),
                        "remark": str(row["备注"]).strip()
                    })
                st.success("完成")
                get_clean_data.clear()
                fetch_all_records_cached.clear()
                st.rerun()
            
            st.subheader("价格走势")
            fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
            st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无数据")

if SessionStateManager.safe_get("scroll_to_bottom", False):
    st.components.v1.html("""
    <script>
        window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
    </script>
    """, height=0)
    SessionStateManager.safe_set("scroll_to_bottom", False)

smart_cache_clear()
