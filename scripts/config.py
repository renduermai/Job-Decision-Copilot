"""仅独立 Hy4 后端使用的环境配置；不保存用户路径或画像。"""
import os

from dotenv import load_dotenv

load_dotenv()

TENCENT_API_URL = os.getenv("TENCENT_API_URL", "")
TENCENT_API_MODEL = os.getenv("TENCENT_API_MODEL", "")
TENCENT_API_KEY = os.getenv("TENCENT_API_KEY", "")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "3"))
OCR_MAX_TOKENS = int(os.getenv("OCR_MAX_TOKENS", "15000"))
PARSE_MAX_TOKENS = int(os.getenv("PARSE_MAX_TOKENS", "10000"))
MODEL_TIMEOUT = float(os.getenv("MODEL_TIMEOUT", "120"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0"))
