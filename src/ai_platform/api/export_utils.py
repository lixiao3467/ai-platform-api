"""Data export utilities — streaming CSV/JSON export for large datasets."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any, Callable

from fastapi.responses import StreamingResponse


def _csv_row(columns: list[str], values: list[Any]) -> str:
    """Serialize one CSV row to a string (with CRLF line ending)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(values)
    return buf.getvalue()


def _format_value(v: Any) -> str:
    """Format a value for CSV export."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (dict, list)):
        import json
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def make_csv_stream(
    columns: list[str],
    headers: list[str],
    fetch_page: Callable[[int, int], Any],
    *,
    page_size: int = 500,
    filename: str = "export.csv",
) -> StreamingResponse:
    """
    Create a streaming CSV response.

    Args:
        columns: CSV column names (machine-readable)
        headers: CSV display headers (same length as columns)
        fetch_page: async callable(offset, limit) → list of row dicts
        page_size: rows per DB page
        filename: download filename

    The generator fetches pages lazily so memory stays bounded.
    """

    async def generate() -> AsyncGenerator[str, None]:
        # BOM for Excel UTF-8 compatibility
        yield "﻿"
        # Header row
        yield _csv_row(columns, headers)

        offset = 0
        while True:
            rows = await fetch_page(offset, page_size)
            if not rows:
                break
            for row in rows:
                values = [_format_value(row.get(col)) for col in columns]
                yield _csv_row(columns, values)
            if len(rows) < page_size:
                break
            offset += page_size

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Accel-Buffering": "no",  # Prevent nginx buffering for streaming
        },
    )


def make_json_stream(
    fetch_page: Callable[[int, int], Any],
    *,
    page_size: int = 500,
    filename: str = "export.json",
) -> StreamingResponse:
    """
    Create a streaming JSON array response.

    Outputs a JSON array [...] with objects streamed lazily.
    """
    import json

    async def generate() -> AsyncGenerator[str, None]:
        yield "[\n"
        offset = 0
        first = True

        while True:
            rows = await fetch_page(offset, page_size)
            if not rows:
                break
            for row in rows:
                if not first:
                    yield ",\n"
                first = False
                yield json.dumps(row, ensure_ascii=False, default=str)
            if len(rows) < page_size:
                break
            offset += page_size

        yield "\n]"

    return StreamingResponse(
        generate(),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Accel-Buffering": "no",
        },
    )
