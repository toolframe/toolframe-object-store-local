# Toolframe local object storage

`object-store-local` stores private binary files for Toolframe business plugins.
It implements `toolframe.object_store` v1, so plugins can use local disk or an
S3-compatible provider through the same `RuntimeContext.object_store()` client.

## Quick start

Use Toolframe Core 0.6.1 and SDK 0.3.0. With the application running:

```sh
toolframe --home APP plugin add pypi:toolframe-object-store-local==0.1.0 --enable
```

For a local source-development example with a Dashboard page, see
[Image Gallery](https://github.com/toolframe/toolframe-demo-image-gallery).

Business plugins declare `Requirement(service="toolframe.object_store")` under
alias `objects`. Inside an admitted handler:

```python
objects = self.context.object_store()
metadata = await objects.put(
    "images/example.png", image_bytes, content_type="image/png"
)
image_bytes = await objects.get(metadata["key"])
page = await objects.list(prefix="images/", limit=20)
await objects.delete(metadata["key"])
```

Core provides `data/objects/object-store-local/`. Objects are partitioned by the
application ID and owning plugin, and keys are hashed into filenames. A single
file holds metadata and content; publication uses fsync and atomic replacement.
No filesystem path is accepted from business callers. Symlink object files and
namespace directories are refused. Keep the application ID stable when restoring
a backup. Back up this directory along with the application configuration.

## Limits and behavior

- At most 64 MiB per object; optional `max_object_bytes` lowers the limit.
- Transfers use the SDK's 256 KiB chunk protocol with size and SHA-256 validation.
- Temporary uploads expire after ten minutes and do not survive worker restart.
- Completed objects survive reload, disable and uninstall. Delete is explicit.
- One enabled object-store provider per application, independent of SQL storage.
- Replacement is last-writer-wins; there is no transaction with SQL records.
- Listings page by key, return at most 100 items, and scan local object headers.
  This backend is intended for modest local collections, not large remote stores.
- No public URL or file-serving route is created. Business services own access
  checks; Dashboard renders bytes through the usual page/service bridge.

Switching providers does not copy objects automatically. Plugin workers are
trusted code, not an operating-system sandbox. The atomic file format assumes a
local filesystem supporting `fsync` and atomic rename; network filesystems are
not verified.

## Development

For sibling source checkouts, with the matching SDK changes checked out:

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
uv build --no-sources
```

Unit tests exercise persistence, isolation, pagination, failed publication,
checksums and path/symlink rejection. The package requires Python 3.14 or later.

Licensed under Apache-2.0; see [LICENSE](LICENSE).

For release validation and Trusted Publisher setup, see [releasing](docs/releasing.md).
