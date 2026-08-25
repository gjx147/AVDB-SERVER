"""对象存储备份（N20）：备份文件上传到 S3 兼容存储（R2/OSS/S3）。"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger("avdb.s3_backup")


def _s3_client():
    import boto3
    from config import get_settings
    from database import SessionLocal
    from models import Setting

    db = SessionLocal()
    try:
        def _g(k: str) -> str:
            row = db.get(Setting, k)
            return row.value if row else ""
        endpoint = _g("s3_endpoint")
        bucket = _g("s3_bucket")
        key = _g("s3_access_key")
        secret = _g("s3_secret_key")
        region = _g("s3_region") or "auto"
    finally:
        db.close()
    if not (endpoint and bucket and key and secret):
        return None
    return boto3.client(
        "s3", endpoint_url=endpoint, region_name=region,
        aws_access_key_id=key, aws_secret_access_key=secret,
    ), bucket


def is_enabled() -> bool:
    from database import SessionLocal
    from models import Setting
    db = SessionLocal()
    try:
        row = db.get(Setting, "s3_backup_enabled")
        return bool(row and row.value == "true")
    finally:
        db.close()


async def upload_backup(local_path: str) -> dict:
    """上传备份文件到对象存储，key: backups/<文件名>。"""
    p = Path(local_path)
    if not p.exists():
        return {"ok": False, "message": f"文件不存在: {local_path}"}
    try:
        client, bucket = _s3_client()
        if not client:
            return {"ok": False, "message": "S3 未配置（endpoint/bucket/密钥）"}
        key = f"backups/{p.name}"
        client.upload_file(str(p), bucket, key)
        logger.info("备份已上传对象存储: %s/%s", bucket, key)
        return {"ok": True, "key": key}
    except Exception as e:
        logger.error("S3 上传失败: %s", e)
        return {"ok": False, "message": str(e)[:200]}


def list_remote(limit: int = 10) -> dict:
    """列出对象存储中的备份文件。"""
    try:
        client, bucket = _s3_client()
        if not client:
            return {"ok": False, "message": "S3 未配置"}
        resp = client.list_objects_v2(Bucket=bucket, Prefix="backups/")
        keys = sorted(
            (o["Key"], o.get("Size", 0)) for o in resp.get("Contents", [])
        )[-limit:]
        return {"ok": True, "items": [{"key": k, "size_mb": round(s / 1048576, 1)} for k, s in keys]}
    except Exception as e:
        return {"ok": False, "message": str(e)[:200]}
