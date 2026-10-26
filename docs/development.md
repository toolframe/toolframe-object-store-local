# Development checks

For sibling source checkouts, with the matching SDK changes checked out:

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
uv build --no-sources
```

Unit tests exercise persistence, isolation, pagination, failed publication,
checksums and path/symlink rejection. The package requires Python 3.14 or later.
