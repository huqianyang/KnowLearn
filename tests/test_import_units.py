"""M3：图片引用处理、zip 校验、Milvus 表达式转义。不依赖中间件。"""
import io
import zipfile

import pytest

from app.core.errors import BizError
from app.infra.mineru import MinerUError, safe_extract
from app.infra.vector_store import quote
from app.parsers.image_refs import context_of, find_image_refs, replace_refs, safe_caption
from app.services.import_service import ImportService


def test_find_md_and_html_images_skip_code():
    """同时识别 HTML 和 Markdown 两种图片写法，代码块中的图片语法不算。"""
    md = (
        "前文介绍架构。\n"
        '<img src="images/1.整体架构图.jpg" style="zoom: 67%;" />\n'
        "中间文字\n"
        "![](images/abc.jpg)\n"
        "```python\n"
        'print("![示例](images/not_real.png)")\n'
        "```\n"
        "![外链](http://example.com/a.png)\n"
    )
    refs = find_image_refs(md)
    assert [r.src for r in refs] == ["images/1.整体架构图.jpg", "images/abc.jpg", "http://example.com/a.png"]


def test_context_excludes_other_images():
    """图片上下文中去掉了其他图片的引用。"""
    md = "A" * 50 + "![x](a.png)" + "B" * 30 + "![y](b.png)" + "C" * 10
    refs = find_image_refs(md)
    before, after = context_of(md, refs[1], chars=20)
    assert before == "B" * 20 and after == "C" * 10


def test_replace_refs_and_delete_missing():
    """引用替换为新的图片语法；值为 None 的引用被删除。"""
    md = '开头 <img src="images/a.jpg" /> 中间 ![](images/missing.jpg) 结尾'
    refs = find_image_refs(md)
    out = replace_refs(md, {refs[0].start: "![架构图](http://minio/a.jpg)", refs[1].start: None})
    assert out == "开头 ![架构图](http://minio/a.jpg) 中间  结尾"


def test_safe_caption_strips_markdown_breakers():
    """摘要中的方括号、圆括号、换行被替换为空格；空摘要用「图片」代替。"""
    assert safe_caption("图片[1]展示了(登录)页面\n第二行") == "图片 1 展示了 登录 页面 第二行"
    assert safe_caption("  ") == "图片"


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    """在内存中生成 zip：{压缩包内路径: 内容}。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


@pytest.mark.parametrize("files,ok", [
    ({"doc/a.md": b"# t", "doc/images/1.jpg": b"x"}, True),     # 1 个 md + 图片
    ({"a.md": b"# t", "b.md": b"# t"}, False),                   # 多个 md
    ({"images/1.jpg": b"x"}, False),                             # 没有 md
    ({"a.md": b"# t", "__MACOSX/._a.md": b"x"}, True),           # macOS 附带文件不计入
])
def test_zip_must_contain_exactly_one_md(tmp_path, files, ok):
    """zip 必须恰好包含 1 个 md 文件。"""
    p = tmp_path / "x.zip"
    p.write_bytes(_zip_bytes(files))
    if ok:
        ImportService._check_zip(p)
    else:
        with pytest.raises(BizError):
            ImportService._check_zip(p)


def test_safe_extract_blocks_path_traversal(tmp_path):
    """含 ../ 的压缩包拒绝解压，目标目录外不会写出文件。"""
    data = _zip_bytes({"../evil.txt": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as z, pytest.raises(MinerUError):
        safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_milvus_quote_escapes():
    """过滤表达式中的引号和反斜杠被正确转义。"""
    assert quote('a"b\\c.md') == '"a\\"b\\\\c.md"'
