"""Async REST API client for RouterOS 7."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from src.utils.logging import get_logger

log = get_logger(__name__)


class RouterOSError(Exception):
    """Base exception for RouterOS API errors."""

    def __init__(self, message: str, status_code: int | None = None, detail: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class RouterOSConnectionError(RouterOSError):
    """Raised when the router is unreachable."""


class RouterOSAuthError(RouterOSError):
    """Raised on authentication failure."""


class RouterOSNotMikroTikError(RouterOSError):
    """Raised when the device is not a MikroTik / does not serve the REST API."""


class RouterOSClient:
    """Async client for the RouterOS 7 REST API.

    RouterOS 7 REST API maps CLI paths to URL paths:
        /ip/address  →  GET https://router/rest/ip/address
        /ip/firewall/filter  →  GET https://router/rest/ip/firewall/filter

    Supports CRUD operations: get, add, set, remove.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: float = 10.0,
        verify_ssl: bool = False,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                auth=httpx.BasicAuth(self.username, self.password),
                timeout=httpx.Timeout(self.timeout),
                verify=self.verify_ssl,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> Any:
        """Execute an HTTP request with retry logic."""
        client = await self._get_client()
        url = path if path.startswith("/") else f"/{path}"

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                )

                if response.status_code == 401:
                    raise RouterOSAuthError(
                        "Authentication failed — check username/password",
                        status_code=401,
                    )

                if response.status_code >= 400:
                    detail = None
                    try:
                        detail = response.json()
                    except Exception:
                        detail = response.text
                    raise RouterOSError(
                        f"API error {response.status_code}: {detail}",
                        status_code=response.status_code,
                        detail=detail,
                    )

                if response.status_code == 204:
                    return None

                # Validate that the response is JSON (MikroTik REST API).
                # Non-MikroTik devices (e.g. UniFi) may return HTML on the
                # same URL which would silently produce empty data.
                content_type = response.headers.get("content-type", "")
                if "json" not in content_type:
                    body_preview = response.text[:200]
                    raise RouterOSNotMikroTikError(
                        f"Device at {self.base_url} did not return JSON "
                        f"(Content-Type: {content_type}). "
                        f"This may not be a MikroTik router. "
                        f"Response preview: {body_preview}",
                    )

                try:
                    return response.json()
                except Exception:
                    raise RouterOSError(
                        f"Invalid JSON response from {self.base_url}{url}"
                    )

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                err_msg = str(e) or type(e).__name__
                # Detect SSL handshake failures and suggest HTTP fallback
                if "ssl" in err_msg.lower() or "handshake" in err_msg.lower():
                    raise RouterOSConnectionError(
                        f"SSL/TLS handshake failed connecting to {self.base_url}. "
                        f"Try using http:// instead of https:// — "
                        f"Detail: {err_msg}"
                    ) from e
                last_error = RouterOSConnectionError(
                    f"Connection to {self.base_url} failed "
                    f"(attempt {attempt}/{self.max_retries}): {err_msg}"
                )
                if attempt < self.max_retries:
                    await log.awarning(
                        "routeros_request_retry",
                        attempt=attempt,
                        base_url=self.base_url,
                        path=url,
                        error=err_msg,
                    )
                    await asyncio.sleep(self.retry_delay * attempt)
            except RouterOSError:
                raise
            except Exception as e:
                raise RouterOSError(
                    f"Unexpected error ({self.base_url}{url}): {e}"
                ) from e

        raise last_error  # type: ignore[misc]

    # --- CRUD Operations ---

    async def get(
        self,
        path: str,
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """GET all items at the given path.

        Args:
            path: RouterOS REST path, e.g. "ip/firewall/filter"
            params: Optional query parameters (.proplist, etc.)

        Returns:
            List of items (dicts).
        """
        result = await self._request("GET", path, params=params)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
        return []

    async def get_one(
        self,
        path: str,
        item_id: str,
    ) -> dict[str, Any]:
        """GET a single item by its .id.

        Args:
            path: RouterOS REST path
            item_id: The .id value (e.g. "*1")

        Returns:
            Single item dict.
        """
        result = await self._request("GET", f"{path}/{item_id}")
        if isinstance(result, dict):
            return result
        raise RouterOSError(f"Unexpected response type for get_one: {type(result)}")

    async def add(
        self,
        path: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """PUT (add) a new item.

        RouterOS 7 REST uses PUT for creating new entries.

        Args:
            path: RouterOS REST path
            data: Item properties

        Returns:
            Created item with .id
        """
        result = await self._request("PUT", path, json_data=data)
        if isinstance(result, dict):
            return result
        return {"ret": result}

    async def set(
        self,
        path: str,
        item_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """PATCH (update) an existing item.

        Args:
            path: RouterOS REST path
            item_id: The .id of the item to update
            data: Properties to update
        """
        result = await self._request("PATCH", f"{path}/{item_id}", json_data=data)
        if isinstance(result, dict):
            return result
        return None

    async def remove(
        self,
        path: str,
        item_id: str,
    ) -> None:
        """DELETE an item.

        Args:
            path: RouterOS REST path
            item_id: The .id of the item to remove
        """
        await self._request("DELETE", f"{path}/{item_id}")

    async def move(
        self,
        path: str,
        item_id: str,
        destination: int,
    ) -> None:
        """Move an item to a specific position (for ordered lists like firewall rules).

        Uses POST with the move command.

        Args:
            path: RouterOS REST path
            item_id: The .id of the item to move
            destination: Target position index
        """
        await self._request(
            "POST",
            f"{path}/move",
            json_data={".id": item_id, "destination": str(destination)},
        )

    # --- Utility Methods ---

    async def is_reachable(self) -> bool:
        """Check if the router API is reachable."""
        try:
            await self.get("system/resource")
            return True
        except Exception:
            return False

    async def get_system_resource(self) -> dict[str, Any]:
        """Get system resource info (CPU, RAM, uptime, version, etc.)."""
        items = await self.get("system/resource")
        return items[0] if items else {}

    async def get_identity(self) -> str:
        """Get the router's identity name."""
        items = await self.get("system/identity")
        return items[0].get("name", "unknown") if items else "unknown"

    async def get_vrrp_interfaces(self) -> list[dict[str, Any]]:
        """Get all VRRP interface configurations."""
        return await self.get("interface/vrrp")

    async def set_vrrp_priority(self, vrrp_id: str, priority: int) -> None:
        """Set the priority of a VRRP interface."""
        await self.set("interface/vrrp", vrrp_id, {"priority": str(priority)})

    async def __aenter__(self) -> "RouterOSClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
