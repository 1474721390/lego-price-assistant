# ===========================================
# 🔒 安全启动配置（必须放在最顶部！）
# ===========================================
import os
import logging

# 🔥 核心修复：关闭 httpx 日志刷屏
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("supabase").setLevel(logging.WARNING)
logging.getLogger("streamlit").setLevel(logging.WARNING)

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
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from supabase import create_client
import time
from typing import Dict, List, Tuple, Optional
import functools

# ==================== 流安全装饰器 ====================
def streamlit_safe(func):
    """装饰器：确保 Streamlit 操作安全，防止会话状态错误"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            # 确保会话状态就绪
            if not hasattr(st, 'session_state') or st.session_state is None:
                # 尝试初始化
                st.session_state = {}
                # 设置基本默认值
                basic_defaults = {
                    "selected_model": "",
                    "scroll_to_bottom": False,
                    "parse_result": pd.DataFrame(),
                    "original_parse": [],
                    "pending_cache_clear": False,
                    "current_page_tab2": 1,
                    "parse_triggered": False,
                    "save_triggered": False,
                    "parsing_in_progress": False,
                    "saving_in_progress": False,
                    "quick_nav_model": "",
                    "show_favorites_bar": True,
                    "last_rerun_time": 0
                }
                for key, value in basic_defaults.items():
                    st.session_state[key] = value
            
            return func(*args, **kwargs)
        except Exception as e:
            st.error(f"操作失败: {str(e)}")
            return None
    return wrapper

# ==================== 会话状态管理器（增强版） ====================
class SessionStateManager:
    """统一的会话状态管理，避免竞态条件"""
    _initialized = False
    _state_ready = False
    
    @classmethod
    def ensure_initialized(cls):
        """确保会话状态完全初始化 - 修复版本"""
        if cls._initialized and cls._state_ready:
            return True
        
        # 最大重试次数
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                # 尝试安全地初始化会话状态
                if not hasattr(st, 'session_state') or st.session_state is None:
                    # 如果还没有会话状态，先设置一个空字典
                    st.session_state = {}
                
                # 设置默认值
                defaults = {
                    "selected_model": "",
                    "scroll_to_bottom": False,
                    "parse_result": pd.DataFrame(),
                    "original_parse": [],
                    "pending_cache_clear": False,
                    "current_page_tab2": 1,
                    "parse_triggered": False,
                    "save_triggered": False,
                    "parsing_in_progress": False,
                    "saving_in_progress": False,
                    "quick_nav_model": "",
                    "show_favorites_bar": True,
                    "last_rerun_time": 0,
                    # 添加调试和状态标记
                    "_session_initialized": True,
                    "_initialization_time": time.time()
                }
                
                # 安全地设置默认值
                for key, value in defaults.items():
                    if key not in st.session_state:
                        try:
                            st.session_state[key] = value
                        except (AttributeError, RuntimeError):
                            # 如果设置失败，稍后重试
                            continue
                
                cls._initialized = True
                cls._state_ready = True
                return True
                
            except (AttributeError, RuntimeError) as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    # 最后一次尝试也失败，记录但不崩溃
                    print(f"警告: 会话状态初始化失败: {e}")
                    return False
        
        return False
    
    @classmethod
    def safe_get(cls, key, default=None):
        """安全获取会话状态"""
        try:
            # 先尝试直接获取
            if hasattr(st, 'session_state') and st.session_state is not None:
                return st.session_state.get(key, default)
            
            # 如果不行，尝试初始化
            if cls.ensure_initialized():
                return st.session_state.get(key, default)
            
            return default
        except (AttributeError, RuntimeError, KeyError):
            return default
    
    @classmethod
    def safe_set(cls, key, value):
        """安全设置会话状态"""
        try:
            # 确保会话状态存在
            if not hasattr(st, 'session_state') or st.session_state is None:
                cls.ensure_initialized()
            
            # 设置值
            st.session_state[key] = value
            return True
        except (AttributeError, RuntimeError) as e:
            print(f"警告: 设置会话状态失败 {key}={value}: {e}")
            return False
    
    @classmethod
    def safe_update(cls, updates: dict):
        """批量安全更新会话状态"""
        try:
            for key, value in updates.items():
                cls.safe_set(key, value)
            return True
        except Exception as e:
            print(f"警告: 批量更新会话状态失败: {e}")
            return False
    
    @classmethod
    def safe_rerun(cls, reason="", force=False):
        """安全的页面刷新，避免无限循环"""
        try:
            if not force:
                last_rerun = cls.safe_get("last_rerun_time", 0)
                if time.time() - last_rerun < 1:
                    return False
            
            cls.safe_set("last_rerun_time", time.time())
            st.rerun()
            return True
        except Exception as e:
            print(f"警告: 刷新页面失败: {e}")
            return False

# ==================== 安全状态访问函数 ====================
@streamlit_safe
def safe_session_get(key, default=None):
    """安全的会话状态获取函数"""
    return SessionStateManager.safe_get(key, default)

@streamlit_safe
def safe_session_set(key, value):
    """安全的会话状态设置函数"""
    return SessionStateManager.safe_set(key, value)

# ==================== 环境配置 ====================
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY")

if not all([SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY]):
    st.error("❌ 缺少必要的环境变量配置")
    st.info("请配置以下环境变量：SUPABASE_URL, SUPABASE_KEY, ZHIPU_API_KEY")
    st.stop()

# 在设置页面配置前初始化会话状态管理器
try:
    SessionStateManager.ensure_initialized()
except:
    pass  # 即使失败也继续

st.set_page_config(
    page_title="🧩 乐高智能报价系统",
    page_icon="🧩",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ==================== 终极商用美化CSS ====================
st.markdown("""
<style>
    .stApp {
        background: linear-gradient(135deg, #f8f9ff 0%, #eef2fc 100%);
        background-attachment: fixed;
    }

    .stExpander, 
    .stTabs [role="tabpanel"], 
    [data-testid="stForm"],
    [data-testid="stDataEditor"],
    [data-testid="stMetric"] {
        background: #ffffff !important;
        border-radius: 16px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.06) !important;
        padding: 20px !important;
        margin-bottom: 16px !important;
        border: none !important;
    }

    .data-manager-card {
        background: #ffffff !important;
        border-radius: 16px !important;
        box-shadow: 0 4px 12px rgba(0,0,0,0.06) !important;
        padding: 24px !important;
        margin-top: 20px !important;
        margin-bottom: 20px !important;
    }

    .stTabs [role="tablist"] {
        background: #f1f5ff;
        border-radius: 14px;
        padding: 6px;
        gap: 6px;
    }
    .stTabs [aria-selected="true"] {
        background: #4a6cf7 !important;
        color: white !important;
        box-shadow: 0 3px 8px rgba(74,108,247,0.3);
    }

    /* 列表里的型号按钮：恢复原来的浅灰 */
    .scroll-box .stButton > button {
        background: #f7f8ff !important;
        color: #333 !important;
    }
    .scroll-box .stButton > button:hover {
        background: #eef1f8 !important;
    }
    .scroll-box button p {
        color: #111111 !important;
        font-weight: 500 !important;
    }

    /* 🔥 只有这三个主按钮 蓝底白字 */
    div[data-testid="stFormSubmitButton"] button,
    button[kind="primary"] {
        background: #2A5BD9 !important;
        color: #FFFFFF !important;
        font-weight: 600 !important;
        border-radius: 12px !important;
        box-shadow: 0 4px 10px rgba(42,91,217,0.25) !important;
    }

    div[data-testid="stFormSubmitButton"] button:hover,
    button[kind="primary"]:hover {
        background: #1E4AC2 !important;
        color: #FFFFFF !important;
        transform: translateY(-2px);
    }

    div[data-testid="stNumberInput"],
    div[data-testid="stTextInput"] {
        background: #ffffff !important;
        border: 1px solid #d4e0fd !important;
        border-radius: 12px !important;
        padding: 6px 12px !important;
    }

    .scroll-box {
        background: #ffffff !important;
        border-radius: 14px !important;
        padding: 16px !important;
        max-height: 480px !important;
        overflow-y: auto !important;
        border: 1px solid #e4eaf7 !important;
        margin-top: 10px !important;
    }

    h1, h2, h3, h4, h5, h6 {
        font-weight: 700 !important;
        color: #000000 !important;
    }

    [data-testid="stDivider"] {
        border-color: #e4eaf7;
    }

    html {
        scroll-behavior: smooth !important;
    }
</style>
""", unsafe_allow_html=True)

# ==================== 初始化会话状态 ====================
try:
    SessionStateManager.ensure_initialized()
except Exception as e:
    st.warning(f"会话状态初始化遇到问题: {e}，但应用将继续运行")

# ==================== Supabase客户端 ====================
@st.cache_resource
def get_supabase_client():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase_client()

# ==================== 收藏功能 ====================
@st.cache_data(ttl=60, show_spinner=False)
def get_favorites():
    res = supabase.table("user_favorites").select("model").execute()
    return {item["model"] for item in res.data} if res.data else set()

def toggle_favorite(model):
    favs = get_favorites()
    if model in favs:
        supabase.table("user_favorites").delete().eq("model", model).execute()
    else:
        supabase.table("user_favorites").insert({"model": model}).execute()
    get_favorites.clear()
    get_clean_data.clear()

# ==================== 心理价位 ====================
@st.cache_data(ttl=300, show_spinner=False)
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
    get_price_rules.clear()

# ==================== 阈值设置（保留函数定义，但不再在UI中使用） ====================
@st.cache_data(ttl=300, show_spinner=False)
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
                "time": row["时间"].isoformat() if row["时间"] else "",
                "last_time": row["时间"].strftime("%m-%d %H:%M") if row["时间"] else ""
            }
    return latest

# ==================== 批量计算趋势和涨跌 ====================
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
        if diff > 0:
            change = f"+¥{diff}"
        elif diff < 0:
            change = f"-¥{abs(diff)}"
        else:
            change = "±¥0"
        
        results[model] = {"trend": trend, "change": change}
    
    return results

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
    success_count = 0
    for record in records:
        try:
            supabase.table("price_records").insert(record).execute()
            success_count += 1
        except Exception as e:
            print(f"保存失败: {e}")
            continue
    if success_count > 0:
        safe_session_set("pending_cache_clear", True)
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

# ==================== 辅助函数 ====================
def render_grid_buttons(items, columns=3, prefix=""):
    if not items:
        return
    for i in range(0, len(items), columns):
        cols = st.columns(columns)
        for j, col in enumerate(cols):
            idx = i + j
            if idx < len(items):
                label, model = items[idx]
                key = f"{prefix}_{model}_{idx}"
                if col.button(label, key=key, use_container_width=True):
                    safe_session_set("selected_model", model)
                    safe_session_set("scroll_to_bottom", True)

def paginate(items, page_size, current_page):
    start_idx = (current_page - 1) * page_size
    end_idx = start_idx + page_size
    return items[start_idx:end_idx]

def smart_cache_clear():
    if safe_session_get("pending_cache_clear", False):
        try:
            get_clean_data.clear()
            get_latest_history.clear()
            get_favorites.clear()
            st.cache_data.clear()
            safe_session_set("pending_cache_clear", False)
        except RuntimeError:
            pass

# ==================== 走势图 ====================
def plot_enhanced_trend(model_data, model, rules):
    fig = go.Figure()
    
    fig.add_trace(go.Scatter(
        x=model_data["时间"],
        y=model_data["价格"],
        mode='lines+markers',
        name='历史价格',
        line=dict(color='#667eea', width=3),
        marker=dict(size=7, color='#764ba2'),
        hovertemplate='<b>%{x}</b><br>价格: ¥%{y}<br>备注: %{text}<extra></extra>',
        text=model_data["remark"]
    ))
    
    if model in rules:
        rule = rules[model]
        if rule["buy"] > 0:
            fig.add_hline(
                y=rule["buy"], 
                line_dash="dash", 
                line_color="green",
                annotation_text=f"💚 收货价: ¥{rule['buy']}",
                annotation_position="bottom right"
            )
        if rule["sell"] > 0:
            fig.add_hline(
                y=rule["sell"], 
                line_dash="dash", 
                line_color="red",
                annotation_text=f"❤️ 出货价: ¥{rule['sell']}",
                annotation_position="top right"
            )
    
    fig.update_layout(
        title={
            'text': f"📊 {model} 价格走势分析",
            'x': 0.5,
            'xanchor': 'center',
            'font': {'size': 20, 'color': '#000'}
        },
        xaxis_title="时间",
        yaxis_title="价格 (¥)",
        hovermode='x unified',
        template='plotly_white',
        height=460,
        margin=dict(l=40, r=40, t=60, b=40),
        plot_bgcolor='rgba(245,247,250,1)',
        paper_bgcolor='rgba(255,255,255,0.95)'
    )
    
    return fig

# ==================== 快速导航 ====================
def render_quick_navigation():
    favs = get_favorites()
    if not favs:
        return
    
    st.markdown("### ⭐ 快速导航 - 我的收藏")
    
    cols = st.columns(min(len(favs), 8))
    for i, model in enumerate(sorted(favs)[:8]):
        with cols[i]:
            latest = get_latest_history()
            price_text = f"¥{latest.get(model, {}).get('price', '?')}" if model in latest else "无数据"
            
            if st.button(
                f"**{model}**\n\n{price_text}",
                key=f"quick_nav_{model}",
                use_container_width=True,
                help=f"点击查看 {model} 详情"
            ):
                safe_session_set("selected_model", model)
                safe_session_set("scroll_to_bottom", True)
                SessionStateManager.safe_rerun()
    
    if len(favs) > 8:
        st.caption(f"... 还有 {len(favs)-8} 个收藏")

# ==================== 主界面 ====================
st.title("🧩 乐高智能报价分析系统")
st.markdown("---")

render_quick_navigation()

# ==================== 批量录入 ====================
with st.expander("📝 批量录入（点击展开）", expanded=True):
    with st.form("batch_input_form"):
        txt = st.text_area(
            "粘贴乐高报价内容（每行一条）",
            height=200,
            key="batch_input_text",
            placeholder="例如：\n10295 保时捷911 好盒 850\n42115 兰博基尼 压盒+纸袋 2300\n..."
        )
        
        col1, col2 = st.columns([1, 5])
        with col1:
            parse_submitted = st.form_submit_button(
                "🔍 一键解析报价",
                type="primary",
                use_container_width=True
            )
        with col2:
            st.caption("💡 系统会自动识别型号、价格、备注，并用AI修正可疑数据")
    
    parsing = safe_session_get("parsing_in_progress", False)
    
    if parse_submitted and not parsing:
        safe_session_set("parsing_in_progress", True)
        
        if not txt:
            st.warning("⚠️ 请输入要解析的内容")
            safe_session_set("parsing_in_progress", False)
        else:
            lines = txt.strip().splitlines()
            total_lines = len(lines)
            res = []
            temp_items = []
            
            progress_bar = st.progress(0, text="🚀 开始智能解析...")
            status_text = st.empty()
            
            for idx, li in enumerate(lines):
                progress = (idx + 1) / total_lines
                progress_bar.progress(progress, text=f"📊 正在解析第 {idx+1}/{total_lines} 行...")
                m, p, r = extract_by_regex(li)
                if not m or not p:
                    res.append({
                        "型号": "", "价格": 0, "备注": "", 
                        "原始": li, "状态": "❌ 解析失败"
                    })
                    continue
                temp_items.append({"model": m, "price": p, "remark": r.strip(), "raw": li})
            
            progress_bar.progress(1.0, text="✅ 解析完成，正在智能去重...")
            status_text.text("✨ 解析完成，正在智能去重和校验...")

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

            save_list = []
            total_unique = len(unique_batch)
            status_text.text(f"🔍 开始校验 {total_unique} 条唯一报价...")
            
            for idx, (key, item) in enumerate(unique_batch.items()):
                progress = (idx + 1) / total_unique if total_unique > 0 else 1
                progress_bar.progress(progress, text=f"🤖 AI智能校验第 {idx+1}/{total_unique} 条...")
                
                m = item["model"]
                p = item["price"]
                r = item["remark"]
                raw = item["raw"]
                
                if (m, p, r) in today_set:
                    res.append({
                        "型号": m, "价格": p, "备注": r, "原始": raw,
                        "状态": "⏭️ 已跳过（当天重复）"
                    })
                    continue
                
                use_ai = should_use_ai_fallback(m, p, raw)
                final_model, final_price, final_remark = m, p, r
                
                if use_ai:
                    ai_model, ai_price, ai_remark = extract_by_llm_full(raw)
                    if ai_model and ai_price:
                        final_model, final_price, final_remark = ai_model, ai_price, ai_remark
                        res.append({
                            "型号": final_model, "价格": final_price, "备注": final_remark,
                            "原始": raw, "状态": f"✅ 有效（AI修正）"
                        })
                    else:
                        res.append({
                            "型号": m, "价格": p, "备注": r, "原始": raw,
                            "状态": f"⚠️ 可疑（建议手动确认）"
                        })
                else:
                    res.append({
                        "型号": m, "价格": p, "备注": r, "原始": raw,
                        "状态": "✅ 有效"
                    })
                
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
            
            result_df = pd.DataFrame(res)
            safe_session_set("parse_result", result_df)
            safe_session_set("original_parse", res.copy())

            if save_list:
                with st.spinner(f"💾 正在保存 {len(save_list)} 条有效数据到数据库..."):
                    saved_count = save_batch_one_by_one(save_list)
                st.success(f"✅ 成功解析并自动保存 {saved_count} 条有效报价数据")
                st.balloons()
        
        safe_session_set("parsing_in_progress", False)

    if not safe_session_get("parse_result", pd.DataFrame()).empty:
        st.markdown("---")
        st.subheader("📋 解析结果（可编辑修正）")
        
        df_display = safe_session_get("parse_result", pd.DataFrame()).copy()
        
        valid_rows = df_display[(df_display["型号"] != "") & (df_display["价格"] > 0)]
        if not valid_rows.empty:
            model_price_pairs = list(zip(valid_rows["型号"].tolist(), valid_rows["价格"].tolist()))
            df_clean_for_calc = get_clean_data()
            batch_results = batch_calculate_trends_and_changes(df_clean_for_calc, model_price_pairs)
            
            df_display["趋势"] = "—"
            df_display["涨跌"] = "—"
            
            for idx, row in valid_rows.iterrows():
                model = row["型号"]
                if model in batch_results:
                    df_display.at[idx, "趋势"] = batch_results[model]["trend"]
                    df_display.at[idx, "涨跌"] = batch_results[model]["change"]
        else:
            df_display["趋势"] = "—"
            df_display["涨跌"] = "—"
        
        cols_order = ["型号", "价格", "趋势", "涨跌", "备注", "原始", "状态"]
        df_display = df_display[cols_order]

        edited_df = st.data_editor(
            df_display,
            column_config={
                "型号": st.column_config.TextColumn("型号", required=True, width="small"),
                "价格": st.column_config.NumberColumn("价格", required=True, min_value=0, width="small"),
                "趋势": st.column_config.TextColumn("趋势", disabled=True, width="small"),
                "涨跌": st.column_config.TextColumn("涨跌", disabled=True, width="small"),
                "备注": st.column_config.TextColumn("备注", width="medium"),
                "原始": st.column_config.TextColumn("原始文本", disabled=True, width="large"),
                "状态": st.column_config.TextColumn("状态", disabled=True, width="small"),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic"
        )

        total = len(df_display)
        valid = sum(1 for r in df_display.to_dict('records') if "✅ 有效" in r["状态"])
        ai_fixed = sum(1 for r in df_display.to_dict('records') if "AI修正" in r["状态"])
        manual = total - valid
        
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("📊 总解析", f"{total} 条")
        with col2:
            st.metric("✅ 有效数据", f"{valid} 条", delta=f"{ai_fixed} 条AI修正" if ai_fixed > 0 else None)
        with col3:
            st.metric("🤖 AI修正", f"{ai_fixed} 条")
        with col4:
            st.metric("✏️ 需手动", f"{manual} 条")

        saving = safe_session_get("saving_in_progress", False)
        if st.button("💾 保存并更新数据", type="primary", use_container_width=True, disabled=saving):
            safe_session_set("saving_in_progress", True)
            
            original_dict = {i: row for i, row in enumerate(safe_session_get("original_parse", []))}
            save_list_manual = []
            
            for idx, (_, edited_row) in enumerate(edited_df.iterrows()):
                original_row = original_dict.get(idx, {})
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
                
                save_list_manual.append({
                    "model": model,
                    "price": price,
                    "remark": str(edited_row["备注"]).strip()
                })

            if not save_list_manual:
                st.warning("⚠️ 没有检测到任何修改")
            else:
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
                    m, p, r = item["model"], item["price"], item["remark"]
                    if (m, p, r) not in today_set:
                        final_save.append(item)
                        today_set.add((m, p, r))

                if not final_save:
                    st.info("ℹ️ 所有修改后的数据今天已存在")
                else:
                    records_to_save = [{
                        "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                        "model": item["model"],
                        "price": int(item["price"]),
                        "remark": item["remark"]
                    } for item in final_save]
                    
                    saved_count = save_batch_one_by_one(records_to_save)
                    st.success(f"✅ 成功保存 {saved_count} 条修正数据")
                    safe_session_set("parse_result", pd.DataFrame())
                    safe_session_set("original_parse", [])
            
            safe_session_set("saving_in_progress", False)

# ==================== 侧边栏（价格预警 + 价格筛选，默认折叠） ====================
# 自定义按钮样式：绿底黑字加粗 + 自动换行 + 并排布局优化
st.markdown("""
<style>
    .stSidebar .stButton > button[kind="secondary"] {
        background-color: #2ecc71 !important;
        color: black !important;
        font-weight: bold !important;
        border: 1px solid #27ae60 !important;
        border-radius: 8px !important;
        transition: all 0.2s;
    }
    .stSidebar .stButton > button[kind="secondary"]:hover {
        background-color: #27ae60 !important;
        color: black !important;
        box-shadow: 0 2px 8px rgba(46,204,113,0.4);
    }
    /* 侧边栏全局字体缩小 */
    .stSidebar {
        font-size: 0.7rem !important;
    }
    /* 侧边栏内按钮文本自动换行，但保留数字单位不拆分（通过内部处理） */
    .stSidebar .stButton > button {
        white-space: normal !important;
        word-wrap: break-word !important;
        text-align: left !important;
        padding: 4px 6px !important;
        line-height: 1.2 !important;
        font-size: 0.7rem !important;
        height: auto !important;
        min-height: unset !important;
    }
    /* 并排布局内边距优化 */
    .stSidebar .stColumn {
        padding-left: 2px !important;
        padding-right: 2px !important;
    }
    .stSidebar .stMarkdown p {
        font-size: 0.7rem !important;
        margin-bottom: 0.1rem !important;
    }
    .stSidebar .stSelectbox label,
    .stSidebar .stNumberInput label {
        font-size: 0.65rem !important;
    }
    /* 紧凑化下拉框和输入框 */
    .stSidebar .stSelectbox > div,
    .stSidebar .stNumberInput > div {
        margin-bottom: 4px !important;
    }
</style>
""", unsafe_allow_html=True)

# 新增函数：计算当天波动明细
def get_today_fluctuation(model, df_clean):
    """计算指定型号当天价格波动明细和总差价"""
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    today_str = today.strftime("%Y-%m-%d")
    
    model_df = df_clean[df_clean["型号"] == model].sort_values("时间")
    if model_df.empty:
        return 0, []
    
    today_records = model_df[model_df["时间"].dt.strftime("%Y-%m-%d") == today_str]
    if len(today_records) < 2:
        return 0, []
    
    changes = []
    prices = today_records["价格"].tolist()
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff != 0:
            changes.append(diff)
    
    total_diff = prices[-1] - prices[0]
    return total_diff, changes

with st.sidebar:
    st.markdown("## 🎛️ 辅助工具")
    st.caption("点击下方展开对应功能，设置价格区间后点击“确定查询”")

    # ---------- 初始化侧边栏 session_state ----------
    if "sidebar_alert_up_page" not in st.session_state:
        st.session_state.sidebar_alert_up_page = 1
    if "sidebar_alert_up_page_size" not in st.session_state:
        st.session_state.sidebar_alert_up_page_size = 50
    if "sidebar_alert_down_page" not in st.session_state:
        st.session_state.sidebar_alert_down_page = 1
    if "sidebar_alert_down_page_size" not in st.session_state:
        st.session_state.sidebar_alert_down_page_size = 50
    if "sidebar_filter_page" not in st.session_state:
        st.session_state.sidebar_filter_page = 1
    if "sidebar_filter_page_size" not in st.session_state:
        st.session_state.sidebar_filter_page_size = 50
    if "sidebar_alert_result" not in st.session_state:
        st.session_state.sidebar_alert_result = None
    if "sidebar_filter_result" not in st.session_state:
        st.session_state.sidebar_filter_result = None

    # ---------- 价格预警折叠块 ----------
    with st.expander("🚨 价格预警", expanded=False):
        col_min, col_max = st.columns(2)
        with col_min:
            min_price_alert = st.number_input("最低价格", min_value=0, value=0, step=10, key="sidebar_min_price_alert")
        with col_max:
            max_price_alert = st.number_input("最高价格", min_value=0, value=100, step=10, key="sidebar_max_price_alert")

        query_alert_clicked = st.button("🔍 确定查询", key="alert_query_btn", type="secondary", use_container_width=True)

        if query_alert_clicked:
            alerts = get_alerts()
            df_clean = get_clean_data()
            filtered_alerts = []
            if alerts:
                for a in alerts:
                    current_price = a["last"]
                    if max_price_alert > 0:
                        if min_price_alert <= current_price <= max_price_alert:
                            filtered_alerts.append(a)
                    else:
                        if current_price >= min_price_alert:
                            filtered_alerts.append(a)
                
                for a in filtered_alerts:
                    total_diff, changes = get_today_fluctuation(a["model"], df_clean)
                    a["today_total_diff"] = total_diff
                    a["today_changes"] = changes
                
                up_list = [a for a in filtered_alerts if a["today_total_diff"] > 0]
                down_list = [a for a in filtered_alerts if a["today_total_diff"] < 0]
                
                up_list.sort(key=lambda x: -x["today_total_diff"])
                down_list.sort(key=lambda x: x["today_total_diff"])
                st.session_state.sidebar_alert_result = {"up": up_list, "down": down_list}
            else:
                st.session_state.sidebar_alert_result = {"up": [], "down": []}
            st.session_state.sidebar_alert_up_page = 1
            st.session_state.sidebar_alert_down_page = 1

        alert_result = st.session_state.sidebar_alert_result
        if alert_result is not None:
            up_list = alert_result["up"]
            down_list = alert_result["down"]

            col_left, col_right = st.columns(2)

            with col_left:
                st.markdown("##### 📈 涨价")
                page_size_up = st.selectbox("每页", options=[10, 20, 50], 
                                            index=[10,20,50].index(st.session_state.sidebar_alert_up_page_size),
                                            key="alert_up_page_size_select")
                st.session_state.sidebar_alert_up_page_size = page_size_up

                if up_list:
                    total_up = len(up_list)
                    current_page = st.session_state.sidebar_alert_up_page
                    total_pages = max(1, (total_up + page_size_up - 1) // page_size_up)

                    start_idx = (current_page - 1) * page_size_up
                    end_idx = min(start_idx + page_size_up, total_up)
                    page_items = up_list[start_idx:end_idx]

                    for a in page_items:
                        star = "⭐" if a["is_fav"] else ""
                        changes_str = ",".join([f"+{c}" if c>0 else str(c) for c in a["today_changes"]])
                        # 防止换行拆分数字单位：将空格替换为 &nbsp;
                        content = f"{star} {a['model']}\n现¥{a['last']} | +{a['today_total_diff']}元\n当天:{changes_str}".replace(" ", "\u00A0")
                        if st.button(content, key=f"alert_up_{a['model']}_{current_page}"):
                            safe_session_set("selected_model", a["model"])
                            safe_session_set("scroll_to_bottom", True)
                            st.rerun()

                    if total_pages > 1:
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            if st.button("◀", key="alert_up_prev", disabled=(current_page==1), use_container_width=True):
                                st.session_state.sidebar_alert_up_page = max(1, current_page-1)
                                st.rerun()
                        with c2:
                            st.markdown(f"<div style='text-align:center;font-size:0.7rem;'>{current_page}/{total_pages}</div>", unsafe_allow_html=True)
                        with c3:
                            if st.button("▶", key="alert_up_next", disabled=(current_page==total_pages), use_container_width=True):
                                st.session_state.sidebar_alert_up_page = min(total_pages, current_page+1)
                                st.rerun()
                    else:
                        st.caption(f"共 {total_up} 条")
                else:
                    st.caption("无")

            with col_right:
                st.markdown("##### 📉 跌价")
                page_size_down = st.selectbox("每页", options=[10, 20, 50],
                                              index=[10,20,50].index(st.session_state.sidebar_alert_down_page_size),
                                              key="alert_down_page_size_select")
                st.session_state.sidebar_alert_down_page_size = page_size_down

                if down_list:
                    total_down = len(down_list)
                    current_page = st.session_state.sidebar_alert_down_page
                    total_pages = max(1, (total_down + page_size_down - 1) // page_size_down)

                    start_idx = (current_page - 1) * page_size_down
                    end_idx = min(start_idx + page_size_down, total_down)
                    page_items = down_list[start_idx:end_idx]

                    for a in page_items:
                        star = "⭐" if a["is_fav"] else ""
                        changes_str = ",".join([f"+{c}" if c>0 else str(c) for c in a["today_changes"]])
                        content = f"{star} {a['model']}\n现¥{a['last']} | {a['today_total_diff']}元\n当天:{changes_str}".replace(" ", "\u00A0")
                        if st.button(content, key=f"alert_down_{a['model']}_{current_page}"):
                            safe_session_set("selected_model", a["model"])
                            safe_session_set("scroll_to_bottom", True)
                            st.rerun()

                    if total_pages > 1:
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            if st.button("◀", key="alert_down_prev", disabled=(current_page==1), use_container_width=True):
                                st.session_state.sidebar_alert_down_page = max(1, current_page-1)
                                st.rerun()
                        with c2:
                            st.markdown(f"<div style='text-align:center;font-size:0.7rem;'>{current_page}/{total_pages}</div>", unsafe_allow_html=True)
                        with c3:
                            if st.button("▶", key="alert_down_next", disabled=(current_page==total_pages), use_container_width=True):
                                st.session_state.sidebar_alert_down_page = min(total_pages, current_page+1)
                                st.rerun()
                    else:
                        st.caption(f"共 {total_down} 条")
                else:
                    st.caption("无")
        else:
            st.caption("设置价格区间后点击“确定查询”查看预警")

    # ---------- 价格筛选折叠块（左右并排显示） ----------
    with st.expander("🔍 价格筛选", expanded=False):
        col_min2, col_max2 = st.columns(2)
        with col_min2:
            min_price = st.number_input("最低价格", min_value=0, value=0, step=10, key="sidebar_min_price_filter")
        with col_max2:
            max_price = st.number_input("最高价格", min_value=0, value=100, step=10, key="sidebar_max_price_filter")

        page_size_filter = st.selectbox("每页显示", options=[10, 20, 50], index=2, key="filter_page_size_select")

        query_filter_clicked = st.button("🔍 确定查询", key="filter_query_btn", type="secondary", use_container_width=True)

        if query_filter_clicked:
            if min_price >= max_price and max_price > 0:
                st.warning("⚠️ 最高价格应大于最低价格")
                st.session_state.sidebar_filter_result = None
            else:
                df_clean = get_clean_data()
                if not df_clean.empty:
                    latest_df = df_clean.sort_values('时间').groupby('型号').tail(1)
                    if max_price > 0:
                        filtered_df = latest_df[(latest_df['价格'] >= min_price) & (latest_df['价格'] <= max_price)]
                    else:
                        filtered_df = latest_df[latest_df['价格'] >= min_price]
                    filtered_df = filtered_df.sort_values('价格', ascending=False)
                    items = []
                    latest_info = get_latest_history()
                    for _, row in filtered_df.iterrows():
                        model = row['型号']
                        price = row['价格']
                        remark = row.get('remark', '')
                        last_time = latest_info.get(model, {}).get('last_time', '无时间')
                        count = len(df_clean[df_clean['型号'] == model])
                        remark_str = f" | {remark}" if remark else ""
                        items.append({
                            "model": model,
                            "price": price,
                            "remark_str": remark_str,
                            "count": count,
                            "last_time": last_time
                        })
                    st.session_state.sidebar_filter_result = items
                else:
                    st.session_state.sidebar_filter_result = []
            st.session_state.sidebar_filter_page = 1
            st.session_state.sidebar_filter_page_size = page_size_filter

        filter_result = st.session_state.sidebar_filter_result
        if filter_result is not None:
            if not filter_result:
                st.info("🔍 未找到符合条件的数据")
            else:
                total_items = len(filter_result)
                page_size = st.session_state.sidebar_filter_page_size
                current_page = st.session_state.sidebar_filter_page
                total_pages = max(1, (total_items + page_size - 1) // page_size)

                start_idx = (current_page - 1) * page_size
                end_idx = min(start_idx + page_size, total_items)
                page_items = filter_result[start_idx:end_idx]

                st.markdown(f"**共 {total_items} 个型号**")

                # 将当前页项目分为左右两栏（左栏比右栏多一个，如果总数奇数）
                mid = (len(page_items) + 1) // 2
                left_items = page_items[:mid]
                right_items = page_items[mid:]

                col_left2, col_right2 = st.columns(2)
                with col_left2:
                    for item in left_items:
                        # 防止数字+单位被拆分，用 &nbsp; 替换空格
                        btn_label = f"{item['model']} ¥{item['price']}{item['remark_str']} | {item['count']}条".replace(" ", "\u00A0")
                        if st.button(btn_label, key=f"sidebar_filter_{item['model']}_{current_page}"):
                            safe_session_set("selected_model", item['model'])
                            safe_session_set("scroll_to_bottom", True)
                            st.rerun()
                with col_right2:
                    for item in right_items:
                        btn_label = f"{item['model']} ¥{item['price']}{item['remark_str']} | {item['count']}条".replace(" ", "\u00A0")
                        if st.button(btn_label, key=f"sidebar_filter_{item['model']}_{current_page}_r"):
                            safe_session_set("selected_model", item['model'])
                            safe_session_set("scroll_to_bottom", True)
                            st.rerun()

                if total_pages > 1:
                    cols = st.columns(3)
                    with cols[0]:
                        if st.button("◀ 上一页", key="filter_prev", disabled=(current_page == 1), use_container_width=True):
                            st.session_state.sidebar_filter_page = max(1, current_page - 1)
                            st.rerun()
                    with cols[1]:
                        st.markdown(f"<div style='text-align: center;'>{current_page}/{total_pages}</div>", unsafe_allow_html=True)
                    with cols[2]:
                        if st.button("下一页 ▶", key="filter_next", disabled=(current_page == total_pages), use_container_width=True):
                            st.session_state.sidebar_filter_page = min(total_pages, current_page + 1)
                            st.rerun()
        else:
            st.caption("设置价格区间后点击“确定查询”查看型号")

# ✅ 恢复 df 和 all_models 的定义（供后续历史数据管理使用）
df = get_clean_data()
all_models = sorted(df["型号"].unique()) if not df.empty else []

# ==================== 历史数据详细管理 ====================
st.markdown('<div class="data-manager-card">', unsafe_allow_html=True)
st.subheader("📋 历史数据详细管理")

# 自定义 CSS：固定表格容器高度，强制显示垂直滚动条，防止闪烁
st.markdown("""
<style>
    .fixed-table-container {
        max-height: 400px;
        overflow-y: scroll !important;
        border: 1px solid #e4eaf7;
        border-radius: 12px;
        margin-bottom: 16px;
    }
    .fixed-table-container::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    .fixed-table-container::-webkit-scrollbar-thumb {
        background: #c1c9d2;
        border-radius: 4px;
    }
</style>
""", unsafe_allow_html=True)

if not df.empty:
    col1, col2 = st.columns([4, 1])
    with col1:
        idx = 0
        selected_model = safe_session_get("selected_model", "")
        if selected_model in all_models:
            idx = all_models.index(selected_model) + 1
        
        target = st.selectbox(
            "🔍 选择或搜索型号",
            [""] + all_models,
            index=idx,
            help="输入型号数字可快速搜索"
        )
    
    if target:
        safe_session_set("selected_model", target)
        
        model_data = df[df["型号"] == target].sort_values("时间", ascending=False)
        if not model_data.empty:
            cur = model_data.iloc[0]["价格"]
            highest = model_data["价格"].max()
            lowest = model_data["价格"].min()
            record_count = len(model_data)
            
            # 四个并排指标卡片
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("当前价格", f"¥{cur}")
            with col2:
                st.metric("历史最高", f"¥{highest}")
            with col3:
                st.metric("历史最低", f"¥{lowest}")
            with col4:
                st.metric("记录条数", record_count)
            
            # ========== 历史数据编辑表格 ==========
            st.markdown("---")
            st.markdown("#### 📝 历史数据编辑")
            
            def format_date(t_str):
                if t_str and len(t_str) >= 10:
                    return t_str[:10]
                return t_str

            # 准备显示数据（列顺序：删除、型号、价格、备注、日期、id、原始时间）
            show = model_data[["id", "原始时间", "型号", "价格", "remark"]].copy()
            show["日期"] = show["原始时间"].apply(format_date)
            show.rename(columns={"remark": "备注"}, inplace=True)
            show.insert(0, "删除", False)

            # 调整列顺序以匹配显示需求
            show = show[["删除", "型号", "价格", "备注", "日期", "id", "原始时间"]]

            with st.container():
                st.markdown('<div class="fixed-table-container">', unsafe_allow_html=True)
                edited_display = st.data_editor(
                    show,
                    column_config={
                        "删除": st.column_config.CheckboxColumn("删除", width="small"),
                        "型号": st.column_config.TextColumn("型号", width="small"),
                        "价格": st.column_config.NumberColumn("价格", width="small", format="%d"),
                        "备注": st.column_config.TextColumn("备注", width="medium"),
                        "日期": st.column_config.TextColumn("日期", disabled=True, width="small"),
                        "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                        "原始时间": st.column_config.TextColumn("原始时间", disabled=True, width="medium"),
                    },
                    column_order=["删除", "型号", "价格", "备注", "日期", "id", "原始时间"],
                    use_container_width=True,
                    hide_index=True,
                    key=f"editor_{target}"
                )
                st.markdown('</div>', unsafe_allow_html=True)

            if st.button("💾 保存修改 & 删除选中", type="primary", key=f"save_{target}"):
                # 删除操作：根据 id 列删除
                del_mask = edited_display["删除"] == True
                del_ids = edited_display.loc[del_mask, "id"].tolist()
                for did in del_ids:
                    delete_record(did)
                
                # 更新操作：仅处理未被删除的行
                update_mask = ~del_mask
                for _, row in edited_display[update_mask].iterrows():
                    update_record(row["id"], {
                        "model": str(row["型号"]).strip(),
                        "price": int(row["价格"]),
                        "remark": str(row["备注"]).strip()
                    })
                
                st.success("✅ 修改已保存")
                get_clean_data.clear()
                SessionStateManager.safe_rerun()

            # ========== 价格走势分析 ==========
            st.markdown("---")
            st.subheader(f"📈 {target} 价格走势分析")
            rules = get_price_rules()
            fig = plot_enhanced_trend(model_data.sort_values("时间"), target, rules)
            st.plotly_chart(fig, use_container_width=True)
            
        else:
            st.info("该型号暂无数据")
    else:
        st.info("👆 请在上方选择一个型号查看详情")
else:
    st.info("📭 暂无历史数据，请先在批量录入中添加数据")
st.markdown('</div>', unsafe_allow_html=True)

# ==================== 自动滚动 ====================
if safe_session_get("scroll_to_bottom", False):
    auto_scroll = """
    <script>
        window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'});
    </script>
    """
    st.components.v1.html(auto_scroll, height=0)
    safe_session_set("scroll_to_bottom", False)

# ==================== 清理缓存 ====================
smart_cache_clear()

# ==================== 页脚 ====================
st.divider()
st.caption("🧩 乐高智能报价系统 ")
