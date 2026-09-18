"""Thin HTTP client over the Sodexo mock enterprise API. Nothing else in the
app talks to the mock API directly - swapping the mock layer for a real
UKG/Workday/ADT/POS/Payroll/ERP integration later only means changing this
module (and MOCK_API_BASE_URL).
"""
from __future__ import annotations

import httpx

from app.core.config import get_settings

settings = get_settings()


class MockApiClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = base_url or settings.mock_api_base_url
        self.timeout = timeout

    def fetch_all_pages(self, endpoint: str, params: dict | None = None, page_size: int = 1000) -> list[dict]:
        params = dict(params or {})
        params["page_size"] = page_size
        page = 1
        records: list[dict] = []
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            while True:
                params["page"] = page
                resp = client.get(endpoint, params=params)
                resp.raise_for_status()
                body = resp.json()
                if body.get("errors"):
                    return records if records else []
                data = body.get("data", [])
                records.extend(data)
                meta = body.get("meta", {})
                total = meta.get("total", len(records))
                if len(records) >= total or not data:
                    break
                page += 1
        return records

    def health(self) -> dict:
        with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
            resp = client.get("/health")
            resp.raise_for_status()
            return resp.json()
