import streamlit as st
import requests
import os
from pathlib import Path
import json
import time
import logging
import datetime
from requests.exceptions import RequestException, Timeout, ConnectionError, TooManyRedirects, SSLError
from typing import Optional

# ===================== 日志配置 =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===================== 页面配置（必须在最前面）=====================
st.set_page_config(
    page_title="小航 · 郑州航院校园信息助手",
    page_icon="✈️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ===================== 隐藏Deploy按钮和Streamlit默认元素 =====================
hide_elements = """
<style>
    .stDeployButton, 
    button[title="Deploy"], 
    [data-testid="stDeployButton"],
    div[data-testid="stToolbar"] {
        display: none !important;
    }
    #MainMenu, footer {
        visibility: hidden !important;
    }
    .viewerBadge_container__1QSob,
    .viewerBadge_link__1S137,
    .viewerBadge_text__1JaDK {
        display: none !important;
    }
    .main .block-container {
        padding-top: 2rem;
        padding-bottom: 2rem;
    }
    .stTabs [data-baseweb="tab"] {
        font-size: 16px;
        font-weight: 500;
    }
</style>
"""
st.markdown(hide_elements, unsafe_allow_html=True)


# ===================== 配置区 =====================
def get_api_key() -> str:
    api_key = os.getenv("SILICONFLOW_API_KEY")
    if api_key:
        return api_key
    try:
        api_key = st.secrets["SILICONFLOW_API_KEY"]
        return api_key
    except Exception:
        pass
    try:
        secrets_paths = [
            Path(__file__).parent / ".streamlit" / "secrets.toml",
            Path(__file__).parent.parent / ".streamlit" / "secrets.toml",
        ]
        for path in secrets_paths:
            if path.exists():
                import toml
                secrets = toml.load(path)
                if "SILICONFLOW_API_KEY" in secrets:
                    return secrets["SILICONFLOW_API_KEY"]
    except Exception:
        pass
    return "sk-dtvzvroekzbfhpgqhqaptvubbpxmyfrzqssmwnwhfnmceclm"


API_KEY = get_api_key()
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
MODEL_NAME = "deepseek-ai/DeepSeek-V3"
MAX_RETRIES = 2
RETRY_DELAY = 2.0
REQUEST_TIMEOUT = (10, 45)

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ===================== 导入prompts模块 =====================
try:
    from prompts import load_school_info, get_system_prompt
except ImportError:
    st.error("❌ 缺少prompts.py文件")
    st.stop()

# ===================== 常量定义 =====================
ROLES = ["新生", "在校生", "教师"]

TAB_QUESTIONS = {
    "新生指南": [
        "报到那天先去哪？", "学费什么时候交？",
        "宿舍是4人间还是6人间？", "军训需要准备什么？",
        "校园卡怎么办理？", "怎么选课？"
    ],
    "办事流程": [
        "怎么开在读证明？", "校园卡丢了怎么补？",
        "转专业怎么转？", "图书馆几点关门？",
        "怎么借教室？", "成绩单怎么打印？"
    ],
    "应急防骗": [
        "有人冒充辅导员要钱怎么办？", "校园报警电话是多少？",
        "校医院急诊电话？", "遇到诈骗怎么处理？",
        "心理压力大找谁？", "宿舍东西被盗怎么办？"
    ]
}

YELLOW_PAGES = """
| 部门 | 联系电话 | 备注 |
|------|---------|------|
| 🚨 校园110（保卫处24小时） | 0371-61916110 | 紧急情况拨打 |
| 📞 学校总值班室 | 0371-61911000 | 24小时值班 |
| 🔧 后勤管理处 | 0371-61912800 | 工作日 8:00-17:30 |
| 🏠 后勤物业报修热线 | 0371-61913110 | 宿舍/教室报修 |
| 🏥 校医院急诊(24h) | 0371-61912730 | 全天候 |
| 📚 招生办公室 | 0371-61916161 | 工作日 8:00-17:30 |
| 💻 信息管理中心 | 0371-61912718 | 网络/校园卡 |

⚠️ 以上信息请以官方最新发布为准（更新时间：2026年7月）
"""


# ===================== Session State初始化 =====================
def init_session_state():
    defaults = {
        "question": "",
        "chat_history": [],
        "last_request_time": 0,
        "total_requests": 0,
        "pending_question": None,
        "selected_role": "新生",
        "answer_length": 0,
        "api_elapsed": 0.0
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ===================== 核心：流式问答处理 =====================
def process_question_stream(question: str, role: str, placeholder) -> Optional[str]:
    """流式处理用户问题"""
    if not question or not question.strip():
        return None

    if not API_KEY:
        st.error("❌ API Key未配置")
        return None

    data_path = Path(__file__).parent.parent / "data"
    if not data_path.exists() or not list(data_path.glob("*.md")):
        st.error("📁 数据文件缺失")
        return None

    try:
        school_data = load_school_info()
        sys_prompt = get_system_prompt(role, school_data)
    except Exception as e:
        st.error(f"❌ 加载失败: {e}")
        return None

    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": question}
        ],
        "temperature": 0.7,
        "max_tokens": 600,
        "stream": True
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            if time.time() - st.session_state.last_request_time < 1:
                time.sleep(1)

            api_start = time.time()
            resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=REQUEST_TIMEOUT, stream=True)
            api_time = time.time() - api_start

            st.session_state.last_request_time = time.time()

            if resp.status_code == 200:
                pass
            elif resp.status_code == 401:
                st.error("🔑 API Key失效")
                return None
            elif resp.status_code in [429, 503]:
                if attempt < MAX_RETRIES:
                    wait = RETRY_DELAY * (attempt + 1)
                    st.warning(f"⏳ 服务繁忙，{wait:.0f}秒后重试...")
                    time.sleep(wait)
                    continue
                st.error("🔧 服务暂时不可用，请稍后再试")
                return None
            elif resp.status_code >= 500:
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                st.error("🔧 服务异常，请稍后重试")
                return None
            else:
                st.error(f"❌ 状态码：{resp.status_code}")
                return None

            answer = ""
            for line in resp.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = line[6:]
                        if data == '[DONE]':
                            break
                        try:
                            chunk = json.loads(data)
                            if 'choices' in chunk and chunk['choices']:
                                delta = chunk['choices'][0].get('delta', {})
                                content = delta.get('content', '')
                                if content:
                                    answer += content
                                    placeholder.markdown(answer + "▌")
                        except json.JSONDecodeError:
                            continue

            placeholder.markdown(answer)

            if not answer or not answer.strip():
                st.warning("⚠️ AI返回空答案")
                return None

            st.session_state["answer_length"] = len(answer)
            st.session_state["api_elapsed"] = api_time
            return answer

        except Timeout:
            if attempt < MAX_RETRIES:
                st.warning("⏰ 超时，重试中...")
                time.sleep(RETRY_DELAY)
            else:
                st.error("⏰ 请求超时")
                return None
        except ConnectionError:
            st.error("🌐 网络连接失败，请检查网络")
            return None
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                st.error(f"💥 错误: {e}")
                return None

    return None


# ===================== UI渲染 =====================
def render_question_tabs():
    st.markdown("### 💡 试试这些问题（点击直接提问）")
    tab1, tab2, tab3 = st.tabs(["🎓 新生指南", "📋 办事流程", "🚨 应急防骗"])

    with tab1:
        questions = TAB_QUESTIONS["新生指南"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(q, key=f"tab1_{i}", use_container_width=True):
                    st.session_state["question"] = q
                    st.session_state["pending_question"] = q
                    st.rerun()

    with tab2:
        questions = TAB_QUESTIONS["办事流程"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(q, key=f"tab2_{i}", use_container_width=True):
                    st.session_state["question"] = q
                    st.session_state["pending_question"] = q
                    st.rerun()

    with tab3:
        questions = TAB_QUESTIONS["应急防骗"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(q, key=f"tab3_{i}", use_container_width=True):
                    st.session_state["question"] = q
                    st.session_state["pending_question"] = q
                    st.rerun()


def render_ui():
    st.title("✈️ 小航 · 郑州航院校园信息助手")
    st.caption("你的郑航校园百事通，有事就问小航~")

    role = st.selectbox("请选择你的身份：", ROLES, key="role_select")
    st.session_state["selected_role"] = role

    render_question_tabs()

    st.divider()
    st.markdown("### ✍️ 或直接输入你的问题")

    def submit_question():
        q = st.session_state.question_input
        if q and q.strip():
            st.session_state["question"] = q
            st.session_state["pending_question"] = q

    question = st.text_input(
        "🔍 有啥想问的？",
        value=st.session_state["question"],
        placeholder="例如：宿舍怎么申请？也可以点击上面的推荐问题",
        key="question_input",
        on_change=submit_question
    )

    return role, question


def render_chat_history():
    if st.session_state["chat_history"]:
        st.divider()
        with st.expander("📜 查看历史对话", expanded=False):
            paired = []
            i = 0
            while i < len(st.session_state["chat_history"]):
                if i + 1 < len(st.session_state["chat_history"]):
                    u = st.session_state["chat_history"][i]
                    a = st.session_state["chat_history"][i + 1]
                    paired.append({
                        "time": a.get("timestamp", ""),
                        "role": a.get("role_name", ""),
                        "question": u["content"],
                        "answer": a["content"],
                        "length": a.get("answer_length", 0),
                        "elapsed": a.get("api_elapsed", 0.0)
                    })
                i += 2
            paired.reverse()

            for idx, item in enumerate(paired):
                st.caption(f"⏰ {item['time']}  👤 {item['role']}")
                st.markdown(f"**❓ {item['question']}**")
                st.markdown(f"**💡 {item['answer']}**")
                if item['length'] > 0:
                    st.caption(f"📝 {item['length']} 字 · ⚡ {item['elapsed']:.1f} 秒")
                if idx < len(paired) - 1:
                    st.divider()

            if st.button("🗑️ 清除所有历史记录", key="clear_history"):
                st.session_state["chat_history"] = []
                st.rerun()


def render_yellow_pages():
    st.divider()
    st.header("📞 校园电话")
    st.caption("AI无法查询时可直接查看本表")
    st.markdown(YELLOW_PAGES)


# ===================== 主程序 =====================
def main():
    role, question = render_ui()

    pending = st.session_state.get("pending_question")

    if pending and pending.strip():
        current_role = st.session_state.get("selected_role", "新生")
        st.markdown("### 📝 AI回答")
        answer_placeholder = st.empty()

        with st.spinner("🤔 小航正在思考中..."):
            answer = process_question_stream(pending.strip(), current_role, answer_placeholder)

            if answer:
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                st.session_state["chat_history"].append({
                    "role": "user", "content": pending,
                    "timestamp": current_time, "role_name": current_role
                })
                st.session_state["chat_history"].append({
                    "role": "assistant", "content": answer,
                    "timestamp": current_time, "role_name": current_role,
                    "answer_length": st.session_state.get("answer_length", len(answer)),
                    "api_elapsed": st.session_state.get("api_elapsed", 0.0)
                })

                length = st.session_state.get("answer_length", len(answer))
                elapsed = st.session_state.get("api_elapsed", 0.0)
                st.caption(f"📝 回答字数：{length} 字 · ⚡ 耗时：{elapsed:.1f} 秒")

                st.divider()
                col1, col2, col3 = st.columns([1, 1, 4])
                with col1:
                    if st.button("👍 有帮助", key="helpful_main"):
                        st.success("感谢反馈！😊")
                with col2:
                    if st.button("👎 需改进", key="improve_main"):
                        st.info("我们会持续优化！💪")

                # 清空标志位，回答保留在页面上
                st.session_state["pending_question"] = None
                st.session_state["question"] = ""
            else:
                st.session_state["pending_question"] = None
                st.info("💡 提示：你可以查看下方的电话黄页获取直接联系方式")

    else:
        st.info("💬 请输入你的问题，或点击上方推荐问题快速提问")

    render_chat_history()
    render_yellow_pages()
    st.divider()
    st.caption("© 2026 郑州航院 · 信息仅供参考，请以官方通知为准")


# ===================== 错误边界 =====================
try:
    main()
except Exception as e:
    logger.exception("应用运行异常")
    st.error(f"""
    ## 💥 应用发生严重错误
    错误信息：{str(e)}
    请刷新页面重试
    """)