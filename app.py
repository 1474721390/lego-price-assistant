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
    st.error("❌ 请配置环境变量")
    st.stop()

st.set_page_config(page_title="乐高报价系统", layout="centered")
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
    df["时间"] = pd.to_datetime(df["time"], errors="coerce")
    df["型号"] = df["model"].astype(str).str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^[1-9]\d{4}$', na=False)]
    df = df.dropna(subset=["型号", "价格"])
    df = df[(df["价格"]>0) & (df["价格"]<100000)]
    return df

# ==================== 获取每个型号最新一条历史记录 ====================
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
                "time": row["时间"].isoformat() if pd.notna(row["时间"]) else ""
            }
    return latest

# ==================== 检查当天是否有重复记录（核心修改点） ====================
def check_today_duplicate(model, price, remark):
    # 计算当天零点的ISO时间
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    # 查询当天内相同型号、价格、备注的记录
    res = supabase.table("price_records").select("id") \
        .eq("model", model) \
        .eq("price", price) \
        .eq("remark", remark) \
        .gte("time", today_start) \
        .execute()
    return len(res.data) > 0  # 存在则返回True（重复）

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

# ==================== 解析函数 ====================
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None

    remark = extract_remark(line)
    # 提取所有数字
    all_digits = re.findall(r'\d+', line)
    if len(all_digits) < 2:
        return None, None, None

    # 1. 优先锁定 5位乐高型号 (1-9开头)
    model_candidates = [d for d in all_digits if len(d) == 5 and d[0] != '0']
    if not model_candidates:
        return None, None, None
    model = model_candidates[0]

    # 2. 从剩下数字里提取价格（排除型号本身）
    price_candidates = [int(p) for p in all_digits if p != model]
    valid_prices = [p for p in price_candidates if 10 <= p <= 8000]

    if valid_prices:
        price = max(valid_prices)  # 取最合理的报价
    elif price_candidates:
        price = max(price_candidates)
    else:
        return None, None, None

    return model, price, remark

# ==================== AI校验 ====================
def llm_verify(model, price, remark, line):
    latest = get_latest_history()
    need_ai = False

    # 价格合理性：10~8000
    price_ok = 10 <= price <= 8000
    model_ok = model and len(model) == 5 and model.isdigit()
    price_change_ok = True
    if model in latest:
        last_price = latest[model]["price"]
        if abs(price - last_price) > 200:
            price_change_ok = False
            need_ai = True

    # 自动通过条件
    if price_ok and model_ok and price_change_ok:
        return True, "正常，自动通过"

    # 其他情况需要 AI 校验
    if not price_ok or not model_ok:
        need_ai = True

    if not need_ai:
        return True, "自动通过"

    # 构造提示词
    prompt = f"""你是乐高价格识别校验器。
文本：{line}
提取结果：型号={model}，价格={price}，备注={remark}
判断是否真实有效单价，是否提取错误。
注意：价格就是数字，不需要单位，行中的“收”、“元”、“块”等字是无关的，忽略它们。
只返回一个 JSON 对象，格式：{{"is_valid": true/false, "reason": "说明"}}"""

    max_retries = 2
    for attempt in range(max_retries):
        try:
            response = requests.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={
                    "Authorization": f"Bearer {ZHIPU_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1
                },
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

# ==================== 近7/30天排行 ====================
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

        trends.append({
            "model": m,
            "diff": diff,
            "abs_diff": abs(diff),
            "last": new
        })

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

# ==================== 自动滚动脚本 ====================
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

# ------------------------------
# 收藏面板
# ------------------------------
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

# ------------------------------
# 近7/30天排行
# ------------------------------
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

# ------------------------------
# 价格波动预警
# ------------------------------
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
            if st