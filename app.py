import os
import re
import json
import requests
import pandas as pd
from datetime import datetime
import streamlit as st
import difflib
import plotly.express as px
from supabase import create_client

# 从环境变量读取密钥
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")
MODEL_NAME = "glm-4-flash"

if not SUPABASE_URL or not SUPABASE_KEY or not ZHIPU_API_KEY:
    st.error("缺少环境变量！请在 Streamlit Cloud 中配置 SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY")
    st.stop()

st.set_page_config(page_title="乐高报价助手", layout="wide")

# 调试输出
st.write("=== 调试信息 ===")
st.write("SUPABASE_URL:", SUPABASE_URL[:20] + "...")
st.write("SUPABASE_KEY length:", len(SUPABASE_KEY) if SUPABASE_KEY else 0)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 初始化 session_state
if "parsed_data" not in st.session_state:
    st.session_state["parsed_data"] = None
if "raw_input" not in st.session_state:
    st.session_state["raw_input"] = ""

# ---------- 分页获取全部记录 ----------
def fetch_all_records(table_name):
    all_data = []
    page_size = 1000
    start = 0
    while True:
        response = supabase.table(table_name).select("*").range(start, start + page_size - 1).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_size:
            break
        start += page_size
    return all_data

@st.cache_data(ttl=60)
def get_trend_data():
    all_data = fetch_all_records("price_records")
    if not all_data:
        return pd.DataFrame()

    df = pd.DataFrame(all_data)
    st.write(f"原始从库中取出：{len(df)} 条")

    # 基础清洗
    df['时间'] = pd.to_datetime(df['time'], errors='coerce')
    df['型号'] = df['model'].astype(str).str.strip()
    df['价格'] = pd.to_numeric(df['price'], errors='coerce')

    # 移除 .0 尾巴（防止 40528.0 变成异常数据）
    df['型号'] = df['型号'].str.replace(r'\.0$', '', regex=True).str.strip()

    # 【严格保留你要的 5 位数字规则】仅保留：首位非0，共5位
    df = df[df['型号'].str.match(r'^[1-9]\d{4}$', na=False)]

    # 只删除真正无效的
    df = df.dropna(subset=['型号', '价格'])
    df = df[df['价格'] > 0]

    st.write(f"get_trend_data 最终有效：{len(df)} 条")
    if len(df) > 0:
        st.write("前 10 个型号：", df['型号'].head(10).tolist())

    return df

@st.cache_data(ttl=60)
def get_all_price_records():
    all_data = fetch_all_records("price_records")
    df = pd.DataFrame(all_data)
    if 'remark' not in df.columns:
        df['remark'] = ''

    # 与趋势数据保持一致的清洗规则
    df['model'] = df['model'].astype(str).str.strip()
    df['model'] = df['model'].str.replace(r'\.0$', '', regex=True).str.strip()
    st.write(f"get_all_price_records 返回 {len(df)} 条")
    return df

def get_last_price(model):
    df = get_trend_data()
    if df.empty:
        return None
    model = str(model).strip()
    df_model = df[df['型号'] == model].sort_values('时间')
    if df_model.empty:
        return None
    return df_model.iloc[-1]['价格']

def save_to_supabase(data, raw_text):
    if not data:
        st.warning("没有数据可保存")
        return 0
    records = []
    for i, item in enumerate(data):
        rec = {
            "time": datetime.now().isoformat(),
            "model": item.get('model'),
            "price": item.get('price'),
            "remark": item.get('remark', ''),
            "raw_text": raw_text if i == 0 else None
        }
        if rec['model'] is None or rec['price'] is None:
            st.warning(f"跳过无效记录：{item}")
            continue
        records.append(rec)
    if not records:
        st.error("没有有效记录可保存")
        return 0
    try:
        response = supabase.table("price_records").insert(records).execute()
        if hasattr(response, 'data') and response.data:
            st.success(f"✅ 成功写入 {len(response.data)} 条记录")
            get_trend_data.clear()
            get_all_price_records.clear()
            st.cache_data.clear()
            return len(response.data)
        else:
            st.error("插入成功但无数据返回")
            return 0
    except Exception as e:
        st.error(f"数据库写入异常：{type(e).__name__}: {e}")
        return 0

def load_corrections():
    response = supabase.table("corrections").select("*").execute()
    data = response.data
    return [{"original": d["original_text"], "corrected": d["corrected_data"]} for d in data]

def save_correction(original_text, corrected_data):
    supabase.table("corrections").insert({
        "original_text": original_text,
        "corrected_data": corrected_data
    }).execute()
    st.cache_data.clear()

def find_similar_cases(text, threshold=0.8):
    corrections = load_corrections()
    similar = []
    for case in corrections:
        ratio = difflib.SequenceMatcher(None, text, case["original"]).ratio()
        if ratio >= threshold:
            similar.append(case)
    return similar

def update_price_record(record_id, new_model, new_price, new_remark):
    supabase.table("price_records").update({
        "model": new_model,
        "price": new_price,
        "remark": new_remark
    }).eq("id", record_id).execute()
    st.cache_data.clear()

def delete_price_record(record_id):
    supabase.table("price_records").delete().eq("id", record_id).execute()
    st.cache_data.clear()

# ---------- 备注提取（盒况+袋子）----------
def extract_remark(line):
    box_keywords = ["好盒", "压盒", "瑕疵", "盒损", "破损", "烂盒", "破盒"]
    bag_keywords = ["纸袋", "M袋", "礼袋", "礼品袋", "M号袋", "S袋", "+袋", "带袋", "有袋", "无袋"]

    box = None
    bag = None
    for kw in box_keywords:
        if kw in line:
            box = kw
            break
    for kw in bag_keywords:
        if kw in line:
            bag = kw if kw != "无袋" else "无袋"
            break
    if box and bag:
        return f"{box}+{bag}"
    elif box:
        return box
    elif bag:
        return "有袋"
    else:
        return ""

# ---------- 【核心】严格 5 位数字正则提取（省Token主力）----------
def extract_with_regex(line):
    line = line.strip()
    if not line:
        return None, None, None

    remark = extract_remark(line)
    # 移除备注干扰
    cleaned = re.sub(r'(好盒|压盒|瑕疵|盒损|破损|烂盒|破盒|纸袋|M袋|礼袋|礼品袋|M号袋|S袋|\+袋|带袋|有袋|无袋)', '', line)

    # 严格匹配 5 位型号：首位非0
    model_match = re.search(r'(?<![0-9])([1-9][0-9]{4})(?![0-9])', cleaned)
    if not model_match:
        return None, None, None
    model = model_match.group(1)

    # 提取价格（找型号之外的数字）
    price_clean = cleaned.replace(model, '')
    price_match = re.search(r'(\d+)', price_clean)
    if not price_match:
        return None, None, None

    try:
        price = int(price_match.group(1))
        if price <= 0:
            return None, None, None
        return model, price, remark
    except:
        return None, None, None

def preprocess_text(text):
    lines = text.strip().split('\n')
    filtered = []
    for line in lines:
        line = line.strip()
        if line and re.search(r'\d', line):
            filtered.append(line)
    return "\n".join(filtered)

# ---------- 解析流程：正则95% + AI5%（最省Token）----------
def parse_with_llm(text):
    lines = text.strip().split('\n')
    regex_results = []
    remaining_lines = []

    # 第一步：正则优先（几乎所有情况都能解决）
    for line in lines:
        line = line.strip()
        if not line:
            continue
        model, price, remark = extract_with_regex(line)
        if model and price:
            regex_results.append({"model": model, "price": price, "remark": remark})
        else:
            remaining_lines.append(line)

    st.write(f"✅ 正则直接解析：{len(regex_results)} 条 | 待AI复核：{len(remaining_lines)} 条")

    # 无剩余则直接返回，不调用AI
    if not remaining_lines:
        return regex_results

    # 第二步：仅剩余疑难杂症调用大模型（极省Token）
    remaining_text = "\n".join(remaining_lines)
    similar_cases = find_similar_cases(remaining_text, threshold=0.5)
    few_shot_examples = ""
    if similar_cases:
        few_shot_examples = "\n参考类似案例：\n"
        for case in similar_cases[:3]:
            few_shot_examples += f"输入：{case['original']}\n输出：{json.dumps(case['corrected'], ensure_ascii=False)}\n"

    prompt = f"""你是乐高报价提取助手。
提取：型号（必须5位数字）、价格、备注（好盒/压盒/有袋）。
输出纯JSON数组，不要其他内容。

{few_shot_examples}

输入：
{remaining_text}

输出：
"""

    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    try:
        response = requests.post("https://open.bigmodel.cn/api/paas/v4/chat/completions", headers=headers, json=payload, timeout=30)
        if response.status_code == 200:
            content = response.json()["choices"][0]["message"]["content"]
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                return regex_results + json.loads(json_match.group())
        return regex_results
    except Exception as e:
        st.error(f"AI调用异常：{str(e)}")
        return regex_results

# ---------- 趋势 ----------
def get_model_trend_change(model, n=5):
    df = get_trend_data()
    if df.empty:
        return None
    model = str(model).strip()
    df_model = df[df['型号'] == model].sort_values('时间')
    if len(df_model) < 2:
        return None
    return df_model.tail(n).iloc[-1]['价格'] - df_model.tail(n).iloc[0]['价格']

def get_trend_alerts():
    df = get_trend_data()
    if df.empty:
        return []
    alerts = []
    for m in df['型号'].unique():
        chg = get_model_trend_change(m)
        if chg and abs(chg) >= 5:
            alerts.append((m, "上涨" if chg > 0 else "下跌", chg))
    return sorted(alerts, key=lambda x: abs(x[2]), reverse=True)

def show_trend_chart(model):
    df = get_trend_data()
    model = str(model).strip()
    st.write(f"查询型号：{model}，总数据量：{len(df)}")

    df_model = df[df['型号'] == model].sort_values('时间')
    st.write(f"匹配到 {len(df_model)} 条记录")

    if df_model.empty:
        sim = df[df['型号'].str.contains(model, na=False)]['型号'].unique()[:5]
        if len(sim):
            st.info(f"未找到 {model}，相似型号：{', '.join(sim)}")
        else:
            st.info("无数据")
        return

    fig = px.line(df_model, x='时间', y='价格', title=f"{model} 价格趋势", markers=True)
    st.plotly_chart(fig, use_container_width=True)

def get_model_list():
    df = get_trend_data()
    if df.empty:
        return []
    models = sorted(df['型号'].dropna().unique())
    st.write(f"型号总数：{len(models)}")
    return models

# ---------- 型号选择 ----------
def model_selector(key_prefix, label="型号"):
    options = get_model_list()
    txt = st.text_input("手动输入型号", key=f"{key_prefix}_txt")
    sel = st.selectbox("或选择已有型号", [""] + options, key=f"{key_prefix}_sel")
    return txt.strip() or sel.strip()

# ---------- UI ----------
st.title("🧩 乐高报价助手")

alerts = get_trend_alerts()
with st.expander(f"⚠️ 价格预警 ({len(alerts)})"):
    for m, t, c in alerts:
        if t == "上涨":
            st.warning(f"📈 {m} 上涨 {c:.0f} 元")
        else:
            st.error(f"📉 {m} 下跌 {abs(c):.0f} 元")

st.markdown("---")
st.subheader("输入报价")
user_input = st.text_area("粘贴内容", height=260)

col1, col2 = st.columns(2)
with col1:
    if st.button("🔍 解析并保存"):
        if not user_input.strip():
            st.warning("请输入内容")
        else:
            with st.spinner("解析中..."):
                parsed = parse_with_llm(user_input)
                if parsed:
                    save_to_supabase(parsed, user_input)
                    st.success(f"解析到 {len(parsed)} 条")
                else:
                    st.warning("未识别到数据")
                st.session_state["parsed_data"] = parsed
                st.session_state["raw_input"] = user_input
                st.rerun()

with col2:
    if st.button("📊 查看所有型号"):
        m = get_model_list()
        st.write(", ".join(m[:20]))

# 编辑解析结果
if st.session_state.get("parsed_data") is not None:
    st.markdown("---")
    st.subheader("📝 编辑解析结果")
    df_edit = pd.DataFrame(st.session_state["parsed_data"])
    if 'remark' not in df_edit:
        df_edit['remark'] = ''
    edited = st.data_editor(df_edit, num_rows="dynamic", key="edit")

    if st.button("💾 保存纠错"):
        final = edited.to_dict('records')
        final = [r for r in final if r.get('model') and r.get('price')]
        if final:
            save_correction(st.session_state["raw_input"], final)
            save_to_supabase(final, st.session_state["raw_input"])
            st.success("已保存！")
            st.session_state["parsed_data"] = None
            st.rerun()

# 历史数据修改
st.markdown("---")
st.subheader("✏️ 历史数据管理")
df_all = get_all_price_records()
if df_all.empty:
    st.info("暂无数据")
else:
    m_sel = model_selector("history")
    if m_sel:
        df_all = df_all[df_all['model'] == m_sel]

    df_show = df_all[['id', 'model', 'price', 'remark', 'time']].copy()
    df_show['time'] = pd.to_datetime(df_show['time']).dt.strftime('%Y-%m-%d %H:%M')
    df_show['删除'] = False

    edited_hist = st.data_editor(df_show)

    if st.button("✅ 保存修改/删除"):
        for _, r in edited_hist.iterrows():
            if r['删除']:
                delete_price_record(r['id'])
            else:
                update_price_record(r['id'], str(r['model']), int(r['price']), r.get('remark', ''))
        st.success("已更新")
        st.rerun()

# 趋势查询
st.markdown("---")
st.subheader("📈 价格趋势")
q_model = model_selector("trend")
if q_model:
    show_trend_chart(q_model)

# 导出
st.markdown("---")
if st.button("📥 导出全部数据"):
    p = pd.DataFrame(fetch_all_records("price_records"))
    c = pd.DataFrame(fetch_all_records("corrections"))
    from io import BytesIO
    io = BytesIO()
    with pd.ExcelWriter(io, engine='openpyxl') as w:
        p.to_excel(w, sheet_name="报价", index=False)
        c.to_excel(w, sheet_name="纠错", index=False)
    st.download_button("下载 Excel", io, f"lego_backup_{datetime.now():%Y%m%d%H%M%S}.xlsx")

st.caption("✅ 严格5位正则 | ✅ 省Token | ✅ 不丢数据 | ✅ 写入即查到")