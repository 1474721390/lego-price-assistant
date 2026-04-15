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

# ==================== 环境变量检查 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 缺少必要环境变量：SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY")
    st.stop()

st.set_page_config(
    page_title="乐高报价系统",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==================== Supabase 客户端 ====================
@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ==================== 数据层（原生 @st.cache_data，绝不触碰 session_state） ====================
@st.cache_data(ttl=120, show_spinner=False)
def _fetch_all_records(table_name):
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
    all_data = _fetch_all_records("price_records")
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
    all_data = _fetch_all_records("price_records")
    return pd.DataFrame(all_data) if all_data else pd.DataFrame()

@st.cache_data(ttl=60, show_spinner=False)
def get_price_rules():
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
    # 清除相关缓存
    st.cache_data.clear()
    get_supabase.clear()

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()
    get_price_rules.clear()
    st.rerun()  # 仅此一处，用于更新界面显示的心理价位

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
            logger.error(f"AI调用异常: {e}")
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
    if df.empty: return []
    trends = []
    for m in df["型号"].unique():
        s = df[df["型号"] == m].sort_values("时间")
        if len(s) < 2: continue
        old = s.iloc[0]["价格"]
        new = s.iloc[-1]["价格"]
        diff = new - old
        trends.append({"model": m, "diff": diff, "abs_diff": abs(diff), "last": new})
    return sorted(trends, key=lambda x: -x["abs_diff"])

def save_batch_one_by_one(records):
    success = 0
    for rec in records:
        try:
            supabase.table("price_records").insert(rec).execute()
            success += 1
        except Exception as e:
            logger.error(f"保存失败: {e}")
    if success > 0:
        # 清除相关缓存
        get_clean_data.clear()
        _fetch_all_records.clear()
    return success

def update_record(id, data):
    try:
        supabase.table("price_records").update(data).eq("id", id).execute()
        return True
    except:
        return False

def delete_record(id):
    try:
        supabase.table("price_records").delete().eq("id", id).execute()
        return True
    except:
        return False

def render_grid_buttons(items, columns=3, prefix=""):
    if not items: return
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
    start = (current_page - 1) * page_size
    return items[start:start+page_size]

# ==================== 会话状态初始化 ====================
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
if "parse_result_status_filter" not in st.session_state:
    st.session_state.parse_result_status_filter = "全部"

# ==================== UI ====================
st.title("🧩 乐高报价分析系统")

# --- 批量录入（无 rerun 循环）---
with st.expander("📝 批量录入", expanded=True):
    txt = st.text_area("粘贴内容", height=200, placeholder="3420收顺丰10307铁塔 湖北\n默认好盒，微压滴滴，加钱私聊")
    col1, col2, _ = st.columns([1, 1, 4])
    with col1:
        parse_clicked = st.button("🔍 解析", type="primary", disabled=st.session_state.parsing_in_progress)
    with col2:
        if st.button("🧹 清空结果"):
            st.session_state.parse_result = pd.DataFrame()
            st.session_state.original_parse = []

    if parse_clicked:
        st.session_state.parsing_in_progress = True
        try:
            if not txt.strip():
                st.warning("请输入内容")
            else:
                lines = txt.strip().splitlines()
                res = [None] * len(lines)
                progress = st.progress(0, "解析中...")
                status = st.empty()

                regex_results = []
                for idx, li in enumerate(lines):
                    m, p, r = extract_by_regex(li)
                    regex_results.append((m, p, r, li))
                    if not m or not p:
                        res[idx] = {"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 解析失败"}

                progress.progress(0.3, "检查可疑项...")
                ai_indices = [idx for idx, (m, p, r, li) in enumerate(regex_results) if m and p and should_use_ai_fallback(m, p, li)]

                if ai_indices:
                    progress.progress(0.5, f"AI处理 {len(ai_indices)} 条...")
                    ai_lines = [regex_results[i][3] for i in ai_indices]
                    ai_results = extract_by_llm_batch(ai_lines)
                    for i, idx in enumerate(ai_indices):
                        ai_m, ai_p, ai_r = ai_results[i]
                        m_old, p_old, r_old, li = regex_results[idx]
                        if ai_m and ai_p:
                            res[idx] = {"型号": ai_m, "价格": ai_p, "备注": ai_r, "原始": li, "状态": "✅ 有效（AI修正）"}
                        else:
                            res[idx] = {"型号": m_old, "价格": p_old, "备注": r_old, "原始": li, "状态": "⚠️ 需手动核实"}

                for idx, (m, p, r, li) in enumerate(regex_results):
                    if res[idx] is None:
                        res[idx] = {"型号": m, "价格": p, "备注": r, "原始": li, "状态": "✅ 有效"}

                progress.progress(0.8, "去重...")
                valid_entries = [e for e in res if e and e["型号"] and e["价格"] > 0]
                unique = {}
                for e in valid_entries:
                    key = f"{e['型号']}_{e['价格']}_{e['备注']}"
                    if key not in unique:
                        unique[key] = e

                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                all_df = get_all_price_records_df()
                today_set = set()
                for _, row in all_df.iterrows():
                    if row.get("time", "")[:10] == str(today):
                        today_set.add((row["model"], row["price"], str(row.get("remark","")).strip()))

                save_list = []
                for key, item in unique.items():
                    m, p, r = item["model"], item["price"], item["remark"]
                    if (m, p, r) in today_set:
                        for idx, e in enumerate(res):
                            if e and e.get("型号") == m and e.get("价格") == p:
                                res[idx]["状态"] = "⏭️ 已跳过（当天重复）"
                                break
                        continue
                    status = next((e["状态"] for e in res if e and e.get("型号")==m and e.get("价格")==p), "")
                    if "✅ 有效" in status:
                        save_list.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                            "model": m, "price": int(p), "remark": str(r).strip()
                        })
                        today_set.add((m, p, r))

                progress.progress(1.0, "完成")
                status.empty()
                progress.empty()

                res_filtered = [r for r in res if r is not None]
                priority = {"⚠️ 需手动核实":1, "❌ 解析失败":2, "✅ 有效（AI修正）":3, "✅ 有效":4, "⏭️ 已跳过":5}
                res_sorted = sorted(res_filtered, key=lambda x: priority.get(x.get("状态",""), 99))

                st.session_state.parse_result = pd.DataFrame(res_sorted)
                st.session_state.original_parse = res_sorted.copy()

                if save_list:
                    saved = save_batch_one_by_one(save_list)
                    st.success(f"✅ 保存 {saved} 条有效数据")
                else:
                    if res_filtered:
                        st.info("没有新数据需要保存")
                    else:
                        st.warning("没有解析到有效数据")
        except Exception as e:
            st.error(f"解析出错: {e}")
        finally:
            st.session_state.parsing_in_progress = False

# --- 解析结果表格 ---
parse_df = st.session_state.parse_result
if not parse_df.empty:
    st.subheader("📋 解析结果")
    status_counts = parse_df["状态"].value_counts().to_dict()
    options = ["全部"] + list(status_counts.keys())
    selected = st.selectbox("筛选", options, key="filter_select")
    filtered = parse_df if selected == "全部" else parse_df[parse_df["状态"] == selected]

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

        edited_df = st.data_editor(
            df_trend[["型号","价格","趋势","涨跌","备注","原始","状态"]],
            column_config={"型号": st.column_config.TextColumn(required=True), "价格": st.column_config.NumberColumn(required=True)},
            use_container_width=True, hide_index=True, num_rows="fixed"
        )

        total = len(parse_df)
        valid = status_counts.get("✅ 有效",0) + status_counts.get("✅ 有效（AI修正）",0)
        st.markdown(f"总 {total} 条 | ✅有效 {valid} | 🤖AI {status_counts.get('✅ 有效（AI修正）',0)} | ✏️需手动 {status_counts.get('⚠️ 需手动核实',0)} | ❌失败 {status_counts.get('❌ 解析失败',0)} | ⏭️跳过 {status_counts.get('⏭️ 已跳过（当天重复）',0)}")

        if st.button("💾 保存修改", type="primary"):
            original = {i: row for i, row in enumerate(st.session_state.original_parse)}
            to_save = []
            for idx, (_, row) in enumerate(edited_df.iterrows()):
                orig = original.get(idx, {})
                if (row["型号"] != orig.get("型号","") or row["价格"] != orig.get("价格",0) or row["备注"] != orig.get("备注","")):
                    if row["型号"] and len(str(row["型号"]))==5 and row["价格"]>=10:
                        to_save.append({"model": row["型号"], "price": int(row["价格"]), "remark": row["备注"]})
            if to_save:
                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                all_rec = get_all_price_records_df()
                today_set = set()
                for _, r in all_rec.iterrows():
                    if r.get("time","")[:10] == str(today):
                        today_set.add((r["model"], r["price"], str(r.get("remark","")).strip()))
                final = [i for i in to_save if (i["model"], i["price"], i["remark"]) not in today_set]
                if final:
                    recs = [{"time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                             "model": i["model"], "price": i["price"], "remark": i["remark"]} for i in final]
                    saved = save_batch_one_by_one(recs)
                    st.success(f"保存 {saved} 条")
                    st.session_state.parse_result = pd.DataFrame()
                else:
                    st.info("数据已存在")
            else:
                st.warning("无修改")
    else:
        st.info("无数据")

# --- 设置 ---
with st.expander("⚙️ 系统设置", expanded=False):
    th = get_alert_threshold()
    new_th = st.number_input("预警阈值", value=th, min_value=1)
    if new_th != th:
        set_alert_threshold(new_th)

# --- 功能选项卡 ---
df_all = get_clean_data()
models_all = sorted(df_all["型号"].unique()) if not df_all.empty else []
tab1, tab2, tab3, tab4 = st.tabs(["⭐ 收藏", "📈 排行", "📊 预警", "🔎 筛选"])

with tab1:
    favs = get_favorites()
    if favs:
        items = []
        for m in favs:
            sub = df_all[df_all["型号"]==m]
            if len(sub)>=2:
                sub = sub.sort_values("时间")
                d = sub.iloc[-1]["价格"] - sub.iloc[0]["价格"]
                icon = "📈" if d>0 else "📉"
                items.append((f"{m} {icon} {d:+}元 | 现 ¥{sub.iloc[-1]['价格']}", m))
            else:
                items.append((f"{m} (数据不足)", m))
        render_grid_buttons(items, columns=1, prefix="fav")
    else:
        st.info("暂无收藏")

with tab2:
    c1, c2 = st.columns(2)
    with c1:
        t7 = get_trend(7)
        if t7:
            render_grid_buttons([(f"{i['model']} {i['diff']:+} | ¥{i['last']}", i['model']) for i in t7[:10]], 1, "t7")
    with c2:
        t30 = get_trend(30)
        if t30:
            render_grid_buttons([(f"{i['model']} {i['diff']:+} | ¥{i['last']}", i['model']) for i in t30[:10]], 1, "t30")

with tab3:
    min_p = st.number_input("最低价", 0, 100000, 0, key="alert_min")
    max_p = st.number_input("最高价", 0, 100000, 10000, key="alert_max")
    alerts = get_alerts()
    if alerts:
        filtered = [a for a in alerts if min_p <= a["last"] <= (max_p if max_p>0 else 999999)]
        up = sorted([a for a in filtered if a["trend"]=="上涨"], key=lambda x: -x["abs_diff"])
        down = sorted([a for a in filtered if a["trend"]=="下跌"], key=lambda x: -x["abs_diff"])
        cu, cd = st.columns(2)
        with cu:
            st.markdown("**📈 涨价**")
            if up: render_grid_buttons([(f"{a['model']} +{a['abs_diff']}", a['model']) for a in up], 1, "up")
        with cd:
            st.markdown("**📉 跌价**")
            if down: render_grid_buttons([(f"{a['model']} -{a['abs_diff']}", a['model']) for a in down], 1, "down")

with tab4:
    min_p2 = st.number_input("最低", 0, value=0, key="filter_min")
    max_p2 = st.number_input("最高", 0, value=10000, key="filter_max")
    if min_p2 < max_p2 or max_p2 == 0:
        latest = df_all.sort_values('时间').groupby('型号').tail(1)
        if max_p2 > 0:
            latest = latest[(latest['价格'] >= min_p2) & (latest['价格'] <= max_p2)]
        else:
            latest = latest[latest['价格'] >= min_p2]
        if not latest.empty:
            items = [(f"{r['型号']} | ¥{r['价格']}", r['型号']) for _, r in latest.iterrows()]
            page = st.selectbox("每页", [10,20,50], key="page_size_tab4")
            total_pages = max(1, (len(items)+page-1)//page)
            cur = st.slider("页码", 1, total_pages, 1)
            render_grid_buttons(paginate(items, page, cur), 2, "filter")

# --- 历史管理 ---
st.divider()
st.subheader("📋 型号详情")
if not df_all.empty:
    idx = 0
    if st.session_state.selected_model in models_all:
        idx = models_all.index(st.session_state.selected_model) + 1
    target = st.selectbox("选择型号", [""] + models_all, index=idx)
    if target:
        st.session_state.selected_model = target
        isfav = target in get_favorites()
        if st.button("⭐ 取消收藏" if isfav else "☆ 收藏"):
            toggle_favorite(target)
            st.rerun()  # 仅此一处刷新，用于更新收藏状态

        rules = get_price_rules()
        rule = rules.get(target, {"buy":0, "sell":0})
        c1, c2 = st.columns(2)
        with c1: b = st.number_input("💚 收货价", value=rule["buy"])
        with c2: s = st.number_input("❤️ 出货价", value=rule["sell"])
        if st.button("💾 保存心理价位"):
            save_price_rule(target, b, s)

        model_df = df_all[df_all["型号"]==target].sort_values("时间", ascending=False)
        if not model_df.empty:
            cur = model_df.iloc[0]["价格"]
            if s>0 and cur>=s: st.info(f"当前 ¥{cur} → 可出货")
            elif b>0 and cur<=b: st.info(f"当前 ¥{cur} → 可收货")

            show = model_df[["id","原始时间","型号","价格","remark"]].copy()
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
                _fetch_all_records.clear()
                st.success("完成")
                st.rerun()

            fig = px.line(model_df.sort_values("时间"), x="时间", y="价格", markers=True)
            st.plotly_chart(fig, use_container_width=True)

if st.session_state.scroll_to_bottom:
    st.components.v1.html("<script>window.scrollTo({top:document.body.scrollHeight,behavior:'smooth'});</script>", height=0)
    st.session_state.scroll_to_bottom = False
