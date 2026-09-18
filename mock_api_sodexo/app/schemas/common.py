from typing import Any, Optional

from pydantic import BaseModel


class Meta(BaseModel):
    source: str
    endpoint: str
    count: int
    total: int
    page: int
    page_size: int


class Envelope(BaseModel):
    data: list[Any]
    meta: Meta
    errors: Optional[list[str]] = None


def make_envelope(source: str, endpoint: str, records: list[dict], total: int, page: int, page_size: int) -> dict:
    return {
        "data": records,
        "meta": {
            "source": source,
            "endpoint": endpoint,
            "count": len(records),
            "total": total,
            "page": page,
            "page_size": page_size,
        },
        "errors": None,
    }


def not_available(source: str, endpoint: str, reason: str) -> dict:
    return {
        "data": [],
        "meta": {
            "source": source,
            "endpoint": endpoint,
            "count": 0,
            "total": 0,
            "page": 1,
            "page_size": 0,
        },
        "errors": [reason],
    }
