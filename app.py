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

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# 初始化 session_state
if "parsed_data" not in st.session_state:
    st.session_state["parsed_data"] = None
if "raw_input" not in st.session_state:
    st.session_state["raw_input"] = ""

# ---------- 数据库操作（分页获取全部数据）----------
def fetch_all_records(table_name):
    """分页获取表中的所有记录（处理 Supabase 默认 1000 条限制）"""
    all_data = []
    page = 0
    page_size = 1000
    while True:
        start = page * page_size
        end = start + page_size - 1
        response = supabase.table(table_name).select("*").range(start, end).execute()
        data = response.data
        if not data:
            break
        all_data.extend(data)
        if len(data) < page_size:
            break
        page += 1
    return all_data

@st.cache_data(ttl=60)
def get_trend_data():
    data = fetch_all_records("price_records")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    df['时间'] = pd.to_datetime(df['time'])
    df['型号'] = df['model'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['型号'] = df['型号'].str.extract(r'(\d+)')[0]
    df['价格'] = pd.to_numeric(df['price'], errors='coerce')
    df = df.dropna(subset=['型号', '价格'])
    df = df[df['型号'].str.match(r'^[1-9][0-9]{4}$')]
    if 'remark' not in df.columns:
        df['remark'] = ''
    return df

@st.cache_data(ttl=60)
def get_all_price_records():
    data = fetch_all_records("price_records")
    df = pd.DataFrame(data)
    if 'remark' not in df.columns:
        df['remark'] = ''
    df['model'] = df['model'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    df['model'] = df['model'].str.extract(r'(\d+)')[0]
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
            st.success(f"成功写入 {len(response.data)} 条记录")
            get_trend_data.clear()
            get_all_price_records.clear()
            st.cache_data.clear()
            return len(response.data)
        else:
            st.error("插入成功但无数据返回，可能被 RLS 阻止")
            return 0
    except Exception as e:
        st.error(f"数据库写入异常：{type(e).__name__}: {e}")
        if hasattr(e, 'response'):
            st.error(f"响应内容: {e.response.text if hasattr(e.response, 'text') else e.response}")
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
        return bag
    else:
        return ""

def extract_with_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    remark = extract_remark(line)
    cleaned = re.sub(r'(好盒|压盒|瑕疵|盒损|破损|烂盒|破盒|纸袋|M袋|礼袋|礼品袋|M号袋|S袋|\+袋|带袋|有袋|无袋)', '', line)
    model_match = re.search(r'(?<![0-9])([1-9][0-9]{4})(?![0-9])', cleaned)
    if not model_match:
        return None, None, None
    model = model_match.group(1)
    price_match = re.search(r'^(\d+)', cleaned)
    if not price_match:
        price_match = re.search(r'(\d+)\s*收', cleaned)
    if price_match:
        price = int(price_match.group(1))
        return model, price, remark
    all_numbers = re.findall(r'\b(\d+)\b', cleaned)
    for num in all_numbers:
        if len(num) != 5 or num[0] == '0':
            price = int(num)
            return model, price, remark
    return None, None, None

def preprocess_text(text):
    lines = text.strip().split('\n')
    filtered = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if re.search(r'\d', line) or "收" in line:
            cleaned = re.sub(r'(普快|顺丰|好盒|压盒|包邮|顺丰发出|跨越最好|留底|加固|揽收|打包|山东|内蒙|广东|江苏|安徽|浙江|辽宁|吉林|黑龙江|河北|天津|山西|陕西|广西|云南|四川|贵州|福建|重庆|海南|北京|上海|天津|宁夏|青海|甘肃|新疆|西藏|内蒙古)', '', line)
            if not re.search(r'\d', cleaned):
                continue
            filtered.append(cleaned)
        elif len(line) > 20:
            continue
    return "\n".join(filtered)

def parse_with_llm(text):
    lines = text.strip().split('\n')
    regex_results = []
    remaining_lines = []
    anomaly_alerts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not re.search(r'(?<![0-9])[1-9][0-9]{4}(?![0-9])', line):
            continue
        model, price, remark = extract_with_regex(line)
        if model is not None and price is not None:
            last_price = get_last_price(model)
            if last_price is not None and abs(price - last_price) > 100:
                anomaly_alerts.append((model, price, last_price))
                remaining_lines.append(line)
            else:
                regex_results.append({"model": model, "price": price, "remark": remark})
        else:
            remaining_lines.append(line)
    if anomaly_alerts:
        for model, new_price, last_price in anomaly_alerts:
            st.warning(f"⚠️ 型号 {model} 价格异常波动（从 {last_price} 变为 {new_price}，差值>100），已转模型复核。")
    if not remaining_lines:
        return regex_results

    remaining_text = "\n".join(remaining_lines)
    similar_cases = find_similar_cases(remaining_text, threshold=0.5)
    few_shot_examples = ""
    if similar_cases:
        few_shot_examples = "\n参考以下类似情况的正确解析结果：\n"
        for case in similar_cases[:3]:
            few_shot_examples += f"输入：{case['original']}\n输出：{json.dumps(case['corrected'], ensure_ascii=False)}\n"
    else:
        few_shot_examples = "\n请直接解析。\n"

    prompt = f"""你是一个乐高报价解析助手。从以下文本中提取出每个乐高产品的官方型号编号、价格和备注信息。备注只包括盒况（好盒、压盒、瑕疵等）和袋子（纸袋、M袋等），若两者都有则输出“盒况+有袋”，只有盒况则输出盒况，只有袋子则输出“有袋”，无则输出空字符串。输出一个JSON数组，每个元素包含 model, price, remark 字段。只输出JSON数组。

示例：
输入："1180收 乐高 10320 1 好盒"
输出：[{{"model": "10320", "price": 1180, "remark": "好盒"}}]

输入："880压盒10358声波普快云南+纸袋"
输出：[{{"model": "10358", "price": 880, "remark": "压盒+纸袋"}}]

输入："1350好盒10333M袋"
输出：[{{"model": "10333", "price": 1350, "remark": "好盒+M袋"}}]

{few_shot_examples}

现在请解析：
{remaining_text}

输出："""

    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 16384
    }
    try:
        response = requests.post(
            "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            headers=headers,
            json=payload,
            timeout=120
        )
        if response.status_code == 200:
            result = response.json()
            content = result["choices"][0]["message"]["content"]
            json_match = re.search(r'\[.*\]', content, re.DOTALL)
            if json_match:
                try:
                    model_results = json.loads(json_match.group())
                    for item in model_results:
                        if "remark" not in item:
                            item["remark"] = ""
                    return regex_results + model_results
                except:
                    return regex_results
            else:
                return regex_results
        else:
            st.error(f"智谱API调用失败：{response.status_code}")
            return regex_results
    except Exception as e:
        st.error(f"智谱API调用出错：{e}")
        return regex_results

# ---------- 趋势预警 ----------
def get_model_trend_change(model, n=5):
    df = get_trend_data()
    if df.empty:
        return None
    model = str(model).strip()
    df_model = df[df['型号'] == model].sort_values('时间')
    if len(df_model) < 2:
        return None
    recent = df_model.tail(n)
    first_price = recent.iloc[0]['价格']
    last_price = recent.iloc[-1]['价格']
    return last_price - first_price

def get_trend_alerts():
    df = get_trend_data()
    if df.empty:
        return []
    alerts = []
    models = df['型号'].unique()
    for model in models:
        change = get_model_trend_change(model, n=5)
        if change is None:
            continue
        if abs(change) > 5:
            alerts.append((model, "上涨" if change > 0 else "下跌", change))
    alerts.sort(key=lambda x: abs(x[2]), reverse=True)
    return alerts

def show_trend_chart(model):
    df = get_trend_data()
    if df.empty:
        st.info("暂无数据")
        return
    model = str(model).strip()
    df_model = df[df['型号'] == model].sort_values('时间')
    if df_model.empty:
        similar = df[df['型号'].str.contains(model, na=False)]['型号'].unique()[:5]
        if len(similar) > 0:
            st.info(f"未找到型号 {model}，但找到相似型号: {', '.join(similar)}")
        else:
            st.info(f"型号 {model} 暂无数据")
        return
    fig = px.line(df_model, x='时间', y='价格', title=f"型号 {model} 价格趋势",
                  markers=True, labels={'时间': '时间', '价格': '价格'})
    fig.update_traces(marker=dict(size=8), line=dict(width=2))
    fig.update_layout(hovermode='x unified')
    st.plotly_chart(fig, use_container_width=True)

def get_model_list():
    df = get_trend_data()
    if df.empty:
        return []
    models = sorted(df['型号'].unique())
    return models

# ---------- 组合控件 ----------
def model_selector(key_prefix, label="型号选择", options=None):
    if options is None:
        options = get_model_list()
    manual_input = st.text_input("手动输入型号", key=f"{key_prefix}_manual")
    selected = st.selectbox("或从已有型号中选择", options=[""] + options, key=f"{key_prefix}_select")
    if manual_input.strip():
        return manual_input.strip()
    else:
        return selected if selected else None

# ---------- Streamlit UI ----------
st.set_page_config(page_title="乐高报价助手", layout="wide")
st.title("🧩 乐高报价助手")

# 价格趋势预警
alerts = get_trend_alerts()
with st.expander(f"⚠️ 价格趋势预警（{len(alerts)}个型号）", expanded=False):
    if alerts:
        alert_models = sorted(set([m for m, _, _ in alerts]))
        filter_model = model_selector("alert_filter", "筛选型号（可选）", options=alert_models)
        filtered = alerts
        if filter_model:
            filtered = [(m, t, c) for (m, t, c) in alerts if m == filter_model]
        if not filtered:
            st.info("该型号无预警")
        else:
            for model, trend, change in filtered:
                if trend == "上涨":
                    st.warning(f"📈 型号 {model} 近期价格上涨 {change:.0f} 元")
                else:
                    st.error(f"📉 型号 {model} 近期价格下跌 {abs(change):.0f} 元")
    else:
        st.info("暂无价格预警")
st.markdown("---")

st.markdown("### 输入报价信息")
user_input = st.text_area("把报价文字粘贴或输入到这里，支持多行（最多几百行）", height=300)

col1, col2 = st.columns(2)
with col1:
    if st.button("🔍 解析并记录"):
        if not user_input.strip():
            st.warning("请输入报价内容")
        else:
            with st.spinner("正在解析（正则+AI）..."):
                try:
                    parsed = parse_with_llm(user_input)
                    if parsed:
                        count = save_to_supabase(parsed, user_input)
                        st.success(f"✅ 成功记录 {count} 条报价")
                    else:
                        st.warning("⚠️ 解析未识别到有效数据，请手动输入或纠错。")
                        parsed = []
                    st.session_state["parsed_data"] = parsed
                    st.session_state["raw_input"] = user_input
                    st.rerun()
                except Exception as e:
                    st.error(f"运行出错：{str(e)}")
                    st.exception(e)

with col2:
    if st.button("📊 查看所有型号"):
        models = get_model_list()
        if not models:
            st.info("暂无数据")
        else:
            st.write("已记录的型号：", ", ".join(models))

# 新报价的解析结果编辑
if st.session_state.get("parsed_data") is not None:
    st.markdown("---")
    st.subheader("📝 解析结果（可编辑）")
    st.caption("💡 提示：双击单元格可编辑数据，点击表格右下角“＋”可添加新行。修改后请点击下方“提交纠错”保存。")
    parsed_data = st.session_state.get("parsed_data")
    if not parsed_data:
        df_edit = pd.DataFrame(columns=['model', 'price', 'remark'])
    else:
        df_edit = pd.DataFrame(parsed_data)
        if 'price' in df_edit.columns:
            df_edit['price'] = pd.to_numeric(df_edit['price'], errors='coerce').fillna(0).astype(int)
        if 'remark' not in df_edit.columns:
            df_edit['remark'] = ""
    
    column_config = {
        "model": st.column_config.TextColumn("型号", required=True),
        "price": st.column_config.NumberColumn("价格", required=True, step=1),
        "remark": st.column_config.TextColumn("备注")
    }
    edited_df = st.data_editor(df_edit, num_rows="dynamic", column_config=column_config, key="edit_table")
    
    if st.button("💾 提交纠错"):
        corrected_data = edited_df.to_dict(orient='records')
        corrected_data = [row for row in corrected_data if row.get('model') and row.get('price') is not None]
        if corrected_data:
            for row in corrected_data:
                row['price'] = int(row['price'])
                if 'remark' not in row:
                    row['remark'] = ""
            save_correction(st.session_state.get("raw_input", ""), corrected_data)
            save_to_supabase(corrected_data, st.session_state.get("raw_input", ""))
            st.success("已学习并保存！下次遇到类似输入将自动参考此纠正。")
            st.session_state["parsed_data"] = None
            st.session_state["raw_input"] = ""
            st.rerun()
        else:
            st.warning("未填写有效数据，未保存。")

# 历史数据纠错
st.markdown("---")
st.subheader("✏️ 历史数据纠错")
all_records_df = get_all_price_records()
if all_records_df.empty:
    st.info("暂无历史数据")
else:
    selected_model = model_selector("history", "选择要修改的型号")
    if selected_model:
        filtered_df = all_records_df[all_records_df['model'].astype(str) == selected_model]
    else:
        filtered_df = all_records_df
    if filtered_df.empty:
        st.info("没有匹配的记录")
    else:
        st.caption("以下为匹配的历史记录，可修改型号/价格/备注，或勾选“删除”后点击“保存修改”。")
        if 'remark' not in filtered_df.columns:
            filtered_df['remark'] = ''
        edit_data = filtered_df[['id', 'model', 'price', 'remark', 'time']].copy()
        edit_data['time'] = pd.to_datetime(edit_data['time']).dt.strftime('%Y-%m-%d %H:%M')
        if '删除' not in edit_data.columns:
            edit_data['删除'] = False
        edited_df = st.data_editor(edit_data, 
                                   column_config={
                                       "id": st.column_config.NumberColumn("ID", disabled=True),
                                       "time": st.column_config.TextColumn("时间", disabled=True),
                                       "model": st.column_config.TextColumn("型号"),
                                       "price": st.column_config.NumberColumn("价格", step=1),
                                       "remark": st.column_config.TextColumn("备注"),
                                       "删除": st.column_config.CheckboxColumn("删除", default=False)
                                   },
                                   num_rows="dynamic",
                                   key="history_edit_with_del")
        if st.button("✅ 保存所有修改并删除选中记录"):
            to_delete = edited_df[edited_df['删除'] == True]['id'].tolist()
            for rid in to_delete:
                delete_price_record(rid)
            for idx, row in edited_df.iterrows():
                if row['删除']:
                    continue
                record_id = row['id']
                new_model = str(row['model']).strip()
                new_price = int(row['price'])
                new_remark = row.get('remark', '')
                original = all_records_df[all_records_df['id'] == record_id]
                if not original.empty:
                    old_model = str(original.iloc[0]['model']).strip()
                    old_price = int(original.iloc[0]['price'])
                    old_remark = original.iloc[0].get('remark', '')
                    if new_model != old_model or new_price != old_price or new_remark != old_remark:
                        update_price_record(record_id, new_model, new_price, new_remark)
            st.success("历史数据已更新！")
            st.rerun()

st.markdown("---")
st.subheader("📈 价格趋势查询")
query_model = model_selector("trend", "选择或输入型号")
if query_model:
    show_trend_chart(query_model)

st.markdown("---")
if st.button("📥 导出所有数据到本地"):
    price_response = supabase.table("price_records").select("*").execute()
    df_price = pd.DataFrame(price_response.data)
    corr_response = supabase.table("corrections").select("*").execute()
    df_corr = pd.DataFrame(corr_response.data)
    from io import BytesIO
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_price.to_excel(writer, sheet_name="价格记录", index=False)
        df_corr.to_excel(writer, sheet_name="纠错案例", index=False)
    output.seek(0)
    st.download_button(
        label="点击下载 Excel 文件",
        data=output,
        file_name=f"lego_price_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

st.markdown("---")
st.caption("数据云端存储，多用户共享。备注自动识别（好盒/压盒/瑕疵 + 纸袋/M袋等）。可随时导出本地备份。")