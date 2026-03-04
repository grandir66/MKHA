"""Health check module for monitoring router status."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.api.routeros_client import RouterOSClient
from src.utils.logging import get_logger

log = get_logger(__name__)


class RouterStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # API reachable but resource issues
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


@dataclass
class HealthResult:
    """Result of a single health check."""

    router_name: str
    status: RouterStatus = RouterStatus.UNKNOWN
    api_reachable: bool = False
    ping_reachable: bool = False
    response_time_ms: float = 0
    cpu_load: int = 0
    memory_used_percent: int = 0
    uptime: str = ""
    version: str = ""
    identity: str = ""
    error: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "router_name": self.router_name,
            "status": self.status.value,
            "api_reachable": self.api_reachable,
            "ping_reachable": self.ping_reachable,
            "response_time_ms": round(self.response_time_ms, 1),
            "cpu_load": self.cpu_load,
            "memory_used_percent": self.memory_used_percent,
            "uptime": self.uptime,
            "version": self.version,
            "identity": self.identity,
            "error": self.error,
            "timestamp": self.timestamp,
        }


async def _ping_host(host: str, timeout: float = 1.0) -> bool:
    """ICMP ping a host. Returns True if reachable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", str(int(timeout)), host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=timeout + 1)
        return proc.returncode == 0
    except (asyncio.TimeoutError, OSError):
        return False


def _extract_host(api_url: str) -> str:
    """Extract hostname/IP from an API URL like https://10.0.0.1/rest."""
    url = api_url.replace("https://", "").replace("http://", "")
    return url.split("/")[0].split(":")[0]


async def check_router_health(
    client: RouterOSClient,
    router_name: str,
    api_url: str,
    ping_timeout: float = 1.0,
) -> HealthResult:
    """Perform a comprehensive health check on a router.

    Checks:
    1. REST API reachability (get system resource)
    2. ICMP ping reachability
    3. System resources (CPU, RAM)
    """
    result = HealthResult(router_name=router_name)
    host = _extract_host(api_url)

    # Run API check and ping in parallel
    start = time.monotonic()

    api_task = asyncio.create_task(_check_api(client, result))
    ping_task = asyncio.create_task(_ping_host(host, ping_timeout))

    await asyncio.gather(api_task, ping_task, return_exceptions=True)

    result.response_time_ms = (time.monotonic() - start) * 1000
    result.ping_reachable = ping_task.result() if not ping_task.cancelled() else False

    # Determine overall status
    if result.api_reachable:
        if result.cpu_load > 90 or result.memory_used_percent > 95:
            result.status = RouterStatus.DEGRADED
        else:
            result.status = RouterStatus.HEALTHY
    elif result.ping_reachable:
        result.status = RouterStatus.DEGRADED
        result.error = "API unreachable but host responds to ping"
    else:
        result.status = RouterStatus.UNREACHABLE

    return result


async def _check_api(client: RouterOSClient, result: HealthResult) -> None:
    """Check router via REST API and populate result."""
    try:
        resource = await client.get_system_resource()
        result.api_reachable = True
        result.cpu_load = int(resource.get("cpu-load", 0))
        result.uptime = resource.get("uptime", "")
        result.version = resource.get("version", "")

        # Calculate memory percentage
        total_mem = int(resource.get("total-memory", 1))
        free_mem = int(resource.get("free-memory", 0))
        if total_mem > 0:
            result.memory_used_percent = int(((total_mem - free_mem) / total_mem) * 100)

        try:
            result.identity = await client.get_identity()
        except Exception:
            pass

    except Exception as e:
        result.api_reachable = False
        result.error = str(e)
