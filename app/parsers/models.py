"""解析结果的数据结构，字段与 Milvus 集合 knowlearn_chunks 对应（数据库设计 3.1）。"""
from dataclasses import dataclass, field


class ParseError(ValueError):
    """资料内容不符合约定格式，导入任务应失败并展示此信息。"""


@dataclass
class ImageRef:
    url: str
    caption: str


@dataclass
class Chunk:
    content_type: str                 # course_intro / doc_chunk / project_material / question
    content: str                      # 给 LLM 看的正文
    embed_text: str                   # 用于向量化的文本（数据库设计 3.2）
    course_name: str | None = None
    project_name: str | None = None
    chapter: str | None = None
    series_code: str | None = None
    direction_code: str | None = None
    bank_name: str | None = None
    question_code: str | None = None
    question_type: str | None = None
    images: list[ImageRef] = field(default_factory=list)


@dataclass
class SubCourse:
    name: str
    code: str
    lessons: int          # 课时
    hours: str            # 学时（原样保留，如 16.00）
    description: str


@dataclass
class CourseSeries:
    name: str
    series_code: str
    direction_code: str
    level: str            # foundation / practice / advanced
    description: str      # 能力线 / 方向 / 班名
    direction_name: str   # 描述中的第二段，如「通用程序设计」
    category: str
    audience: str
    goals: str
    grades: str
    sub_courses: list[SubCourse]
    prerequisite: str = ""        # 派生
    project_practice: str = ""    # 派生
    bank_name: str = ""           # 派生
    bank_code: str = ""           # 派生


@dataclass
class Question:
    code: str
    bank_name: str
    bank_code: str
    direction_code: str
    question_type: str
    stem: str
    options: list[tuple[str, str]]   # [(A, 选项文字)]
    answer: str
    explanation: str
    course_names: list[str]          # 课程归属：同方向的 3 个系列名
