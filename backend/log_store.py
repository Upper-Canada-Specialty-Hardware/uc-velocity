"""Where large JSON logs are kept outside Postgres: a storage bucket, or a local folder.

The Vision cutover writes its change log here (one JSON object per applied
batch) instead of into ``vision_sync_changes``, so hundreds of thousands of log
rows never grow the database.

Configuration, read when a store is asked for:

* a Railway storage bucket (S3-compatible), through the variables the bucket's
  Credentials tab offers as references: ``BUCKET``, ``ENDPOINT``,
  ``ACCESS_KEY_ID``, ``SECRET_ACCESS_KEY``, ``REGION`` (and, optionally,
  ``BUCKET_ADDRESSING_STYLE`` = ``virtual`` (default) or ``path``, as the tab says);
* otherwise ``LOG_STORE_DIR``, a local folder (rehearsals on a local copy, tests).

With neither set there is no store, and callers refuse to run.
"""
from __future__ import annotations

import json
import os
from typing import Optional


class FolderStore:
    """JSON objects as files under one folder (keys may hold ``/``).

    Args:
        root: The folder; created when missing.
    """

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        """The file for a key, refusing keys that climb out of the folder."""
        path = os.path.abspath(os.path.join(self.root, *key.split("/")))
        if not path.startswith(self.root + os.sep):
            raise ValueError(f"bad key {key!r}")           # e.g. "../x": never write outside the folder
        return path

    def put_json(self, key: str, obj) -> None:
        """Write ``obj`` as JSON under ``key`` (replacing any earlier object)."""
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, default=str)
        os.replace(tmp, path)                              # whole file or nothing

    def get_json(self, key: str):
        """Read the object under ``key``.

        Raises:
            KeyError: No such object.
        """
        try:
            with open(self._path(key), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError as e:
            raise KeyError(key) from e

    def delete(self, key: str) -> None:
        """Remove the object under ``key`` (no error when it is not there)."""
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass

    def describe(self) -> str:
        """Where the objects go, for logs and the run record."""
        return f"folder {self.root}"


class BucketStore:
    """JSON objects in an S3-compatible bucket (a Railway storage bucket).

    Args:
        bucket, endpoint, access_key, secret_key, region: The bucket's credentials.
        addressing: ``"virtual"`` or ``"path"``, as the bucket's Credentials tab says.
    """

    def __init__(self, bucket: str, endpoint: str, access_key: str, secret_key: str, region: str,
                 addressing: str = "virtual") -> None:
        import boto3                                       # only needed when a bucket is configured
        from botocore.config import Config
        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key,
                                   aws_secret_access_key=secret_key, region_name=region or None,
                                   config=Config(s3={"addressing_style": addressing}))

    def put_json(self, key: str, obj) -> None:
        """Write ``obj`` as JSON under ``key`` (replacing any earlier object)."""
        body = json.dumps(obj, default=str).encode("utf-8")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body, ContentType="application/json")

    def get_json(self, key: str):
        """Read the object under ``key``.

        Raises:
            KeyError: No such object.
        """
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey as e:
            raise KeyError(key) from e
        return json.loads(body)

    def delete(self, key: str) -> None:
        """Remove the object under ``key`` (S3 does not complain when it is not there)."""
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def describe(self) -> str:
        """Where the objects go, for logs and the run record."""
        return f"bucket {self.bucket}"


def from_env() -> Optional[FolderStore | BucketStore]:
    """The store the environment configures, or None.

    Returns:
        A :class:`BucketStore` when ``BUCKET`` is set, else a
        :class:`FolderStore` when ``LOG_STORE_DIR`` is set, else None.

    Raises:
        RuntimeError: ``BUCKET`` is set but a credential is missing.
    """
    bucket = os.getenv("BUCKET")
    if bucket:
        need = {k: os.getenv(k) for k in ("ENDPOINT", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")}
        missing = [k for k, v in need.items() if not v]
        if missing:
            raise RuntimeError(f"BUCKET is set but {', '.join(missing)} is not")
        return BucketStore(bucket, need["ENDPOINT"], need["ACCESS_KEY_ID"], need["SECRET_ACCESS_KEY"],
                           os.getenv("REGION", ""), os.getenv("BUCKET_ADDRESSING_STYLE", "virtual"))
    folder = os.getenv("LOG_STORE_DIR")
    return FolderStore(folder) if folder else None
