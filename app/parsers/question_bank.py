"""题库（题目资料.md）解析。

文件结构：
    共 M 个题库，N 个题目
    ## <题库名>
    - 题库编码: <方向编码>_bank
    ### <题目编码>
    - **题型**: 单选题
    - **题干**: ...（可能多行，可能含代码块）
    - **选项**:            （部分题型没有）
    A. ...
    - **答案**: ...（可能多行）
    - **解析**: ...（可能多行）

课程归属依赖课程目录（技术方案 T6）：由调用方传入 方向编码 → 系列名列表。
"""
import re

from app.parsers.models import Chunk, ParseError, Question

_RE_TOTAL = re.compile(r"共\s*(\d+)\s*个题库[，,]\s*(\d+)\s*个题目")
_RE_BANK_CODE = re.compile(r"^- 题库编码[:：]\s*(\S+)\s*$", re.M)
_RE_FIELD = re.compile(r"^- \*\*(题型|题干|选项|答案|解析)\*\*[:：]\s?(.*)$")
_RE_OPTION = re.compile(r"^([A-Z])[.．、]\s*(.*)$")
_REQUIRED = ("题型", "题干", "答案", "解析")


def parse_question_bank(text: str, direction_series: dict[str, list[str]]) -> list[Question]:
    m = _RE_TOTAL.search(text)
    if not m:
        raise ParseError("题库文件缺少总数声明（如「共 73 个题库，1752 个题目」），请确认上传的是题库文件")
    declared_banks, declared_questions = int(m.group(1)), int(m.group(2))

    bank_blocks = re.split(r"^## ", text, flags=re.M)[1:]
    if len(bank_blocks) != declared_banks:
        raise ParseError(f"题库数量不一致：文件声明 {declared_banks} 个，实际解析出 {len(bank_blocks)} 个")

    questions: list[Question] = []
    missing_dirs: list[str] = []
    for block in bank_blocks:
        bank_name = block.splitlines()[0].strip()
        cm = _RE_BANK_CODE.search(block)
        if not cm:
            raise ParseError(f"题库「{bank_name}」缺少题库编码")
        bank_code = cm.group(1)
        direction = bank_code.removesuffix("_bank")
        courses = direction_series.get(direction)
        if not courses:
            missing_dirs.append(bank_name)
            continue
        for qblock in re.split(r"^### ", block, flags=re.M)[1:]:
            questions.append(_parse_question(qblock, bank_name, bank_code, direction, courses))

    if missing_dirs:
        raise ParseError(f"请先导入课程目录：以下题库找不到对应的课程方向 {missing_dirs[:5]}"
                         f"{' 等' if len(missing_dirs) > 5 else ''}")
    if len(questions) != declared_questions:
        raise ParseError(f"题目数量不一致：文件声明 {declared_questions} 个，实际解析出 {len(questions)} 个")
    codes = [q.code for q in questions]
    if len(set(codes)) != len(codes):
        raise ParseError("题库中存在重复的题目编码")
    return questions


def _parse_question(qblock: str, bank_name: str, bank_code: str, direction: str, courses: list[str]) -> Question:
    lines = qblock.splitlines()
    code = lines[0].strip()
    fields: dict[str, list[str]] = {}
    current: str | None = None
    in_fence = False
    for line in lines[1:]:
        if line.strip().startswith("```"):
            in_fence = not in_fence
        fm = None if in_fence else _RE_FIELD.match(line)
        if fm:
            current = fm.group(1)
            fields[current] = [fm.group(2)] if fm.group(2) else []
        elif current is not None:
            fields[current].append(line)

    def text_of(key: str) -> str:
        return "\n".join(fields.get(key, [])).strip()

    missing = [k for k in _REQUIRED if not text_of(k)]
    if missing:
        raise ParseError(f"题目 {code} 缺少字段：{', '.join(missing)}")

    options: list[tuple[str, str]] = []
    for line in fields.get("选项", []):
        if (om := _RE_OPTION.match(line.strip())):
            options.append((om.group(1), om.group(2).strip()))
        elif line.strip() and options:          # 选项文字换行
            label, txt = options[-1]
            options[-1] = (label, f"{txt}\n{line.strip()}")
    if "选项" in fields and not options:
        raise ParseError(f"题目 {code} 有「选项」字段但没有解析出任何选项")

    return Question(code=code, bank_name=bank_name, bank_code=bank_code, direction_code=direction,
                    question_type=text_of("题型"), stem=text_of("题干"), options=options,
                    answer=text_of("答案"), explanation=text_of("解析"), course_names=list(courses))


def render_question(q: Question) -> str:
    parts = [f"【{q.bank_name}】{q.code}", f"题型：{q.question_type}", f"题干：{q.stem}"]
    if q.options:
        parts.append("选项：\n" + "\n".join(f"{k}. {v}" for k, v in q.options))
    parts += [f"答案：{q.answer}", f"解析：{q.explanation}", f"课程归属：{' / '.join(q.course_names)}"]
    return "\n".join(parts)


def question_chunks(questions: list[Question]) -> list[Chunk]:
    return [
        Chunk(
            content_type="question",
            content=render_question(q),
            embed_text="\n".join([q.bank_name, q.question_type, q.stem]
                                 + [f"{k}. {v}" for k, v in q.options]),
            course_name=" / ".join(q.course_names), direction_code=q.direction_code,
            bank_name=q.bank_name, question_code=q.code, question_type=q.question_type,
        )
        for q in questions
    ]
