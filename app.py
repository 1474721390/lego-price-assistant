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

st.set_page_config(page_title="乐高报价系统", layout="wide")

if "initialized" not in st.session_state:
    st.session_state.clear()
    st.session_state["initialized"] = True

# 初始化筛选和排序状态
if "global_filters" not in st.session_state:
    st.session_state.global_filters = {}
if "global_sort_column" not in st.session_state:
    st.session_state.global_sort_column = "时间"
if "global_sort_ascending" not in st.session_state:
    st.session_state.global_sort_ascending = False
if "global_page_size" not in st.session_state:
    st.session_state.global_page_size = 20
if "global_current_page" not in st.session_state:
    st.session_state.global_current_page = 1

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

# ==================== 修正后的趋势函数 ====================
def get_price_trend(model: str, current_price: int) -> str:
    """获取价格趋势 emoji，与该型号上一次记录的价格对比"""
    df = get_clean_data()
    past = df[df["型号"] == model]
    if len(past) < 2:
        return "—"  # 数据不足，无法判断趋势
    
    # === 关键修正：必须按时间倒序排序，确保最新在前 ===
    past_sorted = past.sort_values("时间", ascending=False)
    # 当前价格应为最新（past_sorted.iloc[0]），上一次为 past_sorted.iloc[1]
    last_price = past_sorted.iloc[1]["价格"]
    
    if current_price > last_price:
        return "📈"
    elif current_price < last_price:
        return "📉"
    else:
        return "—"

# ==================== 计算涨跌金额函数（新增） ====================
def get_price_change(model: str, current_price: int) -> str:
    """计算与上一次价格的差额，返回格式化的涨跌金额"""
    df = get_clean_data()
    past = df[df["型号"] == model]
    if len(past) < 2:
        return "—"  # 数据不足
    
    past_sorted = past.sort_values("时间", ascending=False)
    last_price = past_sorted.iloc[1]["价格"]
    diff = current_price - last_price
    
    if diff > 0:
        return f"+¥{diff}"
    elif diff < 0:
        return f"-¥{abs(diff)}"
    else:
        return "±¥0"

def get_price_change_value(model: str, current_price: int) -> int:
    """返回涨跌金额的数值（用于排序）"""
    df = get_clean_data()
    past = df[df["型号"] == model]
    if len(past) < 2:
        return 0
    
    past_sorted = past.sort_values("时间", ascending=False)
    last_price = past_sorted.iloc[1]["价格"]
    return current_price - last_price

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

def extract_by_llm_full(line):
    """使用大模型从整行文本中提取结构化数据（当正则结果可疑时调用）"""
    prompt = f"""你是乐高价格信息提取专家。
请从以下用户输入中提取：乐高型号（5位数字）、价格（数字）、备注（如盒况/袋况）。
输入文本：{line}
只返回一个 JSON 对象，格式：{{"model": "字符串", "price": 数字, "remark": "字符串"}}
如果无法提取，返回：{{"model": null, "price": null, "remark": ""}}"""

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
                    model = res.get("model")
                    price = res.get("price")
                    remark = res.get("remark", "")
                    # 类型校验
                    if isinstance(model, str) and len(model) == 5 and model.isdigit() and model[0] != '0':
                        try:
                            price = float(price)
                            if 10 <= price <= 8000:
                                return model, int(price), str(remark)
                        except (TypeError, ValueError):
                            pass
                return None, None, ""
            else:
                continue
        except Exception:
            continue
    return None, None, ""

def should_use_ai_fallback(model, price, line):
    """判断是否需要调用 AI 重新提取（而不是直接拒绝）"""
    latest = get_latest_history()
    
    # 检查价格是否在合理范围内
    if not (10 <= price <= 8000):
        return True
    
    # 检查型号格式
    if not (model and len(model) == 5 and model.isdigit() and model[0] != '0'):
        return True
    
    # 检查价格变动是否过大（>200）
    if model in latest:
        last_price = latest[model]["price"]
        if abs(price - last_price) > 200:
            return True
    
    return False

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
def save_batch_one_by_one(records):
    """逐条保存，避免批量插入失败"""
    success_count = 0
    for record in records:
        try:
            supabase.table("price_records").insert(record).execute()
            success_count += 1
        except Exception as e:
            print(f"保存失败: {e}")
            continue
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

auto_scroll = """
<script>
    window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
</script>
"""

# ==================== 辅助函数：将列表渲染为网格按钮 ====================
def render_grid_buttons(items, columns=3, prefix=""):
    """
    items: list of (显示文本, 型号)
    columns: 每行按钮数
    prefix: 用于生成唯一key的前缀
    """
    if not items:
        return
    for i in range(0, len(items), columns):
        cols = st.columns(columns)
        for j, col in enumerate(cols):
            idx = i + j
            if idx < len(items):
                label, model = items[idx]
                # 使用prefix + model + idx 确保唯一性
                key = f"{prefix}_{model}_{idx}"
                if col.button(label, key=key, use_container_width=True):
                    st.session_state.selected_model = model
                    st.session_state.scroll_to_bottom = True
                    st.rerun()

# ==================== 界面 ====================
st.title("🧩 乐高报价分析系统")

# --- 批量录入区域移到最顶部 ---
with st.expander("📝 批量录入", expanded=True):
    if "parse_result" not in st.session_state:
        st.session_state.parse_result = pd.DataFrame()
    if "original_parse" not in st.session_state:
        st.session_state.original_parse = []

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

        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        today_str = today.strftime("%Y-%m-%d")
        all_records = get_all_price_records()
        today_set = set()
        for _, row in all_records.iterrows():
            time_str = row.get("time", "")
            if time_str and time_str[:10] == today_str:
                today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))

        # ====== 修改点 1: 只保存状态为"有效"或"AI修正"的行 ======
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
            
            # ====== 调用 AI 逻辑保持不变 ======
            use_ai = should_use_ai_fallback(m, p, raw)
            final_model, final_price, final_remark = m, p, r
            ai_used = False
            
            if use_ai:
                ai_model, ai_price, ai_remark = extract_by_llm_full(raw)
                if ai_model and ai_price:
                    final_model, final_price, final_remark = ai_model, ai_price, ai_remark
                    ai_used = True
                    res.append({
                        "型号": final_model,
                        "价格": final_price,
                        "备注": final_remark,
                        "原始": raw,
                        "状态": f"✅ 有效（AI 修正）"
                    })
                else:
                    res.append({
                        "型号": m,
                        "价格": p,
                        "备注": r,
                        "原始": raw,
                        "状态": f"⚠️ 可疑（价格变动>{abs(p - get_latest_history().get(m, {}).get('price', p))}）"
                    })
            else:
                # 正常数据，直接通过
                res.append({
                    "型号": m,
                    "价格": p,
                    "备注": r,
                    "原始": raw,
                    "状态": "✅ 有效"
                })
            
            # ====== 关键修改: 只有"有效"或"AI修正"才加入保存列表 ======
            if "✅ 有效" in res[-1]["状态"]:
                save_list.append({
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                    "model": final_model,
                    "price": int(final_price),
                    "remark": str(final_remark).strip()
                })
                today_set.add((final_model, final_price, final_remark))

        progress_bar.empty()
        status_text.empty()
        st.session_state.parse_result = pd.DataFrame(res)
        # ====== 保存原始解析结果，用于后续对比 ======
        st.session_state.original_parse = res.copy()

        if save_list:
            with st.spinner(f"正在保存 {len(save_list)} 条有效数据..."):
                saved_count = save_batch_one_by_one(save_list)
            st.success(f"✅ 解析并自动保存 {saved_count} 条有效数据")
            get_clean_data.clear()
            st.cache_data.clear()
            st.rerun()

    if not st.session_state.parse_result.empty:
        # ====== 添加趋势列和涨跌金额列（关键修改） ======
        df_with_trend = st.session_state.parse_result.copy()
        
        # 趋势列（显示📈📉）
        df_with_trend["趋势"] = df_with_trend.apply(
            lambda row: get_price_trend(row["型号"], row["价格"]) if row["型号"] and row["价格"] > 0 else "—", axis=1
        )
        
        # 涨跌金额列（新增：显示具体金额）
        df_with_trend["涨跌"] = df_with_trend.apply(
            lambda row: get_price_change(row["型号"], row["价格"]) if row["型号"] and row["价格"] > 0 else "—", axis=1
        )
        
        # 重新排列列顺序（新增"涨跌"列）
        cols_order = ["型号", "价格", "趋势", "涨跌", "备注", "原始", "状态"]
        df_display = df_with_trend[cols_order]

        edited_df = st.data_editor(
            df_display,
            column_config={
                "型号": st.column_config.TextColumn("型号", required=True),
                "价格": st.column_config.NumberColumn("价格", required=True, min_value=0),
                "趋势": st.column_config.TextColumn("趋势", disabled=True),
                "涨跌": st.column_config.TextColumn("涨跌", disabled=True),  # 新增列
                "备注": st.column_config.TextColumn("备注"),
                "原始": st.column_config.TextColumn("原始", disabled=True),
                "状态": st.column_config.TextColumn("状态", disabled=True),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic"
        )

        # ====== 修改点 2: 统计信息放在表格上方 ======
        total = len(st.session_state.parse_result)
        valid = sum(1 for r in st.session_state.parse_result.to_dict('records') if "✅ 有效" in r["状态"])
        ai_fixed = sum(1 for r in st.session_state.parse_result.to_dict('records') if "AI 修正" in r["状态"])
        manual = sum(1 for r in st.session_state.parse_result.to_dict('records') if "需手动" in r["状态"] or "解析失败" in r["状态"])
        st.markdown(f"📊 **本轮解析**：总 {total} 条｜✅ 有效 {valid}｜🤖 AI修正 {ai_fixed}｜✏️ 需手动 {manual}")

        if st.button("💾 修改并保存有效数据", type="primary", use_container_width=True):
            # ====== 关键修改: 只处理用户实际修改过的行 ======
            original_dict = {i: row for i, row in enumerate(st.session_state.original_parse)}
            save_list_manual = []
            
            for idx, (_, edited_row) in enumerate(edited_df.iterrows()):
                original_row = original_dict.get(idx, {})
                # 判断是否被修改
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
                
                # 构建新条目
                new_item = {"model": model, "price": price, "remark": str(edited_row["备注"]).strip()}
                save_list_manual.append(new_item)

            if not save_list_manual:
                st.warning("没有检测到任何修改，无需保存")
            else:
                # 去重和检查当天是否存在
                today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
                today_str = today.strftime("%Y-%m-%d")
                all_records = get_all_price_records()
                today_set = set()
                for _, row in all_records.iterrows():
                    time_str = row.get("time", "")
                    if time_str and time_str[:10] == today_str:
                        today_set.add((row["model"], row["price"], str(row.get("remark", "")).strip()))

                final_save = []
                for item in save_list_manual:
                    m = item["model"]
                    p = item["price"]
                    r = item["remark"]
                    if (m, p, r) not in today_set:
                        final_save.append(item)
                        today_set.add((m, p, r)) # 防止本次保存内重复

                if not final_save:
                    st.info("所有修改后的数据当天均已存在，无需保存")
                else:
                    records_to_save = []
                    for item in final_save:
                        records_to_save.append({
                            "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                            "model": item["model"],
                            "price": int(item["price"]),
                            "remark": item["remark"]
                        })
                    saved_count = save_batch_one_by_one(records_to_save)
                    st.success(f"✅ 成功保存 {saved_count} 条修正数据")
                    st.session_state.parse_result = pd.DataFrame()
                    st.session_state.original_parse = []
                    get_clean_data.clear()
                    st.cache_data.clear()
                    st.rerun()


# --- 移除搜索框，将提醒阈值放入折叠面板 ---
with st.expander("⚙️ 设置", expanded=False):
    col1, col2 = st.columns([3,1])
    with col1:
        st.write("")
    with col2:
        th = get_alert_threshold()
        new_th = st.number_input("⚠️ 提醒阈值", min_value=1, value=th)
        if new_th != th:
            set_alert_threshold(new_th)
            st.rerun()

df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

if 'selected_model' not in st.session_state:
    st.session_state.selected_model = ""
if 'scroll_to_bottom' not in st.session_state:
    st.session_state.scroll_to_bottom = False

# --- 顶部导航栏 (Tabs) ---
tab1, tab2, tab3, tab4 = st.tabs(["⭐ 我的收藏", "📈 涨跌幅排行", "📊 价格预警", "💰 价格区间筛选"])

# ------------------------------
# Tab 1: 我的收藏
# ------------------------------
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

# ------------------------------
# Tab 2: 近7/30天涨幅排行
# ------------------------------
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

# ------------------------------
# Tab 3: 价格预警（修改为带价格区间筛选和翻页的表格）
# ------------------------------
with tab3:
    st.markdown("#### 🔍 价格区间筛选")
    
    # 价格区间输入（默认0～50）
    col_min, col_max = st.columns(2)
    with col_min:
        min_price = st.number_input("最低价格", min_value=0, value=0, step=1, key="alert_min_price")
    with col_max:
        max_price = st.number_input("最高价格", min_value=0, value=50, step=1, key="alert_max_price")
    
    st.divider()
    
    # 获取数据并筛选
    df_clean = get_clean_data()
    if not df_clean.empty:
        # 获取每个型号的最新记录
        latest_df = df_clean.sort_values('时间').groupby('型号').tail(1)
        
        # 应用价格筛选
        if max_price > 0:
            filtered_df = latest_df[(latest_df['价格'] >= min_price) & (latest_df['价格'] <= max_price)]
        else:
            filtered_df = latest_df[latest_df['价格'] >= min_price]
        
        if not filtered_df.empty:
            # 准备显示数据
            display_data = []
            for _, row in filtered_df.iterrows():
                model = row["型号"]
                price = row["价格"]
                remark = str(row.get("remark", "")).strip()
                time_str = row["原始时间"]
                
                # 计算趋势和涨跌
                trend = get_price_trend(model, price)
                change = get_price_change(model, price)
                
                display_data.append({
                    "型号": model,
                    "价格": price,
                    "趋势": trend,
                    "涨跌": change,
                    "备注": remark,
                    "时间": time_str
                })
            
            result_df = pd.DataFrame(display_data)
            
            # 排序（按时间倒序）
            result_df = result_df.sort_values("时间", ascending=False).reset_index(drop=True)
            
            # 分页设置
            PAGE_SIZE = 20
            total_records = len(result_df)
            total_pages = (total_records + PAGE_SIZE - 1) // PAGE_SIZE
            
            # 初始化页码
            if 'alert_page' not in st.session_state:
                st.session_state.alert_page = 1
            
            current_page = st.session_state.alert_page
            
            # 翻页按钮（首页/上一页/下一页）
            col_first, col_prev, col_next, col_info = st.columns([1, 1, 1, 2])
            
            with col_first:
                if st.button("⏮️ 首页", key="alert_first_page"):
                    st.session_state.alert_page = 1
                    st.rerun()
            with col_prev:
                if st.button("◀️ 上一页", key="alert_prev_page") and current_page > 1:
                    st.session_state.alert_page -= 1
                    st.rerun()
            with col_next:
                if st.button("▶️ 下一页", key="alert_next_page") and current_page < total_pages:
                    st.session_state.alert_page += 1
                    st.rerun()
            with col_info:
                st.markdown(f"<div style='text-align: right; line-height: 38px;'>"
                           f"第 <b>{current_page}</b> / <b>{total_pages}</b> 页，共 <b>{total_records}</b> 条</div>",
                           unsafe_allow_html=True)
            
            # 计算当前页数据
            start_idx = (current_page - 1) * PAGE_SIZE
            end_idx = min(start_idx + PAGE_SIZE, total_records)
            page_data = result_df.iloc[start_idx:end_idx]
            
            # 显示表格
            st.dataframe(
                page_data,
                column_config={
                    "型号": st.column_config.TextColumn("🧱 型号", width="small"),
                    "价格": st.column_config.NumberColumn("💰 价格", format="¥%d", width="small"),
                    "趋势": st.column_config.TextColumn("📈 趋势", width="small"),
                    "涨跌": st.column_config.TextColumn("📊 涨跌", width="small"),
                    "备注": st.column_config.TextColumn("📦 备注", width="medium"),
                    "时间": st.column_config.TextColumn("⏰ 时间", width="medium")
                },
                use_container_width=True,
                hide_index=True,
                height=400
            )
            
            # 页码指示器
            st.caption(f"显示第 {start_idx+1} - {end_idx} 条")
        else:
            st.info(f"在价格区间 ¥{min_price} - ¥{max_price} 内未找到数据")
    else:
        st.info("暂无数据")

# ------------------------------
# Tab 4: 价格区间筛选（替换原排序）
# ------------------------------
with tab4:
    st.markdown("#### 🔍 价格区间筛选")
    col_min, col_max = st.columns(2)
    with col_min:
        min_price = st.number_input("最低价格", min_value=0, value=0, step=10)
    with col_max:
        max_price = st.number_input("最高价格", min_value=0, value=10000, step=10)
    
    if min_price >= max_price and max_price > 0:
        st.warning("最高价格应大于最低价格")
    else:
        df_clean = get_clean_data()
        if not df_clean.empty:
            # 获取每个型号的最新记录
            latest_df = df_clean.sort_values('时间').groupby('型号').tail(1)
            # 应用价格筛选
            if max_price > 0:
                filtered_df = latest_df[(latest_df['价格'] >= min_price) & (latest_df['价格'] <= max_price)]
            else:
                filtered_df = latest_df[latest_df['价格'] >= min_price]
            
            # 默认按价格从高到低排序
            filtered_df = filtered_df.sort_values('价格', ascending=False)
            
            if not filtered_df.empty:
                items = [(f"{row['型号']} | {row['价格']}元", row['型号']) for _, row in filtered_df.iterrows()]
                total_items = len(items)
                default_page_size = 20
                page_size = st.selectbox("每页显示", options=[10, 20, 50], index=1, key="page_size_select_tab4")
                total_pages = max(1, (total_items + page_size - 1) // page_size)
                current_page = st.session_state.get("current_page_tab4", 1)
                current_page = st.slider("页码", 1, total_pages, current_page, key="page_slider_tab4")
                st.session_state.current_page_tab4 = current_page

                paginated_items = paginate(items, page_size, current_page)
                render_grid_buttons(paginated_items, columns=2, prefix="filter_tab4")
                st.caption(f"共 {total_items} 条数据，当前显示第 {current_page} 页，共 {total_pages} 页")
            else:
                st.info("未找到符合条件的数据")
        else:
            st.info("暂无数据")


# ------------------------------
# 历史数据管理（保持在底部）
# ------------------------------
st.divider()
st.subheader("📋 历史数据管理")
if not df.empty:
    idx = 0
    if st.session_state.selected_model in all_models:
        idx = all_models.index(st.session_state.selected_model) + 1

    target = st.selectbox("选择乐高型号", [""] + all_models, index=idx)
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
                st.info(f"当前价 ¥{cur} → {tip}")

            # === 🔥 新增：乐高历史价格趋势表格（带涨跌金额）===
            st.subheader("📊 乐高历史价格趋势（含涨跌）")
            
            history_trend_data = []
            for _, row in model_data.iterrows():
                model = row["型号"]
                price = row["价格"]
                remark = str(row.get("remark", "")).strip()
                time_str = row["原始时间"]
                
                # 复用已有函数计算趋势和涨跌
                trend = get_price_trend(model, price)
                change = get_price_change(model, price)
                
                history_trend_data.append({
                    "乐高型号": model,
                    "价格": price,
                    "趋势": trend,
                    "涨跌金额": change,
                    "备注（成色/箱说等）": remark,
                    "记录时间": time_str
                })
            
            history_df = pd.DataFrame(history_trend_data)
            st.dataframe(
                history_df,
                column_config={
                    "乐高型号": st.column_config.TextColumn("🧱 乐高型号"),
                    "价格": st.column_config.NumberColumn("💰 价格 (¥)"),
                    "趋势": st.column_config.TextColumn("📈 趋势"),
                    "涨跌金额": st.column_config.TextColumn("📊 涨跌"),
                    "备注（成色/箱说等）": st.column_config.TextColumn("📦 备注"),
                    "记录时间": st.column_config.TextColumn("⏰ 记录时间")
                },
                use_container_width=True,
                hide_index=True
            )
            # ======================================

            # === 原有可编辑表格（用于删除/修改）===
            def format_date(t_str):
                if t_str and len(t_str) >= 10:
                    return t_str[:10]
                return t_str

            show = model_data[["id", "原始时间", "型号", "价格", "remark"]].copy()
            show["日期"] = show["原始时间"].apply(format_date)
            show.rename(columns={"remark":"备注"}, inplace=True)
            show.insert(0, "删除", False)

            st.subheader("✏️ 编辑/删除历史记录")
            ed_table = st.data_editor(
                show,
                column_config={
                    "删除": st.column_config.CheckboxColumn("删除"),
                    "型号": st.column_config.TextColumn("乐高型号"),
                    "价格": st.column_config.NumberColumn("价格 (¥)"),
                    "备注": st.column_config.TextColumn("备注（成色/箱说等）"),
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
                st.success("✅ 修改已保存")
                get_clean_data.clear()
                st.rerun()

            st.subheader("📈 价格走势")
            fig = px.line(
                model_data.sort_values("时间"),
                x="时间",
                y="价格",
                markers=True,
                title=f"🧱 {target} 历史价格走势"
            )
            fig.update_layout(
                yaxis_title="💰 价格 (¥)",
                xaxis_title="⏰ 时间",
                hovermode="x unified"
            )
            st.plotly_chart(fig, use_container_width=True)
else:
    st.info("📦 暂无乐高价格数据，请先批量录入")

if st.session_state.scroll_to_bottom:
    st.components.v1.html(auto_scroll, height=0)
    st.session_state.scroll_to_bottom = False