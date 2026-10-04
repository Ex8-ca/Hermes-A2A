"""Tiny HTTP client for memex8 used by the a2a_bridge.

Why not reuse ``plugins/memex8/__init__.py``'s ``_Client``? Because
the bridge runs in a different process context (the a2a plugin is
loaded wherever the agent loop runs, but the memex8 plugin is the
memex8 plugin — they don't import each other). Keeping a small
client here means the bridge has zero coupling to the memex8 plugin
internals and the contract is the memex8 REST API.

Configuration:
  MEMEX8_API_BASE   base URL, default http://localhost:8080
  MEMEX8_API_KEY    bearer token; required for any non-empty read

The client is fail-closed: every error raises ``Memex8ClientError``
so callers don't have to remember which path returns None.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import requests


class Memex8ClientError(RuntimeError):
    """Raised for any memex8 API failure the bridge can't recover from.

    The bridge treats this as a sender-side or receiver-side error and
    surfaces a clear message to the agent; the underlying transport
    detail (status code, JSON shape) is in ``__cause__`` or the message.
    """


_DEFAULT_BASE_URL = "http://localhost:8080"


def _env_base_url() -> str:
    return os.environ.get("MEMEX8_API_BASE", _DEFAULT_BASE_URL).rstrip("/")


def _env_api_key() -> str:
    return os.environ.get("MEMEX8_API_KEY", "")


class Memex8Client:
    """Minimal memex8 REST client used by the A2A bridge.

    Exposes only what the bridge needs:
      * ``list_public(limit, offset)`` — discovery for the
        ``a2a_bridge_list_memex8_public`` tool.
      * ``get_memory(id)`` — sender-side pre-flight to confirm a
        memory exists and is ``visibility=public``; also used by the
        receiver to re-fetch the actual content from the sender's URL.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or _env_base_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else _env_api_key()
        self.timeout = timeout

    # -- transport --

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = requests.request(
                method.upper(),
                url,
                headers=self._headers(),
                timeout=kwargs.pop("timeout", self.timeout),
                **kwargs,
            )
        except requests.RequestException as e:
            raise Memex8ClientError(
                f"memex8 {method} {path} failed: transport error {e}"
            ) from e
        if not resp.ok:
            # Capture the body when it's JSON so the bridge can include
            # the server's error in its reply; otherwise just the code.
            detail = ""
            try:
                j = resp.json()
                if isinstance(j, dict):
                    detail = (
                        str(j.get("message"))
                        or str(j.get("error"))
                        or json.dumps(j)[:200]
                    )
                else:
                    detail = str(j)[:200]
            except Exception:
                detail = (resp.text or "")[:200]
            raise Memex8ClientError(
                f"memex8 {method} {path} failed ({resp.status_code}): {detail}"
            )
        # 204 No Content
        if resp.status_code == 204:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # -- public surface --

    def list_public(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Return ``GET /api/v1/memories/public``.

        Raises :class:`Memex8ClientError` on any failure. The server
        already filters to ``visibility=public``; this client does not
        filter again because that would be belt-and-suspenders code
        that could drift from the server's contract.
        """
        return self._request(
            "GET",
            "/api/v1/memories/public",
            params={"limit": str(limit), "offset": str(offset)},
        )

    def get_memory(self, memory_id: str) -> Dict[str, Any]:
        """Return ``GET /api/v1/memories/{id}``.

        Raises :class:`Memex8ClientError` on 404 — callers should
        treat that as 'not shareable' (the memory was deleted or the
        id was tampered with).
        """
        if not memory_id:
            raise Memex8ClientError("memory_id is required")
        return self._request("GET", f"/api/v1/memories/{memory_id}")

    def fetch_memex8_slice(
        self, memory_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Fetch and re-validate a list of memex8 memory IDs.

        Re-fetches each ID and refuses the whole batch if any item:
          * is not found (404),
          * has ``visibility != "public"``,
          * or wasn't actually in the original request (this client
            doesn't re-check that — the envelope signature covers it).

        Returns a list of fully-fetched memory dicts. Raises
        :class:`Memex8ClientError` on the first refusal; the error
        message identifies the offending id.
        """
        out: List[Dict[str, Any]] = []
        for mid in memory_ids:
            mem = self.get_memory(mid)
            # Server returns the MemoryPoint payload directly.
            visibility = (mem.get("visibility") or "").strip()
            if visibility != "public":
                # Fail closed: a memory that was public when the sender
                # signed may have been flipped to private since. Or a
                # tampered envelope is asking for a private memory. In
                # both cases, refuse the slice.
                raise Memex8ClientError(
                    f"memory {mid!r} is not visibility=public "
                    f"(got {visibility!r}); refusing"
                )
            out.append(mem)
        return out
