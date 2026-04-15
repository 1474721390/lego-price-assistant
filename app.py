# ===========================================
# 乐高报价系统 - 极简稳定版 (无自动刷新)
# ===========================================
import os
import re
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="乐高报价", layout="wide")

# 环境变量
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not all([SUPABASE_URL, SUPABASE_KEY]):
    st.error("❌ 缺少环境变量")
    st.stop()

@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

# ---------- 数据函数 ----------
def fetch_price_records():
    res = supabase.table("price_records").select("*").order('time', desc=False).execute()
    return pd.DataFrame(res.data) if res.data else pd.DataFrame()

def save_record(model, price, remark):
    data = {
        "time": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "model": model,
        "price": int(price),
        "remark": remark
    }
    supabase.table("price_records").insert(data).execute()
    # 清除缓存，下次加载时刷新
    st.cache_data.clear()

# ---------- 解析函数 ----------
def extract_by_regex(line):
    line = line.strip()
    if not line:
        return None, None, None
    digits = re.findall(r'\d+', line)
    if len(digits) < 2:
        return None, None, None
    model_candidates = [d for d in digits if len(d) == 5 and d[0] != '0']
    if not model_candidates:
        return None, None, None
    model = model_candidates[0]
    price_candidates = [int(p) for p in digits if p != model]
    valid = [p for p in price_candidates if 10 <= p <= 8000]
    price = max(valid) if valid else (max(price_candidates) if price_candidates else None)
    if price is None:
        return None, None, None
    remark = ""
    return model, price, remark

# ---------- UI ----------
st.title("🧩 乐高报价录入")

with st.expander("📝 批量录入", expanded=True):
    txt = st.text_area("粘贴内容", height=200, placeholder="3420收顺丰10307铁塔 湖北\n默认好盒，微压滴滴")
    if st.button("🔍 解析并保存", type="primary"):
        if not txt.strip():
            st.warning("请输入内容")
        else:
            lines = txt.strip().splitlines()
            success = 0
            today = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
            df_existing = fetch_price_records()
            existing_today = set()
            for _, row in df_existing.iterrows():
                if row.get("time", "")[:10] == today:
                    existing_today.add((row["model"], row["price"], str(row.get("remark", "")).strip()))
            
            for line in lines:
                m, p, r = extract_by_regex(line)
                if m and p:
                    key = (m, p, r)
                    if key not in existing_today:
                        save_record(m, p, r)
                        existing_today.add(key)
                        success += 1
            if success:
                st.success(f"✅ 成功保存 {success} 条记录")
            else:
                st.info("没有新数据需要保存")

# 显示最近记录
st.subheader("📋 最近录入记录")
df = fetch_price_records()
if not df.empty:
    df_display = df.sort_values("time", ascending=False).head(20)[["time", "model", "price", "remark"]]
    df_display["time"] = pd.to_datetime(df_display["time"]).dt.strftime("%Y-%m-%d %H:%M")
    st.dataframe(df_display, use_container_width=True, hide_index=True)
else:
    st.info("暂无数据")
