from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .models import BuildResult


class HindsightError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, Any]]


class HindsightClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float = 600,
        transport: Transport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport or self._urllib_transport

    def _urllib_transport(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Any]:
        request = urllib.request.Request(
            url, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8")
                return response.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            error.close()
            try:
                payload: Any = json.loads(text)
            except json.JSONDecodeError:
                payload = text
            return error.code, payload
        except urllib.error.URLError as error:
            raise HindsightError(f"Hindsight connection failed: {error.reason}") from error

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, response = self.transport(
            method,
            f"{self.base_url}{path}",
            headers,
            body,
            timeout or self.timeout,
        )
        if status == 404:
            return None
        if status >= 400:
            detail = response.get("detail") if isinstance(response, dict) else response
            raise HindsightError(
                f"Hindsight returned HTTP {status}: {detail}", status=status
            )
        return response

    @staticmethod
    def _bank_path(bank: str, suffix: str) -> str:
        bank_id = urllib.parse.quote(bank, safe="")
        return f"/v1/default/banks/{bank_id}{suffix}"

    def get_document(self, bank: str, document_id: str) -> dict[str, Any] | None:
        document_id = urllib.parse.quote(document_id, safe="")
        return self._request(
            "GET", self._bank_path(bank, f"/documents/{document_id}"), timeout=60
        )

    def replace_document(self, bank: str, result: BuildResult) -> dict[str, Any]:
        item = {
            "content": result.content,
            "document_id": result.document_id,
            "update_mode": "replace",
            "timestamp": result.timestamp,
            "context": result.context,
            "metadata": result.metadata,
            "tags": result.tags,
            "observation_scopes": result.observation_scopes,
        }
        response = self._request(
            "POST",
            self._bank_path(bank, "/memories"),
            {"items": [item], "async": False},
        )
        if not response.get("success"):
            raise HindsightError(f"Hindsight retain failed: {response}")
        return response

    def consolidate(self, bank: str, scopes: list[list[str]]) -> dict[str, Any]:
        return self._request(
            "POST",
            self._bank_path(bank, "/consolidate"),
            {"observation_scopes": scopes},
            timeout=60,
        )

    def restore_document(self, bank: str, backup: dict[str, Any]) -> dict[str, Any]:
        params = backup.get("retain_params") or {}
        item = {
            "content": backup["original_text"],
            "document_id": backup["id"],
            "update_mode": "replace",
            "context": params.get("context"),
            "timestamp": params.get("timestamp") or params.get("event_date"),
            "metadata": backup.get("document_metadata") or {},
            "tags": backup.get("tags") or [],
            "observation_scopes": backup.get("observation_scopes"),
        }
        item = {key: value for key, value in item.items() if value is not None}
        response = self._request(
            "POST",
            self._bank_path(bank, "/memories"),
            {"items": [item], "async": False},
        )
        if not response.get("success"):
            raise HindsightError(f"Hindsight restore failed: {response}")
        return response
