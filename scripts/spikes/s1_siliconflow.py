"""技术验证 S1：硅基流动模型列表 + 对话模型 + 视觉模型。

用法：python s1_siliconflow.py <一张测试图片路径>
"""
import base64
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
client = OpenAI(api_key=os.environ["SILICONFLOW_API_KEY"], base_url=os.environ["SILICONFLOW_API_BASE"])

# 1. 列出全部模型，筛出 DeepSeek V4 / Flash 系列，用于核对准确的模型 ID
ids = sorted(m.id for m in client.models.list().data)
ds = [i for i in ids if "deepseek" in i.lower() and ("v4" in i.lower() or "flash" in i.lower())]
print(f"models total={len(ids)}; deepseek v4/flash candidates:")
for i in ds:
    print("  ", i)

# 2. 对话模型：.env 中配置的 ID 不存在时，从候选中自动挑一个
llm = os.environ.get("LLM_MODEL") or ""
if llm not in ids:
    print(f"LLM_MODEL '{llm}' 不在列表中，需要改为上面的准确 ID")
    llm = next((i for i in ds if "v4" in i.lower() and "flash" in i.lower() and "vision" not in i.lower() and "4.1" not in i), None)
print("chat model used:", llm)
if llm:
    t = time.time()
    r = client.chat.completions.create(model=llm, messages=[{"role": "user", "content": "用一句话介绍 Python 的默认参数。"}], max_tokens=100)
    print(f"chat ok in {time.time() - t:.1f}s:", r.choices[0].message.content.strip()[:120])

# 3. 视觉模型：用命令行传入的图片逐个测试候选模型
vl_candidates = [i for i in ds if "4.1" in i or "vision" in i.lower()]
print("vision candidates:", vl_candidates)
img = Path(sys.argv[1])
b64 = base64.b64encode(img.read_bytes()).decode()
mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
for vm in vl_candidates:
    t = time.time()
    try:
        r = client.chat.completions.create(model=vm, max_tokens=150, messages=[{"role": "user", "content": [
            {"type": "text", "text": "这是课程讲义里的一张图片，请用两句话描述它的内容。"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]}])
        print(f"vision [{vm}] ok in {time.time() - t:.1f}s:", r.choices[0].message.content.strip()[:150])
    except Exception as e:
        print(f"vision [{vm}] FAILED:", str(e)[:200])
