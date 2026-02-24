"""Synchronous Paperless-ngx REST client."""
import logging
import os
import re
from typing import Generator, Optional

import httpx

logger = logging.getLogger(__name__)

PAPERLESS_URL   = os.getenv("PAPERLESS_URL", "").rstrip("/")
PAPERLESS_TOKEN = os.getenv("PAPERLESS_TOKEN", "")
PAGE_SIZE = 25
TIMEOUT   = httpx.Timeout(60.0)


class PaperlessClient:
    def __init__(self):
        if not PAPERLESS_URL:
            raise RuntimeError("PAPERLESS_URL not set")
        if not PAPERLESS_TOKEN:
            raise RuntimeError("PAPERLESS_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {PAPERLESS_TOKEN}",
            "Content-Type": "application/json",
        }

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get(self, path: str, **params) -> dict:
        with httpx.Client(headers=self._headers, timeout=TIMEOUT, follow_redirects=True) as c:
            r = c.get(f"{PAPERLESS_URL}{path}", params=params)
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, json: dict) -> dict:
        with httpx.Client(headers=self._headers, timeout=TIMEOUT, follow_redirects=True) as c:
            r = c.post(f"{PAPERLESS_URL}{path}", json=json)
            r.raise_for_status()
            return r.json()

    def _patch(self, path: str, json: dict) -> dict:
        with httpx.Client(headers=self._headers, timeout=TIMEOUT, follow_redirects=True) as c:
            r = c.patch(f"{PAPERLESS_URL}{path}", json=json)
            if not r.is_success:
                # Include response body so we can see exactly what Paperless rejected
                raise httpx.HTTPStatusError(
                    f"{r.status_code} {r.reason_phrase} — body: {r.text[:400]}",
                    request=r.request,
                    response=r,
                )
            return r.json()

    # ── Documents ──────────────────────────────────────────────────────────────

    def get_document(self, doc_id: int) -> dict:
        return self._get(f"/api/documents/{doc_id}/")

    def download_document(self, doc_id: int) -> bytes:
        with httpx.Client(headers=self._headers, timeout=TIMEOUT, follow_redirects=True) as c:
            r = c.get(f"{PAPERLESS_URL}/api/documents/{doc_id}/download/")
            r.raise_for_status()
            return r.content

    def get_all_documents(self) -> Generator[dict, None, None]:
        page = 1
        while True:
            data = self._get("/api/documents/", page=page, page_size=PAGE_SIZE)
            for doc in data.get("results", []):
                yield doc
            if not data.get("next"):
                break
            page += 1

    def rename_document(self, doc_id: int, new_title: str) -> None:
        self._patch(f"/api/documents/{doc_id}/", {"title": new_title})

    def set_created_date(self, doc_id: int, iso_date: str) -> None:
        """Set the document created date. Use noon UTC to avoid timezone day-shift."""
        self._patch(f"/api/documents/{doc_id}/", {"created": f"{iso_date}T12:00:00+00:00"})

    # ── Tags ───────────────────────────────────────────────────────────────────

    def get_or_create_tag(self, name: str) -> int:
        data = self._get("/api/tags/", name__iexact=name)
        if data.get("count", 0) > 0:
            return data["results"][0]["id"]
        result = self._post("/api/tags/", {"name": name, "color": "#e67e22"})
        return result["id"]

    def add_tags(self, doc_id: int, tag_ids: list) -> None:
        doc = self.get_document(doc_id)
        existing = doc.get("tags", [])
        if existing and isinstance(existing[0], dict):
            existing = [t["id"] for t in existing]
        merged = list(set(existing + tag_ids))
        self._patch(f"/api/documents/{doc_id}/", {"tags": merged})

    # ── Custom fields ──────────────────────────────────────────────────────────

    def set_custom_fields(self, doc_id: int, fields: dict) -> None:
        """
        Set custom fields on a Paperless document.

        Paperless requires the FULL custom_fields list on every PATCH —
        sending a partial list clears the omitted fields or causes 400.
        We therefore:
          1. Fetch all defined custom field definitions (name → id + data_type)
          2. Fetch the document's current custom_field assignments
          3. Merge our updates into the existing assignments by field id
          4. PATCH with the complete merged list
        """
        # Step 1 — global field definitions
        try:
            cf_resp = self._get("/api/custom_fields/")
            field_defs = {
                f["name"]: {"id": f["id"], "data_type": f.get("data_type", "string")}
                for f in cf_resp.get("results", [])
            }
        except Exception as e:
            logger.warning(f"Could not fetch custom field definitions: {e}")
            return

        if not field_defs:
            logger.debug("No custom fields defined in Paperless — skipping")
            return

        # Step 2 — existing assignments on this document
        try:
            doc = self.get_document(doc_id)
            existing_cf = doc.get("custom_fields", [])
            # {"field": <id>, "value": <val>} → {id: val}
            merged = {item["field"]: item["value"] for item in existing_cf}
        except Exception as e:
            logger.warning(f"Doc {doc_id}: could not fetch existing custom fields: {e}")
            merged = {}

        # Step 3 — apply our updates, coercing types per data_type
        for name, value in fields.items():
            if name not in field_defs:
                logger.debug(f"Custom field '{name}' not found in Paperless — skipping")
                continue

            fid       = field_defs[name]["id"]
            data_type = field_defs[name]["data_type"]

            if data_type == "monetary":
                # Value should already be formatted as "CAD20.00" by the caller.
                # Just ensure it's a string; if it's a plain number, send as-is.
                val_str = str(value).strip()
                coerced = val_str
            elif data_type in ("integer", "float", "number"):
                try:
                    coerced = float(re.sub(r"[^0-9.]", "", str(value)))
                except (ValueError, TypeError):
                    coerced = 0
            else:
                # string / text / select / url / date — send as-is
                coerced = str(value)

            merged[fid] = coerced

        # Step 4 — PATCH with full merged list
        payload = [{"field": fid, "value": val} for fid, val in merged.items()]
        if payload:
            self._patch(f"/api/documents/{doc_id}/", {"custom_fields": payload})

    # ── Health ─────────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        try:
            with httpx.Client(
                headers=self._headers,
                timeout=httpx.Timeout(5.0),
                follow_redirects=True,
            ) as c:
                return c.get(f"{PAPERLESS_URL}/api/").status_code == 200
        except Exception:
            return False
