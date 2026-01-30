# functions/object_store.py
from __future__ import annotations

import io
import os
from typing import Optional

from minio import Minio
from minio.error import S3Error
from minio.deleteobjects import DeleteObject

import config


class ObjectStore:
    def __init__(self):
        self.client = Minio(
            endpoint=config.MINIO_ENDPOINT,
            access_key=config.MINIO_ACCESS_KEY,
            secret_key=config.MINIO_SECRET_KEY,
            secure=False,  # 内网 HTTP，生产可改 True + TLS
        )
        self.bucket = os.getenv("MINIO_BUCKET", "rag-files")

        try:
            if not self.client.bucket_exists(self.bucket):
                self.client.make_bucket(self.bucket)
        except S3Error as e:
            # 并发启动时可能出现 bucket 已创建，忽略即可
            if getattr(e, "code", "") not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                raise

    # -------------------------
    # Write
    # -------------------------
    def upload_file(self, object_key: str, local_path: str, content_type: str = "application/octet-stream"):
        self.client.fput_object(
            bucket_name=self.bucket,
            object_name=object_key,
            file_path=local_path,
            content_type=content_type,
        )

    def upload_bytes(self, object_key: str, data: bytes, content_type: str = "application/octet-stream"):
        """
        直接上传 bytes（用于你后续的 png_bytes / text_bytes 写入）
        """
        stream = io.BytesIO(data)
        self.client.put_object(
            bucket_name=self.bucket,
            object_name=object_key,
            data=stream,
            length=len(data),
            content_type=content_type,
        )

    # -------------------------
    # Read (新增)
    # -------------------------
    def get_bytes(self, object_key: str) -> bytes:
        """
        读取对象为 bytes
        """
        resp = None
        try:
            resp = self.client.get_object(self.bucket, object_key)
            return resp.read()
        finally:
            # MinIO 需要显式 close/release 连接
            if resp is not None:
                try:
                    resp.close()
                finally:
                    resp.release_conn()

    def get_text(self, object_key: str, encoding: str = "utf-8", errors: str = "replace") -> str:
        """
        读取对象为 text（默认 utf-8）
        """
        data = self.get_bytes(object_key)
        return data.decode(encoding, errors=errors)

    def exists(self, object_key: str) -> bool:
        """
        判断对象是否存在（可选但很实用）
        """
        try:
            self.client.stat_object(self.bucket, object_key)
            return True
        except S3Error as e:
            # NoSuchKey / NoSuchObject 在不同版本可能不同 code
            if getattr(e, "code", "") in ("NoSuchKey", "NoSuchObject", "NotFound"):
                return False
            raise

    # -------------------------
    # Utils
    # -------------------------
    def get_uri(self, object_key: str) -> str:
        return f"s3://{self.bucket}/{object_key}"

    # -------------------------
    # Delete
    # -------------------------
    def clear_prefix(self, prefix: str) -> int:
        """
        删除 bucket 下某个 prefix 的所有对象，返回删除数量（稳健版本）
        """
        return self.clear_prefix_safe(prefix)

    def clear_prefix_safe(self, prefix: str) -> int:
        """
        更稳的实现：先收集对象名，再删；返回删除数量
        """
        objs = list(self.client.list_objects(self.bucket, prefix=prefix, recursive=True))
        if not objs:
            return 0

        delete_list = (DeleteObject(o.object_name) for o in objs)
        errors = list(self.client.remove_objects(self.bucket, delete_list))
        if errors:
            raise RuntimeError("MinIO remove_objects errors: " + "; ".join(str(e) for e in errors))
        return len(objs)

    def clear_bucket(self) -> int:
        """
        清空整个 bucket（危险）
        """
        objs = list(self.client.list_objects(self.bucket, recursive=True))
        if not objs:
            return 0

        delete_list = (DeleteObject(o.object_name) for o in objs)
        errors = list(self.client.remove_objects(self.bucket, delete_list))
        if errors:
            raise RuntimeError("MinIO remove_objects errors: " + "; ".join(str(e) for e in errors))
        return len(objs)
