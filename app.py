# ===========================================
# 乐高报价系统 - 彻底修正版
# ===========================================
import os
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

# 页面配置必须在最前
st.set_page_config(page_title="乐高报价", layout="wide")

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 环境变量
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 缺少环境变量")
    st.stop()

# ---------- Supabase 客户端 ----------
@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# ---------- 手动缓存 ----------
_CACHE = {}
def cached_get(key, func, ttl=120):
    now = time.time()
    if key in _CACHE:
        val, ts = _CACHE[key]
        if now - ts < ttl:
            return val
    val = func()
    _CACHE[key] = (val, now)
    return val

def clear_cache(pattern=None):
    global _CACHE
    if pattern is None:
        _CACHE.clear()
    else:
        for k in list(_CACHE.keys()):
            if pattern in k:
                del _CACHE[k]

# ---------- 数据获取函数 ----------
def fetch_all_records(table_name):
    supabase = get_supabase()
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

def get_clean_data():
    def _load():
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
    return cached_get("clean_data", _load, ttl=120)

def get_latest_history():
    def _load():
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
                    "time": row["时间"].isoformat() if row["時間"] else ""
                }
        return latest
    return cached_get("latest_history", _load, ttl=60)

def get_all_price_records_df():
    all_data = fetch_all_records("price_records")
    return pd.DataFrame(all_data) if all_data else pd.DataFrame()

def get_price_rules():
    def _load():
        supabase = get_supabase()
        res = supabase.table("price_rules").select("model, buy, sell").execute()
        rules = {}
        for r in res.data:
            rules[r["model"]] = {"buy": r["buy"], "sell": r["sell"]}
        return rules
    return cached_get("price_rules", _load, ttl=60)

def get_favorites():
    supabase = get_supabase()
    res = supabase.table("user_favorites").select("model").execute()
    return {item["model"] for item in res.data} if res.data else set()

def get_alert_threshold():
    supabase = get_supabase()
    res = supabase.table("settings").select("alert_threshold").limit(1).execute()
    return res.data[0]["alert_threshold"] if res.data else 10

# ---------- 业务操作 ----------
def save_batch_one_by_one(records):
    supabase = get_supabase()
    success = 0
    for rec in records:
        try:
            supabase.table("price_records").insert(rec).execute()
            success += 1
        except Exception as e:
            logger.error(f"保存失败: {e}")
    if success > 0:
        clear_cache("clean_data")
    return success

def toggle_favorite(model):
    supabase = get_supabase()
    favs = get_favorites()
    if model in favs:
        supabase.table("user_favorites").delete().eq("model", model).execute()
    else:
        supabase.table("user_favorites").insert({"model": model}).execute()

def save_price_rule(model, buy, sell):
    supabase = get_supabase()
    supabase.table("price_rules").upsert(
        {"model": model, "buy": buy, "sell": sell}, on_conflict="model"
    ).execute()
    clear_cache("price_rules")

def set_alert_threshold(v):
    supabase = get_supabase()
    supabase.table("settings").upsert({"id": 1, "alert_threshold": v}, on_conflict="id").execute()

def update_record(rec_id, data):
    supabase = get_supabase()
    try:
        supabase.table("price_records").update(data).eq("id", rec_id).execute()
        return True
    except:
        return False

def delete_record(rec_id):
    supabase = get_supabase()
    try:
        supabase.table("price_records").delete().eq("id", rec_id).execute()
        return True
    except:
        return False

# ---------- 解析辅助 ----------
def extract_remark(line):
    box_kw = ["好盒","压盒","瑕疵","盒损","烂盒","破盒","全新","微压"]
    bag_kw = ["纸袋","M袋","S袋","礼袋","礼品袋","M号袋","S袋","XL袋","L袋","大袋","小袋","有袋","无袋","袋子"]
    box = next((b for b in box_kw if b in line), None)
    bag = next((b for b in bag_kw if b in line), None)
    if box and bag: return f"{box}+{bag}"
    return box or bag or ""

def extract_by_regex(line):
    line = line.strip()
    if not line: return None, None, None
    remark = extract_remark(line)
    digits = re.findall(r'\d+', line)
    if len(digits) < 2: return None, None, None
    model_candidates = [d for d in digits if len(d)==5 and d[0]!='0']
    if not model_candidates: return None, None, None
    model = model_candidates[0]
    price_candidates = [int(p) for p in digits if p != model]
    valid = [p for p in price_candidates if 10 <= p <= 8000]
    price = max(valid) if valid else (max(price_candidates) if price_candidates else None)
    if price is None: return None, None, None
    return model, price, remark

# ---------- 会话状态 ----------
if "parse_result" not in st.session_state:
    st.session_state.parse_result = pd.DataFrame()
if "original_parse" not in st.session_state:
    st.session_state.original_parse = []
if "selected_model" not in st.session_state:
    st.session_state.selected_model = ""
if "parsing" not in st.session_state:
    st.session_state.parsing = False
if "filter_status" not in st.session_state:
    st.session_state.filter_status = "全部"

# ---------- UI ----------
st.title("🧩 乐高报价分析系统")

with st.expander("📝 批量录入", expanded=True):
    txt = st.text_area("粘贴内容", height=200)
    col1, col2, _ = st.columns([1,1,4])
    with col1:
        parse_clicked = st.button("🔍 解析", type="primary", disabled=st.session_state.parsing)
    with col2:
        if st.button("🧹 清空"):
            st.session_state.parse_result = pd.DataFrame()
            st.session_state.original_parse = []

    if parse_clicked:
        st.session_state.parsing = True
        try:
            if not txt.strip():
                st.warning("请输入内容")
            else:
                lines = txt.strip().splitlines()
                res = []
                for li in lines:
                    m, p, r = extract_by_regex(li)
                    if m and p:
                        res.append({"型号": m, "价格": p, "备注": r, "原始": li, "状态": "✅ 有效"})
                    else:
                        res.append({"型号": "", "价格": 0, "备注": "", "原始": li, "状态": "❌ 解析失败"})

                df_all = get_all_price_records_df()
                today_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
                today_set = set()
                for _, row in df_all.iterrows():
                    if row.get("time", "")[:10] == today_str:
                        today_set.add((row["model"], row["price"], str(row.get("remark","")).strip()))

                save_list = []
                for entry in res:
                    if entry["状态"] == "✅ 有效":
                        key = (entry["型号"], entry["价格"], entry["备注"])
                        if key in today_set:
                            entry["状态"] = "⏭️ 已跳过"
                        else:
                            save_list.append({
                                "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                                "model": entry["型号"],
                                "price": entry["价格"],
                                "remark": entry["备注"]
                            })
                            today_set.add(key)

                if save_list:
                    saved = save_batch_one_by_one(save_list)
                    st.success(f"✅ 保存 {saved} 条")
                else:
                    st.info("没有新数据")

                st.session_state.parse_result = pd.DataFrame(res)
                st.session_state.original_parse = res.copy()
        except Exception as e:
            st.error(f"解析出错: {e}")
        finally:
            st.session_state.parsing = False

# 结果表格
parse_df = st.session_state.parse_result
if not parse_df.empty:
    st.subheader("📋 解析结果")
    statuses = ["全部"] + sorted(parse_df["状态"].unique())
    selected = st.selectbox("筛选", statuses, key="filter_select")
    filtered = parse_df if selected == "全部" else parse_df[parse_df["状态"] == selected]

    if not filtered.empty:
        edited = st.data_editor(
            filtered[["型号","价格","备注","原始","状态"]],
            column_config={"型号": st.column_config.TextColumn(required=True), "价格": st.column_config.NumberColumn(required=True)},
            use_container_width=True, hide_index=True, num_rows="fixed"
        )

        total = len(parse_df)
        valid = len(parse_df[parse_df["状态"] == "✅ 有效"])
        st.markdown(f"总 {total} 条 | ✅有效 {valid}")

        if st.button("💾 保存修改", type="primary"):
            original = {i: row for i, row in enumerate(st.session_state.original_parse)}
            to_save = []
            for idx, (_, row) in enumerate(edited.iterrows()):
                orig = original.get(idx, {})
                if (row["型号"] != orig.get("型号","") or row["价格"] != orig.get("价格",0) or row["备注"] != orig.get("备注","")):
                    if row["型号"] and len(str(row["型号"]))==5 and row["价格"]>=10:
                        to_save.append({"model": row["型号"], "price": int(row["价格"]), "remark": row["备注"]})
            if to_save:
                today_str = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
                df_all = get_all_price_records_df()
                today_set = set()
                for _, r in df_all.iterrows():
                    if r.get("time","")[:10] == today_str:
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

# 系统设置
with st.expander("⚙️ 系统设置", expanded=False):
    th = get_alert_threshold()
    new_th = st.number_input("预警阈值", value=th, min_value=1)
    if new_th != th:
        set_alert_threshold(new_th)

# 历史管理
st.divider()
st.subheader("📋 历史数据管理")
df_all = get_clean_data()
if not df_all.empty:
    models = sorted(df_all["型号"].unique())
    target = st.selectbox("选择型号", [""] + models)
    if target:
        st.session_state.selected_model = target
        isfav = target in get_favorites()
        if st.button("⭐ 取消收藏" if isfav else "☆ 收藏"):
            toggle_favorite(target)

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
                clear_cache("clean_data")
                st.success("完成")

            fig = px.line(model_df.sort_values("时间"), x="时间", y="价格", markers=True)
            st.plotly_chart(fig, use_container_width=True)
