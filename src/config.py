# 配置文件
import os

# 大模型API基础配置
API_URL = os.getenv("API_URL", "https://xxx/v1/chat/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-3.5-turbo")
API_KEY = os.getenv("API_KEY", "")

# 知识库路径配置
BASE_DATA_PATH = "../data/"