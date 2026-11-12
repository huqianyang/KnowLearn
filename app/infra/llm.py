"""硅基流动（OpenAI 兼容）：视觉模型图片摘要；M4 将在此加入对话生成。

模型 ID 经技术验证 S1 确认：对话 deepseek-ai/DeepSeek-V4-Flash，视觉 deepseek-ai/DeepSeek-V4.1-Flash。
"""
import base64
import logging
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

IMAGE_PROMPT = (
    "下面是一份教学资料中的图片，以及图片前后的文字。请用一到两句话（不超过 80 字）客观描述图片内容，"
    "说明它展示了什么（如界面、代码运行结果、架构图、流程图、表格），只输出描述本身。\n\n"
    "【图片前文】{before}\n【图片后文】{after}"
)


class LlmClient:
    def __init__(self, api_key: str, base_url: str, llm_model: str, vlm_model: str):
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=0)
        self.llm_model = llm_model
        self.vlm_model = vlm_model

    def describe_image(self, image: bytes | str, mime: str, before: str, after: str, retries: int = 3) -> str:
        """image 为图片字节或 http(s) 地址。失败时按指数退避重试，最终失败抛出异常。"""
        url = image if isinstance(image, str) else f"data:{mime};base64,{base64.b64encode(image).decode()}"
        messages = [{"role": "user", "content": [
            {"type": "text", "text": IMAGE_PROMPT.format(before=before or "（无）", after=after or "（无）")},
            {"type": "image_url", "image_url": {"url": url}},
        ]}]
        last: Exception | None = None
        for attempt in range(retries):
            try:
                r = self.client.chat.completions.create(model=self.vlm_model, messages=messages, max_tokens=200)
                text = (r.choices[0].message.content or "").strip()
                if text:
                    return text
                last = RuntimeError("视觉模型返回空内容")
            except Exception as e:  # noqa: BLE001
                last = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"视觉模型调用失败（已重试 {retries} 次）：{last}")
