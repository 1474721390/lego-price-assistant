# ===========================================
# 🔒 安全启动配置（必须放在最顶部！）
# ===========================================
import os
os.environ["STREAMLIT_SERVER_RUNONSAVE"] = "false"
os.environ["STREAMLIT_SERVER_FOLDERWATCHBLACKLIST"] = ".*"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
os.environ["STREAMLIT_SERVER_ENABLE_WEBSOCKET_COMPRESSION"] = "true"

# ===========================================
# 标准导入（保持原有依赖）
# ===========================================
import re
import json
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import plotly.express as px
from supabase import create_client
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== 会话状态管理器 ====================
class SessionStateManager:
    _initialized = False

    @classmethod
    def ensure_initialized(cls):
        if cls._initialized:
            return True
        try:
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
            }
            for key, value in defaults.items():
                if key not in st.session_state:
                    st.session_state[key] = value
            cls._initialized = True
            return True
        except:
            return False

    @classmethod
    def safe_get(cls, key, default=None):
        if not cls.ensure_initialized():
            return default
        return st.session_state.get(key, default)

    @classmethod
    def safe_set(cls, key, value):
        if cls.ensure_initialized():
            st.session_state[key] = value

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 请配置环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价系统", layout="wide")

# 初始化会话状态
SessionStateManager.ensure_initialized()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 数据缓存 ====================
@st.cache_data(ttl=120, show_spinner=False)
def fetch_all_records(table_name):
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
    all_data = fetch_all_records("price_records")
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

@st.cache_data(ttl=60)
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
    all_data = fetch_all_records("price_records")
    return pd.DataFrame(all_data) if all_data else pd.DataFrame()

# ==================== 业务逻辑 ====================
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

def get_price_rules():
    res = supabase.table("price_rules").select("model, buy, sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    temp = SessionStateManager.safe_get("temp_price_rules", {})
    rules.update(temp)
    return rules

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()
    temp = SessionStateManager.safe_get("temp_price_rules", {})
    temp[model] = {"buy": buy, "sell": sell}
    SessionStateManager.safe_set("temp_price_rules", temp)

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

def extract_by_llm_full(line):
    prompt = f"""你是乐高价格信息提取专家。
请从以下用户输入中提取：乐高型号（5位数字）、价格（数字）、备注（如盒况/袋况）。
输入文本：{line}
只返回一个 JSON 对象，格式：{{"model": "字符串", "price": 数字, "remark": "字符串"}}
如果无法提取，返回：{{"model": null, "price": null, "remark": ""}}"""
    try:
        response = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers={"Authorization": f"Bearer {ZHIPU_API_KEY}", "Content-Type": "application/json"},
            json={"model": "glm-4-flash", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1},
            timeout=15
        )
        if response.status_code == 200:
            j = response.json()
            content = j["choices"][0]["message"]["content"].strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                res = json.loads(json_match.group())
                model = res.get("model")
                price = res.get("price")
                remark = res.get("remark", "")
                if isinstance(model, str) and len(model) == 5 and model.isdigit() and model[0] != '0':
                    try:
                        price = float(price)
                        if 10 <= price <= 8000:
                            return model, int(price), str(remark)
                    except:
                        pass
        return None, None, ""
    except:
        return None, None, ""

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
    except:
        return None

def delete_record(id):
    try:
        return supabase.table("price_records").delete().eq("id", id).execute()
    except:
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
                if col.button(label, key=f"{prefix}_{model}_{idx}", use_container_width=True):
                    SessionStateManager.safe_set("selected_model", model)
                    SessionStateManager.safe_set("scroll_to_bottom", True)

def paginate(items, page_size, current_page):
    start = (current_page - 1) * page_size
    return items[start:start+page_size]

def smart_cache_clear():
    if SessionStateManager.safe_get("pending_cache_clear", False):
        try:
            get_clean_data.clear()
            st.cache_data.clear()
            SessionStateManager.safe_set("pending_cache_clear", False)
        except:
            pass

# ==================== 主界面 ====================
st.title("🧩 乐高报价分析系统")

# 清除标记处理
if SessionStateManager.safe_get("clear_parse_result", False):
    SessionStateManager.safe_set("parse_result", pd.DataFrame())
    SessionStateManager.safe_set("original_parse", [])
    SessionStateManager.safe_set("clear_parse_result", False)

# --- 批量录入 ---
with st.expander("📝 批量录入", expanded=True):
    txt = st.text_area("粘贴内容", height=200, key="batch_input_text")
    parse_clicked = st.button("🔍 解析", type="primary", use_container_width=True,
                              disabled=SessionStateManager.safe_get("parsing_in_progress", False))

    if parse_clicked and not SessionStateManager.safe_get("parsing_in_progress", False):
        SessionStateManager.safe_set("parsing_in_progress", True)

        if not txt:
            st.warning("请输入内容")
            SessionStateManager.safe_set("parsing_in_progress", False)
        else:
            lines = txt.strip().splitlines()
            res = []
            temp_items = []

            progress_bar = st.progress(0, text="开始解析...")
            status_text = st.empty()

            # 第一遍正则
            for idx, li in enumerate(lines):
                progress_bar.progress((idx+1)/len(lines), text=f"正在解析第 {idx+1}/{len(lines)} 行...")
                m, p, r = extract_by_regex(li)
                if not m or not p:
                    res.append({"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 解析失败"})
                else:
                    temp_items.append({"model": m, "price": p, "remark": r.strip(), "raw": li})

            # AI 修正
            for i, item in enumerate(temp_items):
                m, p, r, raw = item["model"], item["price"], item["remark"], item["raw"]
                if should_use_ai_fallback(m, p, raw):
                    ai_m, ai_p, ai_r = extract_by_llm_full(raw)
                    if ai_m and ai_p:
                        temp_items[i] = {"model": ai_m, "price": ai_p, "remark": ai_r, "raw": raw}
                        res.append({"型号": ai_m, "价格": ai_p, "备注": ai_r, "原始": raw, "状态": "✅ 有效（AI修正）"})
                    else:
                        res.append({"型号": m, "价格": p, "备注": r, "原始": raw, "状态": "⚠️ 需手动核实"})
                else:
                    res.append({"型号": m, "价格": p, "备注": r, "原始": raw, "状态": "✅ 有效"})

            progress_bar.progress(1.0, text="去重与当日检查...")
            status_text.text("去重中...")

            # 去重
            unique_batch = {}
            for item in temp_items:
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
                        if entry.get("型号") == m and entry.get("价格") == p:
                            res[idx]["状态"] = "⏭️ 已跳过（当天重复）"
                            break
                    continue
                status = next((e["状态"] for e in res if e.get("型号") == m and e.get("价格") == p), "")
                if "✅ 有效" in status:
                    save_list.append({
                        "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                        "model": m,
                        "price": int(p),
                        "remark": str(r).strip()
                    })
                    today_set.add((m, p, r))

            status_text.empty()
            progress_bar.empty()

            # 排序
            priority = {"⚠️ 需手动核实": 1, "❌ 解析失败": 2, "✅ 有效（AI修正）": 3, "✅ 有效": 4, "⏭️ 已跳过（当天重复）": 5}
            res_sorted = sorted(res, key=lambda x: priority.get(x.get("状态", ""), 99))

            SessionStateManager.safe_set("parse_result", pd.DataFrame(res_sorted))
            SessionStateManager.safe_set("original_parse", res_sorted.copy())

            if save_list:
                with st.spinner(f"正在保存 {len(save_list)} 条有效数据..."):
                    saved = save_batch_one_by_one(save_list)
                st.success(f"✅ 解析并自动保存 {saved} 条有效数据")
                SessionStateManager.safe_set("clear_parse_result", True)
                st.rerun()
            else:
                if res_sorted:
                    st.info("解析完成，没有新的有效数据需要保存")
                else:
                    st.warning("没有解析到任何有效数据")

        SessionStateManager.safe_set("parsing_in_progress", False)

    # 显示表格
    parse_df = SessionStateManager.safe_get("parse_result", pd.DataFrame())
    if not parse_df.empty:
        status_counts = parse_df["状态"].value_counts().to_dict()
        all_statuses = ["全部"] + list(status_counts.keys())

        filter_status = st.selectbox("📌 按状态筛选", options=all_statuses, key="status_filter")
        if filter_status != "全部":
            display_df = parse_df[parse_df["状态"] == filter_status].copy()
        else:
            display_df = parse_df.copy()

        if not display_df.empty:
            edited_df = st.data_editor(
                display_df[["型号", "价格", "备注", "原始", "状态"]],
                column_config={
                    "型号": st.column_config.TextColumn("型号", required=True),
                    "价格": st.column_config.NumberColumn("价格", required=True, min_value=0),
                    "备注": st.column_config.TextColumn("备注"),
                    "原始": st.column_config.TextColumn("原始", disabled=True),
                    "状态": st.column_config.TextColumn("状态", disabled=True),
                },
                use_container_width=True,
                hide_index=True,
                num_rows="fixed"
            )

            total = len(parse_df)
            valid = status_counts.get("✅ 有效", 0) + status_counts.get("✅ 有效（AI修正）", 0)
            ai_fixed = status_counts.get("✅ 有效（AI修正）", 0)
            manual = status_counts.get("⚠️ 需手动核实", 0)
            failed = status_counts.get("❌ 解析失败", 0)
            skipped = status_counts.get("⏭️ 已跳过（当天重复）", 0)
            st.markdown(f"📊 **本轮解析**：总 {total} 条｜✅ 有效 {valid}｜🤖 AI修正 {ai_fixed}｜✏️ 需手动 {manual}｜❌ 失败 {failed}｜⏭️ 跳过 {skipped}")

            if st.button("💾 修改并保存有效数据", type="primary", disabled=SessionStateManager.safe_get("saving_in_progress", False)):
                SessionStateManager.safe_set("saving_in_progress", True)
                original_dict = {i: row for i, row in enumerate(SessionStateManager.safe_get("original_parse", []))}
                save_manual = []
                for idx, (_, row) in enumerate(edited_df.iterrows()):
                    # 简单处理，直接保存编辑后的数据
                    if row["型号"] and row["价格"] > 0:
                        save_manual.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": str(row["型号"]).strip(),
                            "price": int(row["价格"]),
                            "remark": str(row["备注"]).strip()
                        })
                if save_manual:
                    saved = save_batch_one_by_one(save_manual)
                    st.success(f"✅ 成功保存 {saved} 条修正数据")
                    SessionStateManager.safe_set("clear_parse_result", True)
                    st.rerun()
                else:
                    st.warning("没有检测到有效修改")
                SessionStateManager.safe_set("saving_in_progress", False)
        else:
            st.info("当前筛选条件下无数据")
    else:
        st.info("暂无解析结果，请粘贴内容后点击“解析”")

# --- 设置 ---
with st.expander("⚙️ 设置", expanded=False):
    th = get_alert_threshold()
    new_th = st.number_input("⚠️ 提醒阈值", min_value=1, value=th)
    if new_th != th:
        set_alert_threshold(new_th)

df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

# --- 导航栏 ---
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
        render_grid_buttons(fav_items, columns=1, prefix="fav")
    else:
        st.info("暂无收藏")

with tab2:
    st.markdown("#### 📊 近7日 / 近30日波动 TOP10")
    c7, c30 = st.columns(2)
    with c7:
        st.markdown("##### 📈 近7天")
        t7 = []
        for m in all_models:
            s = df[df["型号"]==m].sort_values("时间")
            if len(s)>=2:
                diff = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
                t7.append({"model":m, "diff":diff, "abs":abs(diff), "last":s.iloc[-1]["价格"]})
        t7.sort(key=lambda x: -x["abs"])
        for item in t7[:10]:
            if st.button(f"{item['model']} {item['diff']:+}元 | 当前 {item['last']}元", key=f"t7_{item['model']}"):
                SessionStateManager.safe_set("selected_model", item["model"])
                SessionStateManager.safe_set("scroll_to_bottom", True)
    with c30:
        st.markdown("##### 📉 近30天")
        st.caption("功能同近7天，可根据需要扩展")

with tab3:
    st.markdown("#### 🚨 价格预警")
    alerts = []
    threshold = get_alert_threshold()
    for m in all_models:
        s = df[df["型号"]==m].sort_values("时间")
        if len(s)>=2:
            diff = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
            if abs(diff) >= threshold:
                alerts.append({"model":m, "diff":diff, "last":s.iloc[-1]["价格"]})
    if alerts:
        for a in alerts:
            st.write(f"{a['model']} 波动 {a['diff']:+}元，当前 {a['last']}元")
    else:
        st.info("暂无预警")

with tab4:
    st.markdown("#### 🔍 价格区间筛选")
    min_p = st.number_input("最低价格", 0, 100, 0)
    max_p = st.number_input("最高价格", 0, 100, 100)
    latest = df.sort_values("时间").groupby("型号").tail(1)
    filtered = latest[(latest["价格"] >= min_p) & (latest["价格"] <= max_p)]
    st.dataframe(filtered[["型号", "价格", "备注"]])

# --- 历史数据管理 ---
st.divider()
st.subheader("📋 历史数据管理")
if not df.empty:
    selected = st.selectbox("选择型号", [""] + all_models)
    if selected:
        if st.button("⭐ 收藏" if selected not in get_favorites() else "⭐ 取消收藏"):
            toggle_favorite(selected)
            st.rerun()
        rules = get_price_rules()
        r = rules.get(selected, {"buy":0, "sell":0})
        b = st.number_input("💚 可收价格", value=r["buy"])
        s = st.number_input("❤️ 可出价格", value=r["sell"])
        if st.button("💾 保存心理价位"):
            save_price_rule(selected, b, s)
            st.success("已保存")
        model_data = df[df["型号"]==selected].sort_values("时间", ascending=False)
        st.dataframe(model_data[["时间", "价格", "remark"]])
        fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
        st.plotly_chart(fig, use_container_width=True)

smart_cache_clear()
