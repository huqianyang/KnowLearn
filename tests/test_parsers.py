"""M2：解析器单元测试。fixtures 中的课程目录、题库为真实资料；mineru_*.md 为技术验证 S4 的真实转换结果。"""
from collections import Counter
from pathlib import Path

import pytest

from app.parsers.course_catalog import (course_chunks, direction_series_names, parse_course_catalog,
                                        render_course)
from app.parsers.filename import suggest_name
from app.parsers.markdown_splitter import split_markdown
from app.parsers.models import ParseError
from app.parsers.question_bank import parse_question_bank, question_chunks, render_question

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def catalog():
    return parse_course_catalog((FIX / "course_catalog.md").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def questions(catalog):
    return parse_question_bank((FIX / "question_bank.md").read_text(encoding="utf-8"),
                               direction_series_names(catalog))


# ---------------- 课程目录 ----------------

def test_catalog_counts(catalog):
    assert len(catalog) == 219
    assert Counter(s.level for s in catalog) == {"foundation": 73, "practice": 73, "advanced": 73}
    assert all(len(s.sub_courses) == 3 for s in catalog)


def test_catalog_fields_of_known_series(catalog):
    s = next(x for x in catalog if x.series_code == "general_purpose_programming_practice")
    assert s.name == "通用编程项目班"
    assert s.description == "计算机科学能力线 / 通用程序设计 / 通用编程项目班"
    assert s.audience == "在校生, 职场人, 求职者"
    assert s.grades == "专科, 本科"
    assert [(c.name, c.lessons, c.hours) for c in s.sub_courses] == [
        ("面向对象与常用库实践", 8, "16.00"), ("命令行工具与文件处理", 10, "20.00"), ("综合项目开发与讲评", 12, "24.00")]


def test_catalog_derived_prerequisite(catalog):
    by = {s.series_code: s for s in catalog}
    assert by["general_purpose_programming_foundation"].prerequisite == "无"
    assert by["general_purpose_programming_practice"].prerequisite == "通用编程入门班"
    assert by["general_purpose_programming_advanced"].prerequisite == "通用编程项目班"
    assert all(s.prerequisite and s.prerequisite != "资料未提供" for s in catalog)


def test_catalog_derived_project_practice(catalog):
    by = {s.series_code: s for s in catalog}
    assert by["general_purpose_programming_practice"].project_practice == "综合项目开发与讲评"
    # 入门班子课程都不含「项目/实战」
    assert by["general_purpose_programming_foundation"].project_practice == "资料未提供"


def test_catalog_bank_name_matches_question_bank(catalog, questions):
    bank_names = {q.bank_code: q.bank_name for q in questions}
    for s in catalog:
        assert bank_names[s.bank_code] == s.bank_name


def test_course_chunk_content(catalog):
    s = next(x for x in catalog if x.series_code == "general_purpose_programming_practice")
    text = render_course(s)
    for expect in ("先修要求：通用编程入门班", "项目实战内容：综合项目开发与讲评", "关联题库：通用程序设计题库",
                   "| 综合项目开发与讲评 | general_purpose_programming_practice_m3 | 12 | 24.00 |"):
        assert expect in text
    chunks = course_chunks(catalog)
    assert len(chunks) == 219 and all(c.content_type == "course_intro" and c.course_name for c in chunks)


def test_catalog_empty_grades_shown_as_not_provided(catalog):
    s = next(x for x in catalog if x.series_code == "first_line_management_foundation")
    assert s.grades == "资料未提供"
    assert sum(1 for x in catalog if x.grades == "资料未提供") == 36


def test_catalog_rejects_wrong_file():
    with pytest.raises(ParseError, match="总数声明"):
        parse_course_catalog("# 随便一个文档\n内容")


def test_catalog_count_mismatch():
    text = (FIX / "course_catalog.md").read_text(encoding="utf-8").replace("共 219 个", "共 220 个")
    with pytest.raises(ParseError, match="声明 220"):
        parse_course_catalog(text)


# ---------------- 题库 ----------------

def test_question_counts(questions):
    assert len(questions) == 1752
    assert len({q.bank_code for q in questions}) == 73
    types = Counter(q.question_type for q in questions)
    assert len(types) == 15
    assert types["单选题"] == 533 and types["多选题"] == 249 and types["判断题"] == 235


def test_question_known_item(questions):
    q = next(x for x in questions if x.code == "general_purpose_programming_bank_q004")
    assert q.question_type == "单选题"
    assert q.stem == "关于函数参数传递，以下说法更准确的是哪一项？"
    assert [k for k, _ in q.options] == ["A", "B", "C", "D"]
    assert q.options[1] == ("B", "函数参数用于向函数传入必要数据")
    assert q.answer == "B"
    assert q.bank_name == "通用程序设计题库"
    assert q.course_names == ["通用编程入门班", "通用编程项目班", "通用编程就业强化班"]


def test_question_multiline_with_code(questions):
    q = next(x for x in questions if x.code == "middle_high_school_informatics_bank_q001")
    assert q.question_type == "编程题"
    assert "```python" in q.stem and "print(x + y)" in q.stem
    assert q.answer == "5" and not q.options


def test_question_reading_passage_multiline(questions):
    q = next(x for x in questions if x.code == "postgraduate_english_bank_q001")
    assert "extended its opening hours" in q.stem and "What is the passage mainly about?" in q.stem
    assert len(q.options) == 4 and q.answer == "A"


def test_options_present_exactly_when_declared(questions):
    raw = (FIX / "question_bank.md").read_text(encoding="utf-8")
    assert sum(1 for q in questions if q.options) == raw.count("- **选项**")


def test_question_chunk(questions):
    chunks = question_chunks(questions)
    c = next(x for x in chunks if x.question_code == "general_purpose_programming_bank_q004")
    assert c.content_type == "question" and c.question_type == "单选题"
    assert c.course_name == "通用编程入门班 / 通用编程项目班 / 通用编程就业强化班"
    assert "答案：B" in c.content and "解析：" in c.content
    assert "答案" not in c.embed_text   # 向量化文本不含答案，避免干扰检索
    q = next(x for x in questions if x.code == "general_purpose_programming_bank_q004")
    assert "B. 函数参数用于向函数传入必要数据" in render_question(q)


def test_question_bank_requires_catalog():
    text = (FIX / "question_bank.md").read_text(encoding="utf-8")
    with pytest.raises(ParseError, match="请先导入课程目录"):
        parse_question_bank(text, {})


# ---------------- Markdown 切分 ----------------

def test_heading_path_uses_numbering_and_ignores_code_comments():
    md = """# 1 **概述**
## 1.1 **计算机的组成**
### 1.1.2 **硬件**
###### 1.1.2.1.1.1 运算器
运算器负责算术运算。
```python
# 这是注释不是标题
## 也不是标题
x = 1
```
### 1.1.3 **软件**
软件分为系统软件和应用软件。
"""
    chunks = split_markdown(md, "doc_chunk", course_name="Python", min_len=0)
    chapters = [c.chapter for c in chunks]
    assert chapters == ["1 概述 > 1.1 计算机的组成 > 1.1.2 硬件 > 1.1.2.1.1.1 运算器",
                        "1 概述 > 1.1 计算机的组成 > 1.1.3 软件"]
    assert "# 这是注释不是标题" in chunks[0].content and "x = 1" in chunks[0].content


def test_unnumbered_heading_nests_under_current():
    md = "# 1 环境\n## **Project venv:**\n内容A\n## **Base Conda:**\n内容B\n# 2 下一章\n内容C\n"
    chunks = split_markdown(md, "doc_chunk", course_name="Python", min_len=0)
    assert [c.chapter for c in chunks] == ["1 环境 > Project venv:", "1 环境 > Base Conda:", "2 下一章"]


def test_year_in_title_is_not_numbering():
    md = "# 1 历史\n## 2023年发布的新版本\n内容\n# 2 下一章\n内容\n"
    chunks = split_markdown(md, "doc_chunk", min_len=0)
    assert chunks[0].chapter == "1 历史 > 2023年发布的新版本"


def test_long_paragraph_split_by_sentence_with_overlap():
    sentences = [f"这是第{i}句话，用来测试长段落按句子切分的效果。" for i in range(80)]
    md = "# 1 长文\n" + "".join(sentences) + "\n"
    chunks = split_markdown(md, "doc_chunk", max_len=300, min_len=0)
    assert len(chunks) > 3
    assert all(len(c.content) <= 300 for c in chunks)
    # 相邻切片重叠 1 句
    last_sentence = chunks[0].content.split("。")[-2] + "。"
    assert chunks[1].content.startswith(last_sentence)


def test_code_block_and_table_not_split():
    code = "```python\n" + "\n".join(f"print({i})  # 第{i}行" for i in range(200)) + "\n```"
    table = "| a | b |\n| --- | --- |\n" + "\n".join(f"| {i} | {i * 2} |" for i in range(150))
    md = f"# 1 代码\n前言。\n\n{code}\n\n# 2 表格\n{table}\n"
    chunks = split_markdown(md, "doc_chunk", max_len=500, min_len=0)
    code_chunks = [c for c in chunks if "```python" in c.content]
    assert len(code_chunks) == 1 and code_chunks[0].content.rstrip().endswith("```")
    table_chunks = [c for c in chunks if c.content.startswith("| a | b |")]
    assert len(table_chunks) == 1 and "| 149 | 298 |" in table_chunks[0].content


def test_small_sections_merged_within_same_top_chapter():
    md = "# 1 A\n## 1.1 x\n短内容一。\n## 1.2 y\n短内容二。\n# 2 B\n## 2.1 z\n短内容三。\n"
    chunks = split_markdown(md, "doc_chunk", min_len=500)
    assert len(chunks) == 2
    assert chunks[0].chapter == "1 A" and "1.2 y\n短内容二" in chunks[0].content
    assert chunks[1].chapter == "2 B > 2.1 z"


def test_images_extracted():
    md = "# 1 图\n见下图：\n![Swagger 接口文档页面](http://h/knowlearn/a.jpg)\n说明文字。\n"
    c = split_markdown(md, "doc_chunk", min_len=0)[0]
    assert c.images[0].url == "http://h/knowlearn/a.jpg" and c.images[0].caption == "Swagger 接口文档页面"
    assert "[图片：Swagger 接口文档页面]" in c.content and "](" not in c.content


@pytest.mark.parametrize("name", ["mineru_fastapi_full.md", "mineru_python_full.md"])
def test_real_mineru_output(name):
    md = (FIX / name).read_text(encoding="utf-8")
    chunks = split_markdown(md, "doc_chunk", course_name="X")
    assert len(chunks) > 10
    # 只有第一个标题之前的封面信息（书名、作者、版本）没有章节
    assert all(c.chapter for c in chunks[1:])
    assert all(len(c.content) <= 15000 for c in chunks)
    oversize = [c for c in chunks if len(c.content) > 1000]
    # 只有不可切分的代码块/表格才会超过 1000 字
    assert all("```" in c.content or "|" in c.content or "<table" in c.content for c in oversize)
    # 原文中的图片都被保留下来
    assert sum(len(c.images) for c in chunks) == md.count("![")
    # 代码块中的 # 注释没有变成章节
    assert not any(" > #" in c.chapter or c.chapter.startswith("#") for c in chunks if c.chapter)


# ---------------- 文件名默认值 ----------------

@pytest.mark.parametrize("filename,doc_type,key,expected", [
    ("尚硅谷大模型技术之Python1.0.docx", "lecture", "course_name", "Python"),
    ("尚硅谷大模型技术之FastAPI&SQLAlchemy1.0.docx", "lecture", "course_name", "FastAPI&SQLAlchemy"),
    ("尚硅谷大模型技术之大模型概述v1.1.7.docx", "lecture", "course_name", "大模型概述"),
    ("尚硅谷大模型技术之NLP1.0.9.docx", "lecture", "course_name", "NLP"),
    ("尚硅谷大模型技术之Hugging Face生态.docx", "lecture", "course_name", "Hugging Face生态"),
    ("尚硅谷大模型技术之Linux（Ubuntu）1.0.docx", "lecture", "course_name", "Linux（Ubuntu）"),
    ("尚硅谷大模型技术之智图寻宝2.0.0.docx", "project_doc", "project_name", "智图寻宝"),
    ("readme.PDF", "project_doc", "project_name", "readme"),
])
def test_suggest_name(filename, doc_type, key, expected):
    assert suggest_name(filename, doc_type)[key] == expected


def test_suggest_name_for_structured_types():
    assert suggest_name("课程介绍.md", "course_catalog") == {"course_name": None, "project_name": None}
