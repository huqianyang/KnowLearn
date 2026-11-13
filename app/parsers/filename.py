"""根据文件名生成默认课程名 / 项目名（API A1 的解析规则）。

例：「尚硅谷大模型技术之NLP1.0.9.docx」→ 课程名「NLP」。
"""
import re

PREFIXES = ("尚硅谷大模型技术之",)                      # 需要去掉的固定前缀
_RE_EXT = re.compile(r"\.(md|docx|pdf)$", re.I)          # 扩展名
_RE_VERSION = re.compile(r"\s*[vV]?\d+(?:\.\d+)+$")      # 结尾的版本号，如 1.0.9、v2.0


def suggest_name(filename: str, doc_type: str) -> dict[str, str | None]:
    """返回 {"course_name": ..., "project_name": ...}。

    讲义（lecture）填 course_name，项目文档（project_doc）填 project_name；
    其他类型（课程目录、题库）不需要名称，两项都为 None。结果最长 100 字。
    """
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
