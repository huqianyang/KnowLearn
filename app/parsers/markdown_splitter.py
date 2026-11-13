"""讲义 / 项目文档的 Markdown 切分（技术方案 4.3，决策 T3）。

规则：
1. 跳过围栏代码块（```）内的内容，代码里的 `# 注释` 不会被当成标题（S4 发现）。
2. 章节路径以标题编号为准：MinerU 会把深层标题统一标成 ######，且编号可能跳级
   （如 `### 1.1.2` 下直接出现 `###### 1.1.2.1.1.1`），因此父章节 = 栈中编号是其前缀的最近标题。
   无编号标题挂在当前栈顶之下，按 # 数量与同级的无编号标题互相替换。
3. 每个章节的正文按块（段落 / 代码块 / 表格 / 图片）装箱，最长 max_len 字；
   代码块和表格不从中间切开（超长时整体成为一个切片）；超长段落按句子切分，相邻切片重叠 overlap 句。
4. 同一顶级章节内，不足 min_len 字的相邻切片合并（合并后不超过 max_len），章节路径取公共前缀。
5. 图片 `![摘要](url)` 抽取到 images，正文中替换为「[图片：摘要]」。
"""
import re
from dataclasses import dataclass, field

from app.parsers.models import Chunk, ImageRef

SEP = " > "   # 章节路径分隔符
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RE_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$)")   # 编号后必须是空白，避免把「2023年」当成编号
_RE_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_RE_SENT_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.)(?=\s)")   # 句末位置（中文标点后，或英文句点加空白处）
HARD_MAX = 15000   # Milvus VARCHAR 65535 字节，按 UTF-8 中文 3 字节留余量


@dataclass
class _Heading:
    """标题栈中的一项。"""

    level: int                          # # 的数量
    number: tuple[int, ...] | None      # 标题编号，如 (1, 2, 3)；无编号为 None
    title: str


@dataclass
class _Section:
    """一个章节：完整章节路径 + 正文块。"""

    path: list[str]
    blocks: list[str] = field(default_factory=list)


@dataclass
class _Piece:
    """切分中间结果：章节路径 + 文本。"""

    path: list[str]
    text: str


def clean_title(raw: str) -> str:
    """去掉标题中的加粗、行内代码等标记，合并空白。"""
    t = re.sub(r"\*\*|__|`", "", raw).strip()
    return re.sub(r"\s+", " ", t)


def _parse_number(title: str) -> tuple[int, ...] | None:
    """提取标题开头的编号：「1.2.3 xxx」→ (1, 2, 3)。"""
    m = _RE_NUMBER.match(title)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def _sections(md: str) -> list[_Section]:
    """按标题把全文分成章节，每个章节的正文再按空行、代码块、表格分成块。只返回有正文的章节。"""
    stack: list[_Heading] = []                  # 当前标题路径
    sections: list[_Section] = [_Section(path=[])]   # 第一个是第一个标题之前的前言
    buf: list[str] = []                         # 正在累积的块
    in_fence = False

    def flush_block():
        """把累积的行作为一个块写入当前章节。"""
        if buf:
            sections[-1].blocks.append("\n".join(buf).strip("\n"))
            buf.clear()

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            # 代码块整体成为一个块：开始前先结束上一个块，结束时立即输出
            if not in_fence:
                flush_block()
            buf.append(line)
            in_fence = not in_fence
            if not in_fence:
                flush_block()
            continue
        if in_fence:
            buf.append(line)
            continue
        hm = _RE_HEADING.match(line)
        if hm:
            flush_block()
            title = clean_title(hm.group(2))
            if not title:
                continue
            h = _Heading(level=len(hm.group(1)), number=_parse_number(title), title=title)
            _push(stack, h)
            sections.append(_Section(path=[x.title for x in stack]))
            continue
        if not stripped:
            flush_block()
            continue
        # 表格行连续成块；表格与普通段落之间断开
        if buf and (stripped.startswith("|") != buf[-1].strip().startswith("|")):
            flush_block()
        buf.append(line)
    flush_block()
    return [s for s in sections if s.blocks]


def _push(stack: list[_Heading], h: _Heading) -> None:
    """把新标题放入标题栈，先弹出不是它祖先的标题。

    - 有编号：一直弹，直到栈顶编号是新编号的真前缀（例如 1.2 是 1.2.3 的父章节）；
    - 无编号：只弹出级别不高于它的无编号标题，有编号的标题保留为父章节。
    """
    if h.number is not None:
        while stack:
            top = stack[-1]
            if top.number is not None and len(top.number) < len(h.number) and h.number[:len(top.number)] == top.number:
                break
            if top.number is None:
                stack.pop()
                continue
            stack.pop()
    else:
        while stack and stack[-1].number is None and stack[-1].level >= h.level:
            stack.pop()
    stack.append(h)


def _split_sentences(text: str) -> list[str]:
    """按句末标点切成句子（保留标点）。"""
    return [s for s in _RE_SENT_END.split(text) if s and s.strip()]


def _split_long_paragraph(p: str, max_len: int, overlap: int) -> list[str]:
    """把超长段落按句子装箱成多段，每段不超过 max_len，相邻两段重叠 overlap 句。

    只有一句时（没有句末标点）按字数硬切。
    """
    sents = _split_sentences(p)
    if len(sents) <= 1:
        return [p[i:i + max_len] for i in range(0, len(p), max_len)]
    out, cur = [], []
    for s in sents:
        if cur and len("".join(cur)) + len(s) > max_len:
            out.append("".join(cur).strip())
            # 下一段以上一段的最后 overlap 句开头，保持上下文连贯
            cur = cur[-overlap:] if overlap else []
            # 重叠句加上当前句仍然超长时，放弃重叠
            if len("".join(cur)) + len(s) > max_len:
                cur = []
        cur.append(s)
    if cur:
        out.append("".join(cur).strip())
    return out


def _is_atomic(block: str) -> bool:
    """代码块和表格不能从中间切开。"""
    b = block.lstrip()
    return b.startswith(("```", "|", "<table"))   # MinerU 把表格输出为 HTML <table>


def _pack(section: _Section, max_len: int, overlap: int) -> list[_Piece]:
    """把一个章节的块依次装箱，每箱不超过 max_len 字。"""
    pieces: list[_Piece] = []
    cur: list[str] = []

    def emit():
        """当前箱子输出为一个切片。"""
        if cur:
            pieces.append(_Piece(section.path, "\n\n".join(cur)))
            cur.clear()

    for block in section.blocks:
        if len(block) > max_len:
            # 超长块单独处理：代码块 / 表格整体保留（只在超过 HARD_MAX 时硬切），段落按句子切
            emit()
            if _is_atomic(block):
                for i in range(0, len(block), HARD_MAX):
                    pieces.append(_Piece(section.path, block[i:i + HARD_MAX]))
            else:
                pieces.extend(_Piece(section.path, t) for t in _split_long_paragraph(block, max_len, overlap))
            continue
        # 放不下就先输出当前箱子（+2 是块之间的空行）
        if cur and len("\n\n".join(cur)) + 2 + len(block) > max_len:
            emit()
        cur.append(block)
    emit()
    return pieces


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    """两个章节路径的公共前缀。"""
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def _merge_small(pieces: list[_Piece], min_len: int, max_len: int) -> list[_Piece]:
    """把过短的切片和后一个切片合并，减少信息量过少的碎片。只在同一顶级章节内合并。"""
    out: list[_Piece] = []
    for p in pieces:
        if out:
            last = out[-1]
            if last.path[:1] == p.path[:1] and len(last.text) < min_len:
                prefix = _common_prefix(last.path, p.path) or last.path[:1]
                heading = SEP.join(p.path[len(prefix):])   # 合并后保留子章节标题，长度计入上限
                body = f"{heading}\n{p.text}" if heading else p.text
                if len(last.text) + 2 + len(body) <= max_len:
                    out[-1] = _Piece(prefix, f"{last.text}\n\n{body}")
                    continue
        out.append(p)
    return out


def _extract_images(text: str) -> tuple[str, list[ImageRef]]:
    """把正文中的图片引用替换为文字占位，并返回图片列表（供回答中展示）。"""
    images: list[ImageRef] = []

    def repl(m: re.Match) -> str:
        caption = m.group(1).strip()
        images.append(ImageRef(url=m.group(2), caption=caption))
        return f"[图片：{caption}]" if caption else "[图片]"

    return _RE_IMAGE.sub(repl, text), images


def split_markdown(md: str, content_type: str, course_name: str | None = None, project_name: str | None = None,
                   max_len: int = 1000, min_len: int = 500, overlap: int = 1) -> list[Chunk]:
    """切分讲义或项目文档。

    Args:
        md: Markdown 全文（图片已替换成 ![摘要](公开地址)）。
        content_type: doc_chunk（讲义）或 project_material（项目文档）。
        course_name / project_name: 所属课程或项目，写入切片并拼进向量化文本。
        max_len: 单个切片最大字数。
        min_len: 小于该字数的切片尝试与后一个合并。
        overlap: 长段落切开时相邻切片重叠的句数。
    """
    if content_type not in ("doc_chunk", "project_material"):
        raise ValueError(f"split_markdown 不支持内容类型 {content_type}")
    pieces: list[_Piece] = []
    for sec in _sections(md):
        pieces.extend(_pack(sec, max_len, overlap))
    pieces = _merge_small(pieces, min_len, max_len)

    owner = course_name or project_name or ""
    chunks: list[Chunk] = []
    for p in pieces:
        text, images = _extract_images(p.text)
        if not text.strip() and not images:
            continue
        chapter = SEP.join(p.path) or None
        # 向量化文本带上课程 / 项目名和章节路径，让「FastAPI 的路由」这类问题能匹配到所属资料
        embed = "\n".join(x for x in (owner, chapter, text) if x)
        chunks.append(Chunk(content_type=content_type, content=text, embed_text=embed,
                            course_name=course_name, project_name=project_name,
                            chapter=chapter, images=images))
    return chunks
