"""技术验证 S4：MinerU 云端精准解析 API（批量上传本地文件）。

用法：python s4_mineru_api.py <输出目录> <文件路径> [page_ranges]
运行：uv run --no-project --with requests --with python-dotenv python scripts/spikes/s4_mineru_api.py ...
"""
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
BASE = os.environ["MINERU_API_BASE"].rstrip("/")
HEAD = {"Authorization": f"Bearer {os.environ['MINERU_API_TOKEN']}", "Content-Type": "application/json"}

out_dir, file_path = Path(sys.argv[1]), Path(sys.argv[2])
page_ranges = sys.argv[3] if len(sys.argv) > 3 else None
out_dir.mkdir(parents=True, exist_ok=True)

file_item = {"name": file_path.name, "data_id": "spike"}
if page_ranges:
    file_item["page_ranges"] = page_ranges
t0 = time.time()
r = requests.post(f"{BASE}/file-urls/batch", headers=HEAD, timeout=30,
                  json={"files": [file_item], "model_version": "vlm", "language": "ch"})
body = r.json()
print("apply:", r.status_code, body.get("code"), body.get("msg"))
if body.get("code") != 0:
    sys.exit(1)
batch_id, url = body["data"]["batch_id"], body["data"]["file_urls"][0]
with open(file_path, "rb") as f:
    up = requests.put(url, data=f, timeout=300)
print("upload:", up.status_code, f"{time.time() - t0:.1f}s")

last = None
while True:
    time.sleep(5)
    res = requests.get(f"{BASE}/extract-results/batch/{batch_id}", headers=HEAD, timeout=30).json()
    item = res["data"]["extract_result"][0]
    state, prog = item.get("state"), item.get("extract_progress")
    if (state, prog) != last:
        print(f"[{time.time() - t0:6.1f}s] state={state} progress={prog} err={item.get('err_msg')}")
        last = (state, prog)
    if state in ("done", "failed"):
        break
    if time.time() - t0 > 1800:
        print("timeout"); sys.exit(1)
if state != "done":
    sys.exit(1)

zbytes = requests.get(item["full_zip_url"], timeout=300).content
z = zipfile.ZipFile(io.BytesIO(zbytes))
names = z.namelist()
print("zip entries:", len(names))
for n in names[:15]:
    print("  ", n)
z.extractall(out_dir)
md = next(out_dir.rglob("full.md"), None) or next(out_dir.rglob("*.md"))
text = md.read_text(encoding="utf-8")
heads = [l for l in text.splitlines() if l.startswith("#")]
imgs = [l for l in text.splitlines() if "![" in l]
print(f"markdown: {md.name} {len(text)} chars, headings={len(heads)}, image refs={len(imgs)}")
print("first headings:", heads[:12])
print("first image ref:", imgs[:2])
print(f"total {time.time() - t0:.1f}s")
