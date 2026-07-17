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
    """安全获取API Key（多层级回退）"""
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
MODEL_NAME = "deepseek-ai/DeepSeek-V3.1-Terminus"
MAX_RETRIES = 2
RETRY_DELAY = 1.0
REQUEST_TIMEOUT = (5, 30)  # 🔥 缩短超时时间

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# ===================== 导入prompts模块 =====================
try:
    from prompts import load_school_info, get_system_prompt
except ImportError:
    st.error("❌ 缺少prompts.py文件，请检查文件是否存在")
    st.stop()

# ===================== 常量定义 =====================
ROLES = ["新生", "在校生", "教师"]

TAB_QUESTIONS = {
    "新生指南": [
        "报到那天先去哪？",
        "学费什么时候交？",
        "宿舍是4人间还是6人间？",
        "军训需要准备什么？",
        "校园卡怎么办理？",
        "怎么选课？"
    ],
    "办事流程": [
        "怎么开在读证明？",
        "校园卡丢了怎么补？",
        "转专业怎么转？",
        "图书馆几点关门？",
        "怎么借教室？",
        "成绩单怎么打印？"
    ],
    "应急防骗": [
        "有人冒充辅导员要钱怎么办？",
        "校园报警电话是多少？",
        "校医院急诊电话？",
        "遇到诈骗怎么处理？",
        "心理压力大找谁？",
        "宿舍东西被盗怎么办？"
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
    """初始化会话状态"""
    defaults = {
        "question": "",
        "chat_history": [],
        "last_request_time": 0,
        "total_requests": 0,
        "error_count": 0,
        "pending_question": None,
        "current_answer": None,
        "current_role": None,
        "selected_role": "新生",
        "answer_length": 0,
        "api_elapsed": 0.0
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ===================== 核心：流式问答处理（优化速度）=====================
def process_question_stream(question: str, role: str, placeholder) -> Optional[str]:
    """
    流式处理用户问题，实时显示回答
    🔥 关键优化：max_tokens=600 + stream=True
    """
    if not question or not question.strip():
        return None

    if not API_KEY or API_KEY == "":
        st.error("❌ API Key未配置，请联系管理员")
        return None

    # 检查数据文件
    data_path = Path(__file__).parent.parent / "data"
    if not data_path.exists():
        st.error("📁 数据目录不存在，请检查data文件夹")
        return None

    md_files = list(data_path.glob("*.md"))
    if not md_files:
        st.error("📄 数据文件缺失，请补齐data目录下的md文件")
        return None

    # 加载数据和提示词（不计入API耗时）
    try:
        school_data = load_school_info()
        if not school_data:
            st.error("📊 学校数据加载为空")
            return None

        sys_prompt = get_system_prompt(role, school_data)
        if not sys_prompt:
            st.error("📝 系统提示词生成失败")
            return None
    except Exception as e:
        st.error(f"❌ 加载学校信息失败: {str(e)}")
        return None

    # 构建请求（🔥 max_tokens=600 大幅提速）
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": question}
        ],
        "temperature": 0.7,
        "max_tokens": 600,  # 🔥 降低token数，响应快3-4倍
        "stream": True  # 🔥 开启流式输出
    }

    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            # 限流控制
            current_time = time.time()
            if current_time - st.session_state.last_request_time < 1:
                time.sleep(1)

            # ===== 只计算纯API调用时间 =====
            api_start = time.time()

            resp = requests.post(
                API_URL,
                headers=HEADERS,
                json=payload,
                timeout=REQUEST_TIMEOUT,
                stream=True
            )

            # API耗时结束
            api_time = time.time() - api_start

            st.session_state.last_request_time = time.time()
            st.session_state.total_requests += 1

            # HTTP状态码处理
            if resp.status_code == 200:
                pass
            elif resp.status_code == 401:
                st.error("🔑 API Key失效或无效，请联系管理员更新")
                return None
            elif resp.status_code == 429:
                if attempt < MAX_RETRIES:
                    wait_time = RETRY_DELAY * (attempt + 1)
                    st.warning(f"⏳ 请求频繁，等待{wait_time}秒后重试...")
                    time.sleep(wait_time)
                    continue
                else:
                    st.error("⏳ 请求过于频繁，请30秒后再试")
                    return None
            elif resp.status_code in [500, 502, 503]:
                st.error("🔧 AI服务暂时不可用，请稍后重试")
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)
                    continue
                return None
            else:
                st.error(f"❌ API接口异常，状态码：{resp.status_code}")
                return None

            # ===== 流式读取回答 =====
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
                                    # 实时更新显示（带光标动画）
                                    placeholder.markdown(answer + "▌")
                        except json.JSONDecodeError:
                            continue

            # 最终显示（去掉光标）
            placeholder.markdown(answer)

            # 验证答案
            if not answer or not answer.strip():
                st.warning("⚠️ AI返回了空答案，请尝试重新提问")
                return None

            # 保存统计信息
            st.session_state["answer_length"] = len(answer)
            st.session_state["api_elapsed"] = api_time

            return answer

        except Timeout:
            if attempt < MAX_RETRIES:
                st.warning("⏰ 请求超时，正在重试...")
                time.sleep(RETRY_DELAY)
                last_error = "请求超时"
            else:
                st.error("⏰ 请求超时，请稍后再试")
                return None

        except ConnectionError:
            st.error("🌐 网络连接失败，请检查网络")
            return None

        except RequestException as e:
            if attempt < MAX_RETRIES:
                st.warning("🌐 网络异常，正在重试...")
                time.sleep(RETRY_DELAY)
                last_error = str(e)
            else:
                st.error(f"🌐 网络请求失败: {str(e)}")
                return None

        except Exception as e:
            logger.exception("未知错误")
            st.error(f"💥 系统错误: {str(e)}")
            st.session_state.error_count += 1
            return None

    if last_error:
        st.error(f"❌ 重试{MAX_RETRIES}次后仍失败: {last_error}")
    return None


# ===================== UI渲染 =====================
def render_question_tabs():
    """功能5：问题分类标签"""
    st.markdown("### 💡 试试这些问题（点击直接提问）")

    tab1, tab2, tab3 = st.tabs(["🎓 新生指南", "📋 办事流程", "🚨 应急防骗"])

    # Tab1: 新生指南
    with tab1:
        st.caption("刚入学？看看这些常见问题")
        questions = TAB_QUESTIONS["新生指南"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(
                        q,
                        key=f"tab1_{i}",
                        use_container_width=True,
                        help=f"点击提问：{q}"
                ):
                    st.session_state["pending_question"] = q
                    st.session_state["question"] = q
                    st.rerun()

    # Tab2: 办事流程
    with tab2:
        st.caption("办理各种事务？这里有流程指引")
        questions = TAB_QUESTIONS["办事流程"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(
                        q,
                        key=f"tab2_{i}",
                        use_container_width=True,
                        help=f"点击提问：{q}"
                ):
                    st.session_state["pending_question"] = q
                    st.session_state["question"] = q
                    st.rerun()

    # Tab3: 应急防骗
    with tab3:
        st.caption("遇到紧急情况？快速查找帮助")
        questions = TAB_QUESTIONS["应急防骗"]
        cols = st.columns(2)
        for i, q in enumerate(questions):
            with cols[i % 2]:
                if st.button(
                        q,
                        key=f"tab3_{i}",
                        use_container_width=True,
                        help=f"点击提问：{q}"
                ):
                    st.session_state["pending_question"] = q
                    st.session_state["question"] = q
                    st.rerun()


def render_ui():
    """渲染用户界面"""
    st.title("✈️ 小航 · 郑州航院校园信息助手")
    st.caption("你的郑航校园百事通，有事就问小航~")

    # 身份选择
    role = st.selectbox(
        "请选择你的身份：",
        ROLES,
        help="不同身份会获得更精准的回答",
        key="role_select"
    )
    st.session_state["selected_role"] = role

    # 问题分类标签
    render_question_tabs()

    # 问题输入区
    st.divider()
    st.markdown("### ✍️ 或直接输入你的问题")
    question = st.text_input(
        "🔍 有啥想问的？",
        value=st.session_state["question"],
        placeholder="例如：宿舍怎么申请？也可以点击上面的推荐问题",
        key="question_input",
        on_change=lambda: setattr(st.session_state, "pending_question", st.session_state.question_input)
    )

    return role, question


def render_answer(answer: str):
    """渲染AI回答 + 功能6：元信息展示"""
    # 回答已经在流式过程中显示了，这里只显示统计信息

    # 功能6：显示字数和耗时
    answer_length = st.session_state.get("answer_length", len(answer))
    api_elapsed = st.session_state.get("api_elapsed", 0.0)
    st.caption(f"📝 回答字数：{answer_length} 字 · ⚡ 耗时：{api_elapsed:.1f} 秒")

    # 反馈按钮
    st.divider()
    col1, col2, col3 = st.columns([1, 1, 4])

    with col1:
        if st.button("👍 有帮助", key="helpful"):
            st.success("感谢反馈！😊")

    with col2:
        if st.button("👎 需改进", key="improve"):
            st.info("我们会持续优化！💪")


def render_chat_history():
    """渲染聊天历史（最新在最上面，顺序：时间→身份→问答）"""
    if st.session_state["chat_history"]:
        st.divider()
        with st.expander("📜 查看历史对话", expanded=False):
            # 将聊天记录配对
            paired_history = []
            i = 0
            while i < len(st.session_state["chat_history"]):
                if i + 1 < len(st.session_state["chat_history"]):
                    user_msg = st.session_state["chat_history"][i]
                    assistant_msg = st.session_state["chat_history"][i + 1]

                    paired_history.append({
                        "time": assistant_msg.get("timestamp", "未知时间"),
                        "role": assistant_msg.get("role_name", "未知身份"),
                        "question": user_msg["content"],
                        "answer": assistant_msg["content"],
                        "length": assistant_msg.get("answer_length", 0),
                        "elapsed": assistant_msg.get("api_elapsed", 0.0)
                    })
                i += 2

            # 反转顺序，最新在最上面
            paired_history.reverse()

            # 显示历史记录
            for idx, item in enumerate(paired_history):
                st.caption(f"⏰ {item['time']}")
                st.caption(f"👤 身份：{item['role']}")
                st.markdown(f"**❓ 问题：** {item['question']}")
                st.markdown(f"**💡 回答：** {item['answer']}")

                # 历史统计信息
                if item['length'] > 0:
                    st.caption(f"📝 {item['length']} 字 · ⚡ {item['elapsed']:.1f} 秒")

                if idx < len(paired_history) - 1:
                    st.divider()

            # 清除历史按钮
            if st.button("🗑️ 清除所有历史记录", key="clear_history"):
                st.session_state["chat_history"] = []
                st.rerun()


def render_yellow_pages():
    """渲染校园电话黄页"""
    st.divider()
    st.header("📞 校园电话")
    st.caption("AI无法查询时可直接查看本表（信息以官方最新发布为准）")
    st.markdown(YELLOW_PAGES)


# ===================== 主程序 =====================
def main():
    """主程序入口"""
    role, question = render_ui()

    # 检查是否有待处理的问题
    pending = st.session_state.get("pending_question")

    if pending and pending.strip():
        current_role = st.session_state.get("selected_role", "新生")

        st.markdown("### 📝 AI回答")

        # 创建占位符用于流式显示
        answer_placeholder = st.empty()

        with st.spinner("🤔 小航正在思考中..."):
            # 使用流式处理
            answer = process_question_stream(pending.strip(), current_role, answer_placeholder)

            if answer:
                # 保存到聊天历史
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                st.session_state["chat_history"].append({
                    "role": "user",
                    "content": pending,
                    "timestamp": current_time,
                    "role_name": current_role
                })
                st.session_state["chat_history"].append({
                    "role": "assistant",
                    "content": answer,
                    "timestamp": current_time,
                    "role_name": current_role,
                    "answer_length": st.session_state.get("answer_length", len(answer)),
                    "api_elapsed": st.session_state.get("api_elapsed", 0.0)
                })

                # 显示统计信息
                length = st.session_state.get("answer_length", len(answer))
                elapsed = st.session_state.get("api_elapsed", 0.0)
                st.caption(f"📝 回答字数：{length} 字 · ⚡ 耗时：{elapsed:.1f} 秒")

                # 反馈按钮
                st.divider()
                col1, col2, col3 = st.columns([1, 1, 4])
                with col1:
                    if st.button("👍 有帮助", key="helpful_main"):
                        st.success("感谢反馈！😊")
                with col2:
                    if st.button("👎 需改进", key="improve_main"):
                        st.info("我们会持续优化！💪")

                # 清空待处理标志和输入框
                st.session_state["pending_question"] = None
                st.session_state["question"] = ""
                st.session_state["current_answer"] = None
            else:
                st.session_state["pending_question"] = None
                st.info("💡 提示：你可以查看下方的电话黄页获取直接联系方式")

    elif not pending:
        if question is not None and question.strip() == "":
            st.info("💬 请点击上方推荐问题或直接输入你的问题")

    # 渲染聊天历史
    render_chat_history()

    # 渲染电话黄页
    render_yellow_pages()

    # 页脚
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

    请尝试：
    1. 刷新页面
    2. 清除浏览器缓存
    3. 联系管理员

    错误代码：{hash(str(e))}
    """)