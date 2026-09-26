import hashlib
import time
from types import SimpleNamespace

import pytest
from toolframe_sdk import PluginError, RuntimeContext, call_context
from toolframe_sdk.object_store import OBJECT_STORE_SERVICE

from toolframe_object_store_local.plugin import LocalObjectStore


@pytest.fixture
async def store(tmp_path):
    context = RuntimeContext(
        None,
        plugin_id="object-store-local",
        generation="s1",
        state_directory=str(tmp_path),
    )
    provider = LocalObjectStore(context)
    await provider.activate()

    async def request(method, params, **kwargs):
        return await provider.invoke(
            OBJECT_STORE_SERVICE, params["method"], params["params"]
        )

    client = RuntimeContext(
        SimpleNamespace(request=request), plugin_id="gallery", generation="g1"
    ).object_store()
    token = call_context.set(
        {
            "application_id": "app",
            "owner": "gallery",
            "owner_generation": "g1",
            "root_id": "r",
            "deadline": time.time() + 60,
        }
    )
    try:
        yield provider, client
    finally:
        call_context.reset(token)
        await provider.dispose()


async def test_atomic_persistence_pagination_delete_and_isolation(store):
    provider, client = store
    data = bytes(range(256)) * 7000
    await client.put("images/a.png", data, content_type="image/png")
    await client.put("images/b.png", b"b")
    assert await client.get("images/a.png") == data
    assert (await client.list(prefix="images/", limit=1))[
        "next_after"
    ] == "images/a.png"
    await provider.dispose()
    await provider.activate()
    assert await client.get("images/a.png") == data
    token = call_context.set({**provider.context.call_info, "application_id": "other"})
    try:
        assert (await client.list())["objects"] == []
    finally:
        call_context.reset(token)
    await client.delete("images/a.png")
    await client.delete("images/a.png")
    assert len((await client.list())["objects"]) == 1


async def test_failed_replacement_keeps_old_object(store, monkeypatch):
    provider, client = store
    await client.put("image", b"old")

    def fail(*args):
        raise OSError("simulated disk error")

    monkeypatch.setattr("toolframe_object_store_local.plugin.os.replace", fail)
    with pytest.raises(PluginError) as error:
        await client.put("image", b"new")
    assert error.value.code == "object_store_unavailable"
    assert await client.get("image") == b"old"
    assert not list(provider.directory.rglob(".upload-*"))


async def test_symlinks_and_traversal_are_rejected(store, tmp_path):
    provider, client = store
    outside = tmp_path / "outside"
    outside.mkdir()
    namespace = provider.identity()[0]
    folder = provider.folder(namespace)
    path = folder / (hashlib.sha256(b"image").hexdigest() + ".object")
    path.symlink_to(outside / "secret")
    with pytest.raises(PluginError):
        await client.get("image")
    with pytest.raises(PluginError):
        await client.put("image", b"x")
    with pytest.raises(PluginError):
        await client.put("../secret", b"x")
    assert not (outside / "secret").exists()


async def test_empty_file_and_corruption(store):
    provider, client = store
    await client.put("empty", b"")
    assert await client.get("empty") == b""
    path = provider.path(provider.identity()[0], "empty")
    path.write_bytes(b"bad")
    with pytest.raises(PluginError) as error:
        await client.get("empty")
    assert error.value.code == "object_corrupt"
