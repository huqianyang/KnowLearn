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
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, secure: bool, public_base: str):
        self.client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self.bucket = bucket
        self.public_base = public_base.rstrip("/")

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("已创建 MinIO 桶 %s", self.bucket)
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
        content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
        self.client.fput_object(self.bucket, object_key, str(local_path), content_type=content_type)
        return self.public_url(object_key)

    def delete_prefix(self, prefix: str) -> int:
        objs = [DeleteObject(o.object_name) for o in self.client.list_objects(self.bucket, prefix=prefix, recursive=True)]
        if not objs:
            return 0
        errors = list(self.client.remove_objects(self.bucket, objs))
        for e in errors:
            logger.error("删除对象失败: %s", e)
        return len(objs) - len(errors)

    def public_url(self, object_key: str) -> str:
        return f"{self.public_base}/{self.bucket}/{quote(object_key)}"

    def ping(self) -> None:
        self.client.bucket_exists(self.bucket)
