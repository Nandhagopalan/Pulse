"""
Cloudflare R2 access.

Two clients, deliberately:
  - boto3  for object-level work (does this key exist, put this raw zip, list a
           prefix) — cheap metadata calls DuckDB has no good primitive for.
  - duckdb for anything that reads or writes Parquet, so scans stay columnar and
           push down predicates instead of dragging whole files through Python.
"""
from __future__ import annotations

import functools
from typing import List, Optional

import boto3
import duckdb
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import config


@functools.lru_cache(maxsize=1)
def s3():
    key_id, secret = config.s3_credentials()
    return boto3.client(
        "s3",
        endpoint_url=config.r2_endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        # R2 does not implement the trailing-checksum flavour newer botocore
        # versions send by default; without this, PUTs fail with 501.
        config=BotoConfig(
            retries={"max_attempts": 5, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def object_exists(key: str) -> bool:
    try:
        s3().head_object(Bucket=config.r2_bucket, Key=key)
        return True
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def put_object(key: str, blob: bytes, content_type: str = "application/octet-stream") -> None:
    s3().put_object(Bucket=config.r2_bucket, Key=key, Body=blob, ContentType=content_type)


def get_object(key: str) -> Optional[bytes]:
    try:
        return s3().get_object(Bucket=config.r2_bucket, Key=key)["Body"].read()
    except ClientError as err:
        if err.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise


def list_keys(prefix: str) -> List[str]:
    keys: List[str] = []
    paginator = s3().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=config.r2_bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def duck(memory_limit: str = "4GB", threads: Optional[int] = None) -> duckdb.DuckDBPyConnection:
    """
    In-process DuckDB wired to R2 over the S3 protocol.

    URL_STYLE 'path' is required: R2's virtual-host addressing does not work with
    a custom endpoint, and the failure mode is a confusing 400 rather than a 404.
    """
    key_id, secret = config.s3_credentials()
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(
        """
        CREATE OR REPLACE SECRET pulse_r2 (
            TYPE S3,
            KEY_ID ?, SECRET ?,
            ENDPOINT ?, REGION 'auto', URL_STYLE 'path'
        )
        """,
        [key_id, secret, f"{config.r2_account_id}.r2.cloudflarestorage.com"],
    )
    con.execute(f"SET memory_limit = '{memory_limit}'")
    # Remote scans redraw a progress bar on every chunk, which turns piped or
    # logged output (CI, nohup) into megabytes of escape codes.
    con.execute("SET enable_progress_bar = false")
    if threads:
        con.execute(f"SET threads = {threads}")
    return con


def preflight() -> None:
    """Fail fast with an actionable message before a long job starts."""
    try:
        s3().head_bucket(Bucket=config.r2_bucket)
    except ClientError as err:
        raise RuntimeError(
            f"cannot reach R2 bucket '{config.r2_bucket}': {err}. "
            "Check R2_BUCKET_NAME and that the API token grants Object Read & Write on it."
        ) from err
