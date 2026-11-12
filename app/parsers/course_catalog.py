"""课程目录（课程介绍.md）解析 + 派生字段（PRD F1-3 补充规则）。

文件结构：
    # 课程
    共 N 个课程系列
    ## <系列名>
    - **系列编码**: xxx_foundation|practice|advanced
    - **描述**: 能力线 / 方向 / 班名
    - **课程分类**: ...
    - **适合人群**: ...
    - **学习目标**: ...
    - **适合年级**: ...
    ### 课程
    - **<子课程名>**
      - 编码: xxx_m1, 课时: 8, 学时: 16.00
      - 描述: ...
"""
import re

from app.parsers.models import Chunk, CourseSeries, ParseError, SubCourse

LEVELS = ("foundation", "practice", "advanced")
PREREQ_OF = {"foundation": None, "practice": "foundation", "advanced": "practice"}
PROJECT_KEYWORDS = ("项目", "实战")
NOT_PROVIDED = "资料未提供"

_FIELDS = {"系列编码": "series_code", "描述": "description", "课程分类": "category",
           "适合人群": "audience", "学习目标": "goals", "适合年级": "grades"}
_OPTIONAL = {"grades"}
_RE_TOTAL = re.compile(r"共\s*(\d+)\s*个课程系列")
_RE_FIELD = re.compile(r"^- \*\*(.+?)\*\*[:：]\s*(.*)$")
_RE_SUB_NAME = re.compile(r"^- \*\*(.+?)\*\*\s*$")
_RE_SUB_META = re.compile(r"^\s+- 编码[:：]\s*(\S+?),\s*课时[:：]\s*(\d+),\s*学时[:：]\s*([\d.]+)\s*$")
_RE_SUB_DESC = re.compile(r"^\s+- 描述[:：]\s*(.*)$")


def parse_course_catalog(text: str) -> list[CourseSeries]:
    m = _RE_TOTAL.search(text)
    if not m:
        raise ParseError("课程目录缺少总数声明（如「共 219 个课程系列」），请确认上传的是课程目录文件")
    declared = int(m.group(1))

    blocks = re.split(r"^## ", text, flags=re.M)[1:]
    series = [_parse_series(b) for b in blocks]
    if len(series) != declared:
        raise ParseError(f"课程系列数量不一致：文件声明 {declared} 个，实际解析出 {len(series)} 个")
    codes = [s.series_code for s in series]
    if len(set(codes)) != len(codes):
        raise ParseError("课程目录中存在重复的系列编码")
    _derive(series)
    return series


def _parse_series(block: str) -> CourseSeries:
    lines = block.splitlines()
    name = lines[0].strip()
    values: dict[str, str] = {}
    subs: list[SubCourse] = []
    in_courses = False
    cur: dict | None = None
    for line in lines[1:]:
        if line.startswith("### "):
            in_courses = True
            continue
        if not line.strip():
            continue
        if not in_courses:
            fm = _RE_FIELD.match(line)
            if fm and fm.group(1) in _FIELDS:
                values[_FIELDS[fm.group(1)]] = fm.group(2).strip()
            continue
        if (sm := _RE_SUB_NAME.match(line)):
            cur = {"name": sm.group(1).strip()}
            subs.append(cur)  # type: ignore[arg-type]
        elif cur is not None and (mm := _RE_SUB_META.match(line)):
            cur.update(code=mm.group(1), lessons=int(mm.group(2)), hours=mm.group(3))
        elif cur is not None and (dm := _RE_SUB_DESC.match(line)):
            cur["description"] = dm.group(1).strip()

    # 「适合年级」允许为空：36 个职场类系列（如班组长带人训练营）原始数据就是空值，显示为「资料未提供」
    missing = [k for k in _FIELDS.values() if k not in values or (k not in _OPTIONAL and not values[k])]
    if missing:
        raise ParseError(f"课程系列「{name}」缺少字段：{', '.join(missing)}")
    code = values["series_code"]
    direction, _, level = code.rpartition("_")
    if level not in LEVELS or not direction:
        raise ParseError(f"课程系列「{name}」的系列编码 {code} 不是以 _foundation/_practice/_advanced 结尾")
    sub_courses = []
    for s in subs:
        if "code" not in s:
            raise ParseError(f"课程系列「{name}」的子课程「{s['name']}」缺少编码/课时/学时")
        sub_courses.append(SubCourse(name=s["name"], code=s["code"], lessons=s["lessons"],
                                     hours=s["hours"], description=s.get("description", "")))
    if not sub_courses:
        raise ParseError(f"课程系列「{name}」没有子课程")
    parts = [p.strip() for p in values["description"].split("/")]
    return CourseSeries(
        name=name, series_code=code, direction_code=direction, level=level,
        description=values["description"], direction_name=parts[1] if len(parts) > 1 else parts[0],
        category=values["category"], audience=values["audience"], goals=values["goals"],
        grades=values["grades"] or NOT_PROVIDED, sub_courses=sub_courses,
    )


def _derive(series: list[CourseSeries]) -> None:
    by_code = {s.series_code: s for s in series}
    for s in series:
        pre_level = PREREQ_OF[s.level]
        if pre_level is None:
            s.prerequisite = "无"
        else:
            pre = by_code.get(f"{s.direction_code}_{pre_level}")
            s.prerequisite = pre.name if pre else NOT_PROVIDED
        projects = [c.name for c in s.sub_courses if any(k in c.name for k in PROJECT_KEYWORDS)]
        s.project_practice = "、".join(projects) if projects else NOT_PROVIDED
        s.bank_code = f"{s.direction_code}_bank"
        s.bank_name = f"{s.direction_name}题库"


def direction_series_names(series: list[CourseSeries]) -> dict[str, list[str]]:
    """方向编码 → 该方向按 入门/项目/强化 顺序排列的系列名，供题库解析确定课程归属。"""
    order = {lv: i for i, lv in enumerate(LEVELS)}
    out: dict[str, list[CourseSeries]] = {}
    for s in series:
        out.setdefault(s.direction_code, []).append(s)
    return {d: [x.name for x in sorted(v, key=lambda x: order[x.level])] for d, v in out.items()}


def render_course(s: CourseSeries) -> str:
    rows = "\n".join(f"| {c.name} | {c.code} | {c.lessons} | {c.hours} | {c.description} |" for c in s.sub_courses)
    return (
        f"## {s.name}\n"
        f"- 系列编码：{s.series_code}\n"
        f"- 课程定位：{s.description}\n"
        f"- 课程分类：{s.category}\n"
        f"- 适合人群：{s.audience}\n"
        f"- 学习目标：{s.goals}\n"
        f"- 适合年级：{s.grades}\n"
        f"- 先修要求：{s.prerequisite}\n"
        f"- 项目实战内容：{s.project_practice}\n"
        f"- 关联题库：{s.bank_name}\n\n"
        f"### 章节结构\n| 子课程 | 编码 | 课时 | 学时 | 描述 |\n| --- | --- | --- | --- | --- |\n{rows}"
    )


def course_chunks(series: list[CourseSeries]) -> list[Chunk]:
    return [
        Chunk(
            content_type="course_intro",
            content=render_course(s),
            embed_text="；".join([s.name, s.description, s.category, f"适合人群：{s.audience}",
                                 f"学习目标：{s.goals}", "子课程：" + "、".join(c.name for c in s.sub_courses)]),
            course_name=s.name, series_code=s.series_code, direction_code=s.direction_code,
        )
        for s in series
    ]
