"""MinIO 对象存储：桶初始化（公开只读）、上传、删除前缀、生成访问地址（数据库设计第 5 章）。"""
import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import quote

from minio import Minio
from minio.deleteobjects import DeleteObject

logger = logging.getLogger(__name__)


class Storage:
    """MinIO 客户端封装。

    Args:
        endpoint: MinIO 地址（host:port）。
        access_key / secret_key: 账号。
        bucket: 桶名。
        secure: 是否使用 HTTPS。
        public_base: 浏览器访问文件时使用的地址前缀。
    """

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool, public_base: str):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")

    def ensure_bucket(self) -> None:
        """桶不存在时创建，并设置为公开只读，让前端能直接显示图片、打开原文件。"""
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("已创建 MinIO 桶 %s", self.bucket)
        # S3 桶策略：允许任何人读取（GetObject）桶内对象，不允许写入和列目录
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{self.bucket}/*"],
            }],
        }
        self.client.set_bucket_policy(self.bucket, json.dumps(policy))
        logger.info("MinIO 桶 %s 已设置为公开只读", self.bucket)

    def upload_file(self, object_key: str, local_path: Path) -> str:
        """上传本地文件，返回公开访问地址。Content-Type 按扩展名推断，浏览器才能直接显示图片。"""
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        self.client.fput_object(self.bucket, object_key, str(local_path), content_type=content_type)
        return self.public_url(object_key)

    def delete_prefix(self, prefix: str) -> int:
        """删除指定前缀下的全部对象（覆盖导入、导入失败清理时使用），返回成功删除的数量。"""
        objs = [DeleteObject(o.object_name) for o in self.client.list_objects(self.bucket, prefix=prefix, recursive=True)]
        if not objs:
            return 0
        # remove_objects 是惰性的，必须遍历结果才会真正执行删除
        errors = list(self.client.remove_objects(self.bucket, objs))
        for e in errors:
            logger.error("删除对象失败: %s", e)
        return len(objs) - len(errors)

    def public_url(self, object_key: str) -> str:
        """对象的公开访问地址；对象名中的中文、空格等做 URL 编码。"""
        return f"{self.public_base}/{self.bucket}/{quote(object_key)}"

    def ping(self) -> None:
        """连通性检查，失败时抛出异常。"""
        self.client.bucket_exists(self.bucket)
