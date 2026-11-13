"""硅基流动（OpenAI 兼容）：视觉模型图片摘要、对话生成（含流式）。

模型 ID 经技术验证 S1 确认：对话 deepseek-ai/DeepSeek-V4-Flash，视觉 deepseek-ai/DeepSeek-V4.1-Flash。
"""
import base64
import logging
import time

from openai import OpenAI

logger = logging.getLogger(__name__)

# 图片摘要提示词：带上图片前后的文字，让模型结合上下文描述图片
IMAGE_PROMPT = (
    "下面是一份教学资料中的图片，以及图片前后的文字。请用一到两句话（不超过 80 字）客观描述图片内容，"
    "说明它展示了什么（如界面、代码运行结果、架构图、流程图、表格），只输出描述本身。\n\n"
    "【图片前文】{before}\n【图片后文】{after}"
)


class LlmClient:
    """大模型客户端，同时封装对话模型和视觉模型。

    Args:
        api_key: 硅基流动 API Key。
        base_url: OpenAI 兼容接口地址。
        llm_model: 对话模型 ID。
        vlm_model: 视觉模型 ID。
    """

    def __init__(self, api_key: str, base_url: str, llm_model: str, vlm_model: str):
        # max_retries=0：关闭 SDK 自带重试，是否重试由各方法自己决定
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=60, max_retries=0)
        self.llm_model = llm_model
        self.vlm_model = vlm_model

    def describe_image(self, image: bytes | str, mime: str, before: str, after: str, retries: int = 3) -> str:
        """为一张图片生成文字摘要（导入时使用）。

        Args:
            image: 图片字节，或可公开访问的 http(s) 地址。
            mime: 图片 MIME 类型，image 为字节时用于拼 data URL。
            before: 图片前面的文字（上下文）。
            after: 图片后面的文字（上下文）。
            retries: 最多尝试次数，失败时按 1、2、4… 秒指数退避。

        Returns:
            图片描述文本。

        Raises:
            RuntimeError: 全部尝试都失败（调用方会用「图片」占位，不影响导入）。
        """
        # 字节形式的图片转为 base64 data URL
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

    # ---------------- 对话（M4） ----------------
    # 问答链路不重试，保证速度（技术方案第 6 章）

    def complete(self, messages: list[dict], max_tokens: int = 800, temperature: float = 0.2,
                 json_mode: bool = False) -> str:
        """非流式对话，返回完整文本（用于问题改写、HyDE）。

        Args:
            messages: OpenAI 格式的消息列表。
            max_tokens: 最大输出长度。
            temperature: 采样温度，越低越稳定。
            json_mode: True 时要求模型输出 JSON 对象。
        """
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        r = self.client.chat.completions.create(model=self.llm_model, messages=messages, max_tokens=max_tokens,
                                                temperature=temperature, **kwargs)
        return (r.choices[0].message.content or "").strip()

    def stream(self, messages: list[dict], max_tokens: int = 2000, temperature: float = 0.3):
        """逐段产出文本。调用方可随时停止迭代（流式中断）。"""
        resp = self.client.chat.completions.create(model=self.llm_model, messages=messages, max_tokens=max_tokens,
                                                   temperature=temperature, stream=True)
        try:
            for ev in resp:
                # 部分事件没有 choices 或内容为空（如结束事件），跳过
                if ev.choices and (delta := ev.choices[0].delta.content):
                    yield delta
        finally:
            # 调用方提前停止时也要关闭连接，避免模型继续生成
            resp.close()
