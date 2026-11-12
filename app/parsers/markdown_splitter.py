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

SEP = " > "
_RE_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_RE_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$)")   # 编号后必须是空白，避免把「2023年」当成编号
_RE_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
_RE_SENT_END = re.compile(r"(?<=[。！？；!?;])|(?<=\.)(?=\s)")
HARD_MAX = 15000   # Milvus VARCHAR 65535 字节，按 UTF-8 中文 3 字节留余量


@dataclass
class _Heading:
    level: int
    number: tuple[int, ...] | None
    title: str


@dataclass
class _Section:
    path: list[str]
    blocks: list[str] = field(default_factory=list)


@dataclass
class _Piece:
    path: list[str]
    text: str


def clean_title(raw: str) -> str:
    t = re.sub(r"\*\*|__|`", "", raw).strip()
    return re.sub(r"\s+", " ", t)


def _parse_number(title: str) -> tuple[int, ...] | None:
    m = _RE_NUMBER.match(title)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def _sections(md: str) -> list[_Section]:
    stack: list[_Heading] = []
    sections: list[_Section] = [_Section(path=[])]
    buf: list[str] = []
    in_fence = False

    def flush_block():
        if buf:
            sections[-1].blocks.append("\n".join(buf).strip("\n"))
            buf.clear()

    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
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
    return [s for s in _RE_SENT_END.split(text) if s and s.strip()]


def _split_long_paragraph(p: str, max_len: int, overlap: int) -> list[str]:
    sents = _split_sentences(p)
    if len(sents) <= 1:
        return [p[i:i + max_len] for i in range(0, len(p), max_len)]
    out, cur = [], []
    for s in sents:
        if cur and len("".join(cur)) + len(s) > max_len:
            out.append("".join(cur).strip())
            cur = cur[-overlap:] if overlap else []
            if len("".join(cur)) + len(s) > max_len:
                cur = []
        cur.append(s)
    if cur:
        out.append("".join(cur).strip())
    return out


def _is_atomic(block: str) -> bool:
    b = block.lstrip()
    return b.startswith(("```", "|", "<table"))   # MinerU 把表格输出为 HTML <table>


def _pack(section: _Section, max_len: int, overlap: int) -> list[_Piece]:
    pieces: list[_Piece] = []
    cur: list[str] = []

    def emit():
        if cur:
            pieces.append(_Piece(section.path, "\n\n".join(cur)))
            cur.clear()

    for block in section.blocks:
        if len(block) > max_len:
            emit()
            if _is_atomic(block):
                for i in range(0, len(block), HARD_MAX):
                    pieces.append(_Piece(section.path, block[i:i + HARD_MAX]))
            else:
                pieces.extend(_Piece(section.path, t) for t in _split_long_paragraph(block, max_len, overlap))
            continue
        if cur and len("\n\n".join(cur)) + 2 + len(block) > max_len:
            emit()
        cur.append(block)
    emit()
    return pieces


def _common_prefix(a: list[str], b: list[str]) -> list[str]:
    out = []
    for x, y in zip(a, b):
        if x != y:
            break
        out.append(x)
    return out


def _merge_small(pieces: list[_Piece], min_len: int, max_len: int) -> list[_Piece]:
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
    images: list[ImageRef] = []

    def repl(m: re.Match) -> str:
        caption = m.group(1).strip()
        images.append(ImageRef(url=m.group(2), caption=caption))
        return f"[图片：{caption}]" if caption else "[图片]"

    return _RE_IMAGE.sub(repl, text), images


def split_markdown(md: str, content_type: str, course_name: str | None = None, project_name: str | None = None,
                   max_len: int = 1000, min_len: int = 500, overlap: int = 1) -> list[Chunk]:
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
        embed = "\n".join(x for x in (owner, chapter, text) if x)
        chunks.append(Chunk(content_type=content_type, content=text, embed_text=embed,
                            course_name=course_name, project_name=project_name,
                            chapter=chapter, images=images))
    return chunks
