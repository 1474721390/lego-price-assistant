# ===========================================
# 🔒 安全启动配置（必须放在最顶部！）
# ===========================================
import os
import logging

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("supabase").setLevel(logging.WARNING)
logging.getLogger("streamlit").setLevel(logging.WARNING)

os.environ["STREAMLIT_SERVER_RUNONSAVE"] = "false"
os.environ["STREAMLIT_SERVER_FOLDERWATCHBLACKLIST"] = ".*"
os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"

# ===========================================
# 标准导入
# ===========================================
import re
import json
import requests
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from supabase import create_client
import time
from typing import Dict, List, Tuple, Optional

# ==================== 会话状态管理器 ====================
class SessionStateManager:
    _initialized = False
    
    @classmethod
    def ensure_initialized(cls):
        if cls._initialized:
            return True
        
        max_wait = 0.5
        start_time = time.time()
        
        while not hasattr(st, 'session_state'):
            if time.time() - start_time > max_wait:
                return False
            time.sleep(0.01)
        
        defaults = {
            "selected_model": "",
            "scroll_to_bottom": False,
            "parse_result": pd.DataFrame(),
            "original_parse": [],
            "pending_cache_clear": False,
            "current_page_tab4": 1,
            "parsing_in_progress": False,
            "saving_in_progress": False,
            "last_rerun_time": 0
        }
        
        for k, v in defaults.items():
            if k not in st.session_state:
                st.session_state[k] = v
        
        cls._initialized = True
        return True
    
    @classmethod
    def safe_get(cls, key, default=None):
        if not cls.ensure_initialized():
            return default
        return st.session_state.get(key, default)
    
    @classmethod
    def safe_set(cls, key, value):
        if cls.ensure_initialized():
            st.session_state[key] = value

    @classmethod
    def safe_rerun(cls, force=False):
        if not force:
            if time.time() - st.session_state.get("last_rerun_time", 0) < 1:
                return
        st.session_state["last_rerun_time"] = time.time()
        st.rerun()

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 缺少环境变量")
    st.stop()

st.set_page_config(
    page_title="🧩 乐高智能报价系统",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==================== 🌸 最终完美样式 ====================
st.markdown("""
<style>
    /* 全局背景 */
    .stApp {
        background: linear-gradient(135deg, #f8f9ff 0%, #eef2fc 100%) !important;
        background-attachment: fixed !important;
    }

    /* 卡片容器 */
    .stExp, .stTabs [role="tabpanel"], [data-testid="stForm"] {
        background: #FFFFFF !important;
        border-radius: 16px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.06) !important;
        padding: 20px !important;
        margin-bottom: 16px !important;
        border: none !important;
    }

    /* 独立滚动区域 */
    .scroll-box {
        background: #ffffff !important;
        border-radius: 14px !important;
        padding: 14px !important;
        max-height: 480px !important;
        overflow-y: auto !important;
        border: 1px solid #e6efff !important;
    }

    /* 按钮恢复原来的干净样式（不蓝）*/
    div.stButton > button {
        background: #f7f8fc !important;
        color: #222 !important;
        border-radius: 10px !important;
        font-weight: 500 !important;
        border: 1px solid #e2e8f0 !important;
    }
    div.stButton > button:hover {
        background: #eef1f8 !important;
        border-color: #d0d8e3 !important;
    }

    /* 主按钮（一键解析、保存）保持绿色突出 */
    button[kind="primary"] {
        background: #28C76F !important;
        color: white !important;
        font-weight: bold !important;
        border: none !important;
    }
    button[kind="primary"]:hover {
        background: #22b864 !important;
    }

    html {
        scroll-behavior: smooth !important;
    }
</style>
""", unsafe_allow_html=True)

SessionStateManager.ensure_initialized()

# ==================== 数据库 ====================
@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)
supabase = get_supabase()

# ==================== 收藏 ====================
@st.cache_data(ttl=60)
def get_favs():
    r = supabase.table("user_favorites").select("model").execute()
    return {i["model"] for i in r.data} if r.data else set()

def toggle_fav(model):
    f = get_favs()
    if model in f:
        supabase.table("user_favorites").delete().eq("model", model).execute()
    else:
        supabase.table("user_favorites").insert({"model": model}).execute()
    get_favs.clear()
    get_clean.clear()

# ==================== 数据 ====================
@st.cache_data(ttl=120)
def get_clean():
    d = supabase.table("price_records").select("*").execute()
    if not d.data:
        return pd.DataFrame()
    df = pd.DataFrame(d.data)
    df["型号"] = df["model"].str.strip()
    df["价格"] = pd.to_numeric(df["price"], errors="coerce")
    df = df[df["型号"].str.match(r'^\d{5}$', na=False)]
    df = df.dropna(subset=["价格"])
    return df

# ==================== 解析 ====================
def ext(line):
    box = ["好盒","压盒","瑕疵","盒损","全新","微压"]
    bag = ["纸袋","M袋","S袋","无袋"]
    b = next((x for x in box if x in line), None)
    g = next((x for x in bag if x in line), None)
    rem = f"{b}+{g}" if b and g else (b or g)
    dig = re.findall(r'\d+', line)
    if len(dig)<2:
        return None,None,rem
    m = next((x for x in dig if len(x)==5), None)
    p = max([int(x) for x in dig if x!=m]) if m else None
    return m,p,rem

# ==================== 界面 ====================
st.title("🧩 乐高智能报价分析系统")
st.divider()

# ==================== 批量录入 ====================
with st.expander("📝 批量录入", expanded=True):
    with st.form("f"):
        txt = st.text_area("粘贴报价", height=200, placeholder="10295 好盒 850")
        c1,c2 = st.columns([1,5])
        with c1:
            go = st.form_submit_button("🔍 一键解析报价", type="primary")
        with c2:
            st.caption("自动识别型号｜价格｜盒况")
    
    if go and not SessionStateManager.safe_get("parsing_in_progress"):
        SessionStateManager.safe_set("parsing_in_progress", True)
        lines = txt.strip().splitlines()
        res = []
        for li in lines:
            m,p,r = ext(li)
            if m and p:
                res.append({"型号":m,"价格":p,"备注":r,"原始":li,"状态":"✅ 有效"})
            else:
                res.append({"型号":"","价格":0,"备注":"","原始":li,"状态":"❌ 失败"})
        df = pd.DataFrame(res)
        st.session_state["parse_result"] = df
        st.success(f"✅ 解析完成")
        st.balloons()
        SessionStateManager.safe_set("parsing_in_progress", False)

    if "parse_result" in st.session_state and not st.session_state["parse_result"].empty:
        st.subheader("解析结果")
        st.dataframe(st.session_state["parse_result"], use_container_width=True)
        if st.button("💾 保存全部有效数据", type="primary"):
            df = st.session_state["parse_result"]
            ok = df[df["状态"]=="✅ 有效"]
            for _,r in ok.iterrows():
                supabase.table("price_records").insert({
                    "model":r["型号"],"price":r["价格"],"remark":r["备注"]
                }).execute()
            st.success("✅ 保存成功")
            get_clean.clear()
            st.session_state["parse_result"] = pd.DataFrame()

# ==================== 标签 ====================
t1,t2,t3,t4 = st.tabs(["⭐ 我的收藏","📈 涨跌排行","🚨 价格预警","🔍 价格筛选"])
df = get_clean()

# -------------------- 收藏 --------------------
with t1:
    st.subheader("我的收藏")
    favs = get_favs()
    if favs:
        # 独立滚动框
        st.markdown('<div class="scroll-box">', unsafe_allow_html=True)
        for m in sorted(favs):
            st.button(f"⭐ {m}", key=f"fav_{m}", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("暂无收藏")

# -------------------- 涨跌排行 --------------------
with t2:
    st.subheader("价格波动排行")
    if not df.empty:
        # 独立滚动框
        st.markdown('<div class="scroll-box">', unsafe_allow_html=True)
        last = df.sort_values("created_at").groupby("型号").last()
        last["波动"] = last["price"].pct_change()
        top = last.sort_values("波动", ascending=False).head(15)
        for idx,row in top.iterrows():
            st.button(f"📊 {idx} ｜ 现价 ¥{int(row['price'])}", key=f"t_{idx}", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("暂无数据")

# -------------------- 价格预警 --------------------
with t3:
    st.subheader("价格预警")
    c1,c2 = st.columns(2)
    with c1:
        minp = st.number_input("最低价格", 0, 99999, 0)
    with c2:
        maxp = st.number_input("最高价格", 0, 99999, 9999)
    
    if not df.empty:
        last = df.sort_values("created_at").groupby("型号").last()
        fil = last[(last["price"]>=minp) & (last["price"]<=maxp)]
        # 独立滚动框
        st.markdown('<div class="scroll-box">', unsafe_allow_html=True)
        for idx,row in fil.iterrows():
            st.button(f"🚨 {idx} ｜ ¥{int(row['price'])}", key=f"w_{idx}", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("暂无数据")

# -------------------- 价格筛选 --------------------
with t4:
    st.subheader("价格筛选")
    c1,c2 = st.columns(2)
    with c1:
        mi = st.number_input("最低价", 0, 99999, 0, key="smi")
    with c2:
        ma = st.number_input("最高价", 0, 99999, 9999, key="sma")
    
    if not df.empty:
        last = df.sort_values("created_at").groupby("型号").last()
        res = last[(last["price"]>=mi) & (last["price"]<=ma)]
        # 独立滚动框
        st.markdown('<div class="scroll-box">', unsafe_allow_html=True)
        for idx,row in res.iterrows():
            st.button(f"🔍 {idx} ｜ ¥{int(row['price'])}", key=f"s_{idx}", use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        st.info("暂无数据")

# ==================== 历史管理 ====================
st.divider()
st.subheader("📋 历史数据管理")

if not df.empty:
    mods = [""] + sorted(df["型号"].unique())
    tar = st.selectbox("选择型号", mods)
    if tar:
        c1,c2 = st.columns(2)
        with c1:
            if st.button("⭐ 收藏/取消" if tar in get_favs() else "⭐ 添加收藏"):
                toggle_fav(tar)
                st.rerun()
        with c2:
            st.button("📈 查看走势")
        
        data = df[df["型号"]==tar].sort_values("created_at", ascending=False)
        st.dataframe(data[["型号","price","remark","created_at"]], use_container_width=True)
        
        # 走势图
        st.subheader("走势")
        fig = px.line(data, x="created_at", y="price", title=f"{tar} 价格走势")
        st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("🧩 乐高智能报价系统 | 完美最终版")
