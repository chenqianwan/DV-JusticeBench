"""
配置文件
使用环境变量存储敏感信息（如API密钥）
支持从.env文件或环境变量读取
"""
import os
from dotenv import load_dotenv

# 加载.env文件中的环境变量（如果存在）
load_dotenv()

# DeepSeek API配置
DEEPSEEK_API_KEY = os.getenv('DEEPSEEK_API_KEY', '')
DEEPSEEK_API_URL = 'https://api.deepseek.com/v1/chat/completions'

# OpenAI/ChatGPT API配置
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
OPENAI_API_URL = os.getenv('OPENAI_API_URL', 'https://api3.xhub.chat/v1/chat/completions')
OPENAI_MAX_RPM = int(os.getenv('OPENAI_MAX_RPM', '5000'))  # OpenAI API每分钟最大请求数
OPENAI_MAX_RPS = int(os.getenv('OPENAI_MAX_RPS', '50'))  # OpenAI API每秒最大请求数

# Qwen（通义千问）API配置
QWEN_API_KEY = os.getenv('QWEN_API_KEY', '')
QWEN_API_URL = os.getenv('QWEN_API_URL', 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')  # 阿里云DashScope API端点
QWEN_MAX_RPM = int(os.getenv('QWEN_MAX_RPM', '3000'))  # Qwen API每分钟最大请求数
QWEN_MAX_RPS = int(os.getenv('QWEN_MAX_RPS', '50'))  # Qwen API每秒最大请求数

# Anthropic/Claude API配置
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
ANTHROPIC_API_URL = os.getenv('ANTHROPIC_API_URL', 'https://api.anthropic.com/v1/messages')
ANTHROPIC_MAX_RPM = int(os.getenv('ANTHROPIC_MAX_RPM', '5000'))  # Anthropic API每分钟最大请求数
ANTHROPIC_MAX_RPS = int(os.getenv('ANTHROPIC_MAX_RPS', '50'))  # Anthropic API每秒最大请求数

# API提供商选择（'deepseek'、'chatgpt'、'qwen' 或 'claude'）
API_PROVIDER = os.getenv('API_PROVIDER', 'deepseek').lower()

# 应用配置
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'

# 数据目录
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
CASES_DIR = os.path.join(DATA_DIR, 'cases')
RESULTS_DIR = os.path.join(DATA_DIR, 'results')

# 确保数据目录存在
os.makedirs(CASES_DIR, exist_ok=True)
os.makedirs(RESULTS_DIR, exist_ok=True)

# 并发处理配置
MAX_CONCURRENT_WORKERS = int(os.getenv('MAX_CONCURRENT_WORKERS', '50'))  # 批量分析时的最大并发数
DEEPSEEK_MAX_RPM = int(os.getenv('DEEPSEEK_MAX_RPM', '3000'))  # DeepSeek API每分钟最大请求数
DEEPSEEK_MAX_RPS = int(os.getenv('DEEPSEEK_MAX_RPS', '50'))  # DeepSeek API每秒最大请求数
