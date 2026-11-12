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
    md = "A" * 50 + "![x](a.png)" + "B" * 30 + "![y](b.png)" + "C" * 10
    refs = find_image_refs(md)
    before, after = context_of(md, refs[1], chars=20)
    assert before == "B" * 20 and after == "C" * 10


def test_replace_refs_and_delete_missing():
    md = '开头 <img src="images/a.jpg" /> 中间 ![](images/missing.jpg) 结尾'
    refs = find_image_refs(md)
    out = replace_refs(md, {refs[0].start: "![架构图](http://minio/a.jpg)", refs[1].start: None})
    assert out == "开头 ![架构图](http://minio/a.jpg) 中间  结尾"


def test_safe_caption_strips_markdown_breakers():
    assert safe_caption("图片[1]展示了(登录)页面\n第二行") == "图片 1 展示了 登录 页面 第二行"
    assert safe_caption("  ") == "图片"


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


@pytest.mark.parametrize("files,ok", [
    ({"doc/a.md": b"# t", "doc/images/1.jpg": b"x"}, True),
    ({"a.md": b"# t", "b.md": b"# t"}, False),
    ({"images/1.jpg": b"x"}, False),
    ({"a.md": b"# t", "__MACOSX/._a.md": b"x"}, True),
])
def test_zip_must_contain_exactly_one_md(tmp_path, files, ok):
    p = tmp_path / "x.zip"
    p.write_bytes(_zip_bytes(files))
    if ok:
        ImportService._check_zip(p)
    else:
        with pytest.raises(BizError):
            ImportService._check_zip(p)


def test_safe_extract_blocks_path_traversal(tmp_path):
    data = _zip_bytes({"../evil.txt": b"x"})
    with zipfile.ZipFile(io.BytesIO(data)) as z, pytest.raises(MinerUError):
        safe_extract(z, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_milvus_quote_escapes():
    assert quote('a"b\\c.md') == '"a\\"b\\\\c.md"'
