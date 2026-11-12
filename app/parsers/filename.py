"""根据文件名生成默认课程名 / 项目名（API A1 的解析规则）。"""
import re

PREFIXES = ("尚硅谷大模型技术之",)
_RE_EXT = re.compile(r"\.(md|docx|pdf)$", re.I)
_RE_VERSION = re.compile(r"\s*[vV]?\d+(?:\.\d+)+$")


def suggest_name(filename: str, doc_type: str) -> dict[str, str | None]:
    result: dict[str, str | None] = {"course_name": None, "project_name": None}
    if doc_type not in ("lecture", "project_doc"):
        return result
    name = _RE_EXT.sub("", filename.strip())
    for p in PREFIXES:
        name = name.removeprefix(p)
    name = _RE_VERSION.sub("", name).strip()
    key = "course_name" if doc_type == "lecture" else "project_name"
    result[key] = name[:100] or None
    return result
