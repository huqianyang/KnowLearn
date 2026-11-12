"""批量导入教育资料（调用导入接口，任务在服务端排队、按顺序执行）。

用法：
    uv run python scripts/import_all.py <数据目录> [--base http://127.0.0.1:8010] [--only catalog,bank,lecture,project]

数据目录结构（即 1.资料/教育/数据）：
    课程介绍.md                → 课程目录
    题目资料.md                → 题库（依赖课程目录，脚本会先等课程目录导入完成）
    课程文档/*.docx            → 课程讲义，课程名取接口 A1 根据文件名给出的默认值
    项目文档/*.docx            → 项目文档，项目名取 A1 的默认值
    项目文档/<项目名>/*.md     → 项目文档：每个 md 与它引用的图片打成一个 zip 上传（PRD F1-1）
"""
import argparse
import re
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import requests

IMG_REF = re.compile(r'<img[^>]*?src\s*=\s*["\']([^"\']+)["\']|!\[[^\]]*\]\(\s*<?([^)\s>]+)', re.I)


def submit(base: str, path: Path, doc_type: str, name: str | None = None) -> str:
    data = {"doc_type": doc_type}
    if name:
        data["course_name" if doc_type == "lecture" else "project_name"] = name
    with open(path, "rb") as f:
        r = requests.post(f"{base}/api/v1/imports", files={"file": (path.name, f)}, data=data, timeout=300).json()
    if r["code"] != 0:
        raise SystemExit(f"提交失败 {path.name}: {r['message']}")
    d = r["data"]
    print(f"  已提交 {d['task_id'][:8]} {path.name}{'（覆盖）' if d['is_overwrite'] else ''}")
    return d["task_id"]


def suggest(base: str, filename: str, doc_type: str) -> str:
    d = requests.get(f"{base}/api/v1/imports/suggest", params={"filename": filename, "doc_type": doc_type}, timeout=30).json()["data"]
    return d["course_name"] if doc_type == "lecture" else d["project_name"]


def wait(base: str, task_id: str) -> dict:
    while True:
        d = requests.get(f"{base}/api/v1/imports/{task_id}", timeout=30).json()["data"]
        if d["status"] in ("succeeded", "failed"):
            return d
        time.sleep(3)


def zip_markdown(md: Path, out_dir: Path) -> Path:
    """把一个 md 和它引用的本地图片打成 zip，保持相对路径。"""
    text = md.read_text(encoding="utf-8")
    refs = {a or b for a, b in IMG_REF.findall(text)}
    out = out_dir / f"{md.parent.name}_{md.stem}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(md, md.name)
        for ref in refs:
            if ref.startswith(("http://", "https://")):
                continue
            p = (md.parent / ref).resolve()
            if p.is_file() and p.is_relative_to(md.parent.resolve()):
                z.write(p, p.relative_to(md.parent.resolve()).as_posix())
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--base", default="http://127.0.0.1:8010")
    ap.add_argument("--only", default="catalog,bank,lecture,project")
    a = ap.parse_args()
    only, base, root = set(a.only.split(",")), a.base.rstrip("/"), a.data_dir
    tasks: list[str] = []

    if "catalog" in only:
        print("课程目录：")
        t = submit(base, root / "课程介绍.md", "course_catalog")
        d = wait(base, t)
        print(f"  → {d['status']} {d['error'] or ''}")
    if "bank" in only:
        print("题库：")
        tasks.append(submit(base, root / "题目资料.md", "question_bank"))
    if "lecture" in only:
        print("课程讲义：")
        for f in sorted((root / "课程文档").glob("*.docx")):
            tasks.append(submit(base, f, "lecture", suggest(base, f.name, "lecture")))
    if "project" in only:
        print("项目文档：")
        for f in sorted((root / "项目文档").glob("*.docx")):
            tasks.append(submit(base, f, "project_doc", suggest(base, f.name, "project_doc")))
        tmp = Path(tempfile.mkdtemp(prefix="knowlearn_zip_"))
        for d in sorted(p for p in (root / "项目文档").iterdir() if p.is_dir()):
            for md in sorted(d.glob("*.md")):
                tasks.append(submit(base, zip_markdown(md, tmp), "project_doc", d.name))

    print(f"\n共提交 {len(tasks)} 个任务，等待完成……")
    failed = []
    for t in tasks:
        d = wait(base, t)
        st = d["stats"]
        print(f"  {d['status']:<9} {d['file_name']:<48} 切片 {st.get('chunk_count')}  图片 {st.get('image_count')}"
              f"（失败 {st.get('image_failed')}，缺失 {st.get('image_missing')}）  {d['error'] or ''}")
        if d["status"] == "failed":
            failed.append(d["file_name"])
    print(f"\n完成：成功 {len(tasks) - len(failed)}，失败 {len(failed)} {failed or ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
