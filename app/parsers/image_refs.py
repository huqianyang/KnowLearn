"""Markdown 中图片引用的查找与替换（纯函数）。

支持两种写法：`![alt](src)`（MinerU 输出）和 `<img src="...">`（掌柜智库等手写 md）。
围栏代码块中的内容不处理（代码示例里的 ![...](...) 不是真实图片）。
"""
import re
from dataclasses import dataclass

# ![alt](src) 或 ![alt](<src> "title")
_RE_MD_IMG = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
# <img ... src="..." ...>
_RE_HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"'][^>]*>", re.I)
CONTEXT_CHARS = 200   # 给视觉模型的上下文：图片前后各取多少字


@dataclass
class ImageRefSpan:
    """一处图片引用在原文中的位置。"""

    start: int   # 起始字符位置（含）
    end: int     # 结束字符位置（不含）
    src: str     # 图片地址（相对路径或 URL）
    alt: str     # 原有的替代文字；HTML 写法为空


def find_image_refs(md: str) -> list[ImageRefSpan]:
    """找出全部图片引用，按出现位置排序。"""
    refs: list[ImageRefSpan] = []
    pos = 0          # 当前行在全文中的起始位置
    in_fence = False
    for line in md.splitlines(keepends=True):
        if line.strip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            for m in _RE_MD_IMG.finditer(line):
                refs.append(ImageRefSpan(pos + m.start(), pos + m.end(), m.group(2), m.group(1)))
            for m in _RE_HTML_IMG.finditer(line):
                refs.append(ImageRefSpan(pos + m.start(), pos + m.end(), m.group(1), ""))
        pos += len(line)
    return sorted(refs, key=lambda r: r.start)


def context_of(md: str, ref: ImageRefSpan, chars: int = CONTEXT_CHARS) -> tuple[str, str]:
    """图片前后各 chars 字的文字，去掉其他图片引用和多余空白。"""
    def clean(s: str) -> str:
        s = _RE_MD_IMG.sub("", s)
        s = _RE_HTML_IMG.sub("", s)
        return re.sub(r"\s+", " ", s).strip()
    # 先多取一倍的原文，清理后再截取 chars 字，保证清理掉图片和空白后仍有足够上下文
    return clean(md[max(0, ref.start - chars * 2):ref.start])[-chars:], clean(md[ref.end:ref.end + chars * 2])[:chars]


def safe_caption(text: str, limit: int = 120) -> str:
    """摘要会写进 ![摘要](url)，去掉会破坏语法的字符。"""
    t = re.sub(r"[\[\]\(\)\n\r]+", " ", text).strip()
    return t[:limit] or "图片"


def replace_refs(md: str, replacements: dict[int, str | None]) -> str:
    """replacements: ref.start → 新文本（None 表示删除该引用）。从后往前替换，位置不受影响。"""
    refs = {r.start: r for r in find_image_refs(md)}
    out = md
    for start in sorted(replacements, reverse=True):
        r = refs[start]
        out = out[:r.start] + (replacements[start] or "") + out[r.end:]
    return out
