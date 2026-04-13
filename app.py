import os
import re
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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

st.set_page_config(page_title="乐高报价系统", layout="centered")

if "initialized" not in st.session_state:
    st.session_state.clear()
    st.session_state["initialized"] = True

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ==================== 收藏功能 ====================
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

# ==================== 心理价位 ====================
def get_price_rules():
    res = supabase.table("price_rules").select("model, buy, sell").execute()
    rules = {}
    for r in res.data:
        rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
    return rules

def save_price_rule(model, buy, sell):
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()

# ==================== 阈值设置 ====================
def get_alert_threshold():
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

def set_alert_threshold(v):
    supabase.table("settings").upsert(
        {"id": 1, "alert_threshold": v}, on_conflict="id"
    ).execute()

# ==================== 数据读取 ====================
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        res = supabase.table(table_name).select("*").range(start, start+page_size-1).execute()
        if not res.data: break
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
    # 兼容各种时间格式
    def parse_time(t):
        try:
            return pd.to_datetime(t, errors='coerce')
        except:
            return None
    df["时间"] = df["time"].apply(parse_time)
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"]>0) & (df["价格"]<100000)]
    return df

@st.cache_data(ttl=120, show_spinner=False)
def get_all_price_records():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()
    df = pd.DataFrame(all_data)
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

def get_price_sorted_models(order='desc'):
    """
    获取每个型号的最新价格，并按价格排序
    order: 'desc' 降序（从高到低），'asc' 升序（从低到高）
    返回列表，每个元素为 (型号, 最新价格)
    """
    df = get_clean_data()
    if df.empty:
        return []
    # 获取每个型号的最新价格（按时间排序后取最后一条）
    latest_price = df.sort_values('时间').groupby('型号').last()['价格']
    sorted_models = latest_price.sort_values(ascending=(order=='asc')).items()
    return list(sorted_models)

# ==================== 工具函数 ====================
def is_price_abnormal(price):
    return price < 10 or price > 8000

def extract_remark(line):
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "烂盒", "破盒", "全新", "微压"]
    bag_keywords = ["纸袋", "M袋", "S袋", "礼袋", "礼品袋", "M号袋", "S袋", "XL袋", "L袋", "大袋", "小袋", "有袋", "无袋", "袋子"]
    
    box = None
    for b in box_keywords:
        if b in line:
            box = b
            break

    bag = None
    for bg in bag_keywords:
        if bg in line:
            bag = bg
            break

    if box and bag:
        return f"{box}+{bag}"
    elif box:
        return box
    elif bag:
        return bag
    else:
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

    if valid_prices:
        price = max(valid_prices)
    elif price_candidates:
        price = max(price_candidates)
    else:
        return None, None, None

    return model, price, remark

def llm_verify(model, price, remark, line):
    latest = get_latest_history()
    need_ai = False

    price_ok = 10 <= price <= 8000
    model_ok = model and len(model) == 5 and model.isdigit()
    price_change_ok = True
    if model in latest:
        last_price = latest[model]["price"]
        if abs(price - last_price) > 200:
            price_change_ok = False
            need_ai = True

    if price_ok and model_ok and price_change_ok:
        return True, "正常，自动通过"

    if not price_ok or not model_ok:
        need_ai = True

    if not need_ai:
        return True, "自动通过"

    prompt = f"""你是乐高价格识别校验器。
文本：{line}
已提取结果：型号={model}，价格={price}，备注={remark}
请判断这个提取结果是否合理。注意：
- 乐高型号是5位数字，首位非0（如 76914、43020）。
- 价格可以是2~5位数字（例如 61、160、1250、20000），只要在10~8000元之间且符合文本上下文。
- 价格不需要紧跟在“收”字后面，也不一定必须是3-4位。只要文本中有一个合理的数字即可。
- 如果价格与文本明显不符（例如文本中另一个数字更可能是价格），请指出；否则认为提取正确。
只返回一个 JSON 对象，格式：{{"is_valid": true/false, "reason": "说明"}}"""

    max_retries = 2
    for attempt in range(max_retries):
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
                    return res["is_valid"], res["reason"]
                else:
                    return False, f"AI返回非JSON格式: {content[:100]}"
            elif response.status_code == 401:
                return False, "API密钥无效或已过期"
            elif response.status_code == 402:
                return False, "API额度已用尽"
            elif response.status_code >= 500:
                return False, f"智谱AI服务器错误（{response.status_code}）"
            else:
                return False, f"API请求失败，状态码 {response.status_code}"
        except requests.exceptions.Timeout:
            if attempt == max_retries - 1:
                return False, "请求超时（网络问题）"
            else:
                continue
        except requests.exceptions.ConnectionError:
            if attempt == max_retries - 1:
                return False, "网络连接失败（无法访问智谱API）"
            else:
                continue
        except json.JSONDecodeError as e:
            return False, f"API返回内容解析失败: {e}"
        except Exception as e:
            if attempt == max_retries - 1:
                return False, f"未知异常: {type(e).__name__} - {e}"
            else:
                continue

    return False, "AI调用异常（多次重试失败）"

# ==================== 预警 ====================
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

# ==================== 增删改 ====================
def save_batch(records):
    try:
        return supabase.table("price_records").insert(records).execute()
    except:
        return None

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

auto_scroll = """
<script>
    window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
</script>
"""

# ==================== 界面 ====================
st.title("🧩 乐高报价分析系统")

df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

if 'selected_model' not in st.session_state:
    st.session_state.selected_model = ""
if 'scroll_to_bottom' not in st.session_state:
    st.session_state.scroll_to_bottom = False

col1, col2 = st.columns([3,1])
with col1:
    search = st.text_input("🔍 搜索型号")
with col2:
    th = get_alert_threshold()
    new_th = st.number_input("⚠️ 提醒阈值", min_value=1, value=th)
    if new_th != th:
        set_alert_threshold(new_th)
        st.rerun()

filtered = [m for m in all_models if search in m] if search else all_models

favs = get_favorites()
if favs:
    with st.expander("⭐ 我的收藏", expanded=True):
        for m in favs:
            s = df[df["型号"]==m]
            if len(s)<2:
                st.write(f"{m} | 数据不足")
                continue
            s = s.sort_values("时间")
            d = s.iloc[-1]["价格"] - s.iloc[0]["价格"]
            icon = "📈" if d>0 else "📉"
            label = f"【{m}】{icon} {d:+}元 | 当前 {s.iloc[-1]['价格']}元"
            if st.button(label, key=f"fav_{m}", use_container_width=True):
                st.session_state.selected_model = m
                st.session_state.scroll_to_bottom = True
                st.rerun()

st.divider()

with st.expander("📈 近7日 / 近30日涨幅排行", expanded=False):
    c7, c30 = st.columns(2)
    with c7:
        st.markdown("#### 近7天波动TOP10")
        t7 = get_trend(7)
        for item in t7[:10]:
            m = item["model"]
            label = f"【{m}】{item['diff']:+}元 | 当前 {item['last']}元"
            if st.button(label, key=f"t7_{m}", use_container_width=True):
                st.session_state.selected_model = m
                st.session_state.scroll_to_bottom = True
                st.rerun()
    with c30:
        st.markdown("#### 近30天波动TOP10")
        t30 = get_trend(30)
        for item in t30[:10]:
            m = item["model"]
            label = f"【{m}】{item['diff']:+}元 | 当前 {item['last']}元"
            if st.button(label, key=f"t30_{m}", use_container_width=True):
                st.session_state.selected_model = m
                st.session_state.scroll_to_bottom = True
                st.rerun()

st.divider()

with st.expander("📊 价格波动预警", expanded=False):
    alerts = get_alerts()
    up_list = [a for a in alerts if a["trend"] == "上涨"]
    down_list = [a for a in alerts if a["trend"] == "下跌"]
    up_list.sort(key=lambda x: -x["abs_diff"])
    down_list.sort(key=lambda x: -x["abs_diff"])
    col_up, col_down = st.columns(2)
    with col_up:
        st.subheader("📈 涨价排行")
        for a in up_list:
            star = "⭐" if a["is_fav"] else ""
            m = a["model"]
            label = f"{star} {m} | {a['last']}元 | +{a['abs_diff']}元"
            if st.button(label, key=f"up_{m}", use_container_width=True):
                st.session_state.selected_model = m
                st.session_state.scroll_to_bottom = True
                st.rerun()
    with col_down:
        st.subheader("📉 跌价排行")
        for a in down_list:
            star = "⭐" if a["is_fav"] else ""
            m = a["model"]
            label = f"{star} {m} | {a['last']}元 | -{a['abs_diff']}元"
            if st.button(label, key=f"down_{m}", use_container_width=True):
                st.session_state.selected_model = m
                st.session_state.scroll_to_bottom = True
                st.rerun()

st.divider()

# ------------------------------
# 价格排序功能（新增）
# ------------------------------
st.subheader("💰 价格排序")
col_sort1, col_sort2 = st.columns(2)
with col_sort1:
    if st.button("📈 价格从高到低", use_container_width=True):
        sorted_models = get_price_sorted_models(order='desc')
        if sorted_models:
            st.markdown("**型号 | 最新价格**")
            for model, price in sorted_models:
                if st.button(f"{model}  {price}元", key=f"sort_desc_{model}"):
                    st.session_state.selected_model = model
                    st.session_state.scroll_to_bottom = True
                    st.rerun()
        else:
            st.info("暂无数据")
with col_sort2:
    if st.button("📉 价格从低到高", use_container_width=True):
        sorted_models = get_price_sorted_models(order='asc')
        if sorted_models:
            st.markdown("**型号 | 最新价格**")
            for model, price in sorted_models:
                if st.button(f"{model}  {price}元", key=f"sort_asc_{model}"):
                    st.session_state.selected_model = model
                    st.session_state.scroll_to_bottom = True
                    st.rerun()
        else:
            st.info("暂无数据")

st.divider()

# ------------------------------
# 批量录入
# ------------------------------
with st.expander("📝 批量录入", expanded=True):
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()

    txt = st.text_area("粘贴内容", height=200)

    if st.button("🔍 解析", type="primary", use_container_width=True):
        if not txt:
            st.warning("请输入内容")
            st.stop()

        lines = txt.strip().splitlines()
        total_lines = len(lines)
        res = []
        temp_items = []
        
        progress_bar = st.progress(0, text="开始解析...")
        status_text = st.empty()
        
        for idx, li in enumerate(lines):
            progress = (idx + 1) / total_lines
            progress_bar.progress(progress, text=f"正在解析第 {idx+1}/{total_lines} 行...")
            m, p, r = extract_by_regex(li)
            if not m or not p:
                res.append({"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 解析失败"})
                continue
            temp_items.append({"model": m, "price": p, "remark": r.strip(), "raw": li})
        progress_bar.progress(1.0, text="解析完成，正在去重...")
        status_text.text("解析完成，正在去重...")

        unique_batch = {}
        for item in temp_items:
            key = f"{item['model']}_{item['price']}_{item['remark']}"
            if key not in unique_batch:
                unique_batch[key] = item

        # 获取当天已存在的所有记录（通过比较 UTC 时间字符串的日期部分）
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        today_str = today.strftime("%Y-%m-%d")
        all_records = get_all_price_records()
        today_set = set()
        for _, row in all_records.iterrows():
            time_str = row.get("time", "")
            if time_str and time_str[:10] == today_str:
                today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))

        save_list = []
        total_unique = len(unique_batch)
        status_text.text(f"开始校验 {total_unique} 条唯一报价...")
        for idx, (key, item) in enumerate(unique_batch.items()):
            progress = (idx + 1) / total_unique if total_unique > 0 else 1
            progress_bar.progress(progress, text=f"正在校验第 {idx+1}/{total_unique} 条...")
            m = item["model"]
            p = item["price"]
            r = item["remark"]
            raw = item["raw"]
            if (m, p, r) in today_set:
                res.append({"型号":m,"价格":p,"备注":r,"原始":raw,"状态":"⏭️ 已跳过（当天重复）"})
                continue
            ok, reason = llm_verify(m, p, r, raw)
            if ok:
                res.append({"型号":m,"价格":p,"备注":r,"原始":raw,"状态":"✅ 有效"})
                save_list.append({
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                    "model": m,
                    "price": int(p),
                    "remark": str(r).strip()
                })
                today_set.add((m, p, r))
            else:
                res.append({"型号":m,"价格":p,"备注":r,"原始":raw,"状态":f"❌ 无效 ({reason})"})

        progress_bar.empty()
        status_text.empty()
        st.session_state.parse_result = pd.DataFrame(res)

        if save_list:
            with st.spinner(f"正在保存 {len(save_list)} 条数据..."):
                save_batch(save_list)
            st.success(f"✅ 解析并保存 {len(save_list)} 条")
            get_clean_data.clear()
            st.cache_data.clear()
            st.rerun()

    if not st.session_state.parse_result.empty:
        edited_df = st.data_editor(
            st.session_state.parse_result,
            column_config={
                "型号": st.column_config.TextColumn("型号", required=True),
                "价格": st.column_config.NumberColumn("价格", required=True, min_value=0),
                "备注": st.column_config.TextColumn("备注"),
                "原始": st.column_config.TextColumn("原始", disabled=True),
                "状态": st.column_config.TextColumn("状态", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic"
        )

        if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True):
            raw_save_list = []
            for _, row in edited_df.iterrows():
                model = str(row["型号"]).strip()
                price = row["价格"]
                if not (model and len(model) == 5 and model.isdigit()):
                    continue
                try:
                    price = int(price)
                except:
                    continue
                if price < 10:
                    continue
                raw_save_list.append({"model": model, "price": price, "remark": str(row["备注"]).strip()})

            if not raw_save_list:
                st.warning("没有有效数据可保存")
            else:
                unique_save = {}
                for item in raw_save_list:
                    key = f"{item['model']}_{item['price']}_{item['remark']}"
                    if key not in unique_save:
                        unique_save[key] = item

                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                today_str = today.strftime("%Y-%m-%d")
                all_records = get_all_price_records()
                today_set = set()
                for _, row in all_records.iterrows():
                    time_str = row.get("time", "")
                    if time_str and time_str[:10] == today_str:
                        today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))

                final_save = []
                for item in unique_save.values():
                    m = item["model"]
                    p = item["price"]
                    r = item["remark"]
                    if (m, p, r) not in today_set:
                        final_save.append(item)

                if not final_save:
                    st.info("所有数据当天均已存在，无需保存")
                else:
                    records_to_save = []
                    for item in final_save:
                        records_to_save.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": item["model"],
                            "price": int(item["price"]),
                            "remark": item["remark"]
                        })
                    save_batch(records_to_save)
                    st.success(f"✅ 保存成功 {len(records_to_save)} 条")
                    st.session_state.parse_result = pd.DataFrame()
                    get_clean_data.clear()
                    st.cache_data.clear()
                    st.rerun()

st.divider()

# ------------------------------
# 历史数据管理
# ------------------------------
st.subheader("📋 历史数据管理")
if not df.empty:
    idx = 0
    if st.session_state.selected_model in all_models:
        idx = all_models.index(st.session_state.selected_model) + 1

    target = st.selectbox("选择型号", [""] + all_models, index=idx)
    if target:
        st.session_state.selected_model = target
        isfav = target in favs
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
            st.success("✅ 已保存")
            st.rerun()

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
            if t_str and len(t_str) >= 10:
                return t_str[:10]
            return t_str

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
            hide_index=True
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
            st.rerun()

        st.subheader("价格走势")
        fig = px.line(model_data.sort_values("时间"), x="时间", y="价格", markers=True)
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("暂无数据")

if st.session_state.scroll_to_bottom:
    st.components.v1.html(auto_scroll, height=0)
    st.session_state.scroll_to_bottom = False