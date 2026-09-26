"""Atomic filesystem object envelopes. Logical keys never become filesystem paths."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import tempfile
from pathlib import Path

from toolframe_sdk import AuthorizationSpec, PluginDefinition, PluginError
from toolframe_sdk.object_store import (
    MAX_OBJECT_BYTES,
    OBJECT_STORE_SERVICE,
    object_store_service,
)
from toolframe_sdk.object_store_provider import ObjectStoreProvider


class LocalObjectStore(ObjectStoreProvider):
    def open_backend(self):
        if not self.context.state_directory:
            raise PluginError(
                "storage_directory_missing",
                "Core must allocate an object storage directory",
            )
        self.directory = Path(self.context.state_directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory = self.directory.resolve()

    def folder(self, namespace):
        path = self.directory
        for part in namespace.split("/"):
            path = path / part
            if path.is_symlink():
                raise PluginError(
                    "invalid_store_path",
                    "Object storage directories cannot be symlinks",
                )
            path.mkdir(exist_ok=True)
        return path

    def path(self, namespace, key):
        path = self.folder(namespace) / (
            hashlib.sha256(key.encode()).hexdigest() + ".object"
        )
        if path.is_symlink():
            raise PluginError("invalid_store_path", "Object files cannot be symlinks")
        return path

    def put(self, namespace, metadata, stream):
        destination = self.path(namespace, metadata["key"])
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, prefix=".upload-", delete=False
            ) as output:
                temporary = Path(output.name)
                header = json.dumps(metadata, separators=(",", ":")).encode()
                output.write(struct.pack("!I", len(header)))
                output.write(header)
                shutil.copyfileobj(stream, output, 256 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            descriptor = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    def open_object(self, namespace, key):
        try:
            descriptor = os.open(self.path(namespace, key), os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise PluginError("object_missing", "Object does not exist") from None
        return os.fdopen(descriptor, "rb")

    @staticmethod
    def metadata(stream, key=None):
        raw = stream.read(4)
        if len(raw) != 4 or not 0 < (size := struct.unpack("!I", raw)[0]) < 4096:
            raise PluginError("object_corrupt", "Object metadata is invalid")
        metadata = json.loads(stream.read(size))
        if key is not None and metadata["key"] != key:
            raise PluginError(
                "object_corrupt", "Object key does not match its metadata"
            )
        if os.fstat(stream.fileno()).st_size - stream.tell() != metadata["size"]:
            raise PluginError(
                "object_corrupt", "Object content does not match its size"
            )
        return metadata

    def head(self, namespace, key):
        with self.open_object(namespace, key) as stream:
            return self.metadata(stream, key)

    def read(self, namespace, key, offset, length, etag):
        with self.open_object(namespace, key) as stream:
            metadata = self.metadata(stream, key)
            if metadata["etag"] != etag:
                raise PluginError(
                    "object_changed", "Object changed; read its metadata again"
                )
            if offset > metadata["size"]:
                raise PluginError("invalid_range", "Offset exceeds the object size")
            stream.seek(offset, os.SEEK_CUR)
            return stream.read(length)

    def list(self, namespace, prefix, after, limit):
        # Read only object headers; bounded output and a portable key cursor.
        from heapq import nsmallest

        def records():
            for path in self.folder(namespace).glob("*.object"):
                with os.fdopen(
                    os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb"
                ) as stream:
                    metadata = self.metadata(stream)
                if metadata["key"].startswith(prefix) and metadata["key"] > after:
                    yield metadata

        items = nsmallest(limit + 1, records(), key=lambda item: item["key"])
        return {
            "objects": items[:limit],
            "next_after": items[limit - 1]["key"] if len(items) > limit else None,
        }

    def delete(self, namespace, key):
        self.path(namespace, key).unlink(missing_ok=True)


plugin = PluginDefinition(
    id="object-store-local",
    kind="provider",
    provider_kind="object-store",
    factory="toolframe_object_store_local.plugin:LocalObjectStore",
    authorization=AuthorizationSpec(),
    config_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "max_object_bytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_OBJECT_BYTES,
            }
        },
    },
    services={OBJECT_STORE_SERVICE: object_store_service()},
)
