"""MinerU 云端精准解析 API v4（技术方案 T1，技术验证 S4）。

流程：申请上传链接 → PUT 上传 → 轮询批量结果 → 下载 full_zip_url 并解压。
结果 zip 中有 full.md 与 images/，Markdown 用 ![](images/<hash>.jpg) 引用图片。
"""
import io
import logging
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class MinerUError(RuntimeError):
    pass


class MinerUClient:
    def __init__(self, base: str, token: str, poll_interval: float = 5.0, timeout: float = 1800.0):
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self.poll_interval = poll_interval
        self.timeout = timeout

    def convert(self, file_path: Path, out_dir: Path,
                on_progress: Callable[[int, int], None] | None = None) -> Path:
        """返回解压后的 full.md 路径。on_progress(已解析页数, 总页数)。"""
        t0 = time.time()
        body = self._post("/file-urls/batch", {"files": [{"name": file_path.name}],
                                               "model_version": "vlm", "language": "ch"})
        batch_id, upload_url = body["batch_id"], body["file_urls"][0]
        with open(file_path, "rb") as f:
            r = requests.put(upload_url, data=f, timeout=600)
        if r.status_code != 200:
            raise MinerUError(f"上传文件到 MinerU 失败：HTTP {r.status_code}")
        logger.info("MinerU 已上传 %s（batch %s）", file_path.name, batch_id)

        while True:
            if time.time() - t0 > self.timeout:
                raise MinerUError(f"MinerU 解析超时（>{self.timeout / 60:.0f} 分钟）")
            time.sleep(self.poll_interval)
            item = self._get(f"/extract-results/batch/{batch_id}")["extract_result"][0]
            state = item.get("state")
            prog = item.get("extract_progress") or {}
            if on_progress and prog.get("total_pages"):
                on_progress(int(prog.get("extracted_pages") or 0), int(prog["total_pages"]))
            if state == "done":
                break
            if state == "failed":
                raise MinerUError(f"MinerU 解析失败：{item.get('err_msg') or '未知原因'}")

        zr = requests.get(item["full_zip_url"], timeout=600)
        zr.raise_for_status()
        out_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zr.content)) as z:
            safe_extract(z, out_dir)
        md = out_dir / "full.md"
        if not md.exists():
            found = list(out_dir.rglob("*.md"))
            if not found:
                raise MinerUError("MinerU 结果中没有 Markdown 文件")
            md = found[0]
        logger.info("MinerU 解析完成 %s，用时 %.0fs", file_path.name, time.time() - t0)
        return md

    def _post(self, path: str, payload: dict) -> dict:
        r = requests.post(self.base + path, headers=self.headers, json=payload, timeout=30)
        return self._unwrap(r)

    def _get(self, path: str) -> dict:
        r = requests.get(self.base + path, headers=self.headers, timeout=30)
        return self._unwrap(r)

    @staticmethod
    def _unwrap(r: requests.Response) -> dict:
        try:
            body = r.json()
        except ValueError:
            raise MinerUError(f"MinerU 返回了非 JSON 响应：HTTP {r.status_code}") from None
        if body.get("code") != 0:
            raise MinerUError(f"MinerU 接口错误：{body.get('code')} {body.get('msg')}")
        return body["data"]


def safe_extract(z: zipfile.ZipFile, dest: Path) -> None:
    """解压并防止 zip 路径穿越（../ 或绝对路径）。"""
    dest = dest.resolve()
    for member in z.infolist():
        target = (dest / member.filename).resolve()
        if not target.is_relative_to(dest):
            raise MinerUError(f"压缩包包含非法路径：{member.filename}")
    z.extractall(dest)
