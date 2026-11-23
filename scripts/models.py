"""原 Demo 腾讯 Hy4 客户端的最小保留版本，按需导入，禁止打印密钥。"""
from langchain_openai import ChatOpenAI

from config import (
    MODEL_TIMEOUT,
    TEMPERATURE,
    TENCENT_API_KEY,
    TENCENT_API_MODEL,
    TENCENT_API_URL,
)


def get_tx_model_client(model=None, temperature=None):
    """通过 OpenAI 兼容协议创建 Hy4 客户端；配置缺失时给出可操作错误。"""
    selected_model = model or TENCENT_API_MODEL
    missing = [
        name
        for name, value in (
            ("TENCENT_API_KEY", TENCENT_API_KEY),
            ("TENCENT_API_URL", TENCENT_API_URL),
            ("TENCENT_API_MODEL", selected_model),
        )
        if not value
    ]
    if missing:
        raise RuntimeError("独立 Hy4 后端缺少环境变量：" + ", ".join(missing))
    return ChatOpenAI(
        api_key=TENCENT_API_KEY,
        base_url=TENCENT_API_URL,
        model=selected_model,
        temperature=TEMPERATURE if temperature is None else temperature,
        timeout=MODEL_TIMEOUT,
        max_retries=0,
        extra_body={"enable_thinking": False},
    )
