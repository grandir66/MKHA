"""Quorum manager - the orchestrator acts as the witness/arbiter.

The orchestrator monitors both routers and makes decisions about
which should be master based on health status. It also exposes
a /health endpoint that the routers query to verify the witness is alive.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.api.routeros_client import RouterOSClient
from src.quorum.health import HealthResult, RouterStatus, check_router_health
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


class ClusterState(str, Enum):
    NORMAL = "normal"  # Both routers healthy, master is active
    FAILOVER = "failover"  # Master down, backup promoted
    DEGRADED = "degraded"  # One router has issues but hasn't failed
    SPLIT = "split"  # Potential split-brain detected
    OFFLINE = "offline"  # Both routers unreachable
    INITIALIZING = "initializing"


class FailoverAction(str, Enum):
    NONE = "none"
    PROMOTE_BACKUP = "promote_backup"
    DEMOTE_MASTER = "demote_master"
    RESTORE_MASTER = "restore_master"
    ALERT_ONLY = "alert_only"


@dataclass
class QuorumDecision:
    """A decision made by the quorum manager."""

    timestamp: float = field(default_factory=time.time)
    cluster_state: ClusterState = ClusterState.INITIALIZING
    action: FailoverAction = FailoverAction.NONE
    reason: str = ""
    master_health: HealthResult | None = None
    backup_health: HealthResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cluster_state": self.cluster_state.value,
            "action": self.action.value,
            "reason": self.reason,
            "master_health": self.master_health.to_dict() if self.master_health else None,
            "backup_health": self.backup_health.to_dict() if self.backup_health else None,
        }


class QuorumManager:
    """Monitors both routers and makes failover decisions.

    The orchestrator itself is the quorum witness. Decisions are based on:
    - API reachability of both routers
    - ICMP reachability
    - System resource health
    - Consecutive failure threshold (to avoid flapping)
    - Cooldown period after failover
    """

    def __init__(
        self,
        config: HAConfig,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ):
        self.config = config
        self.master_client = master_client
        self.slave_client = slave_client

        self._cluster_state = ClusterState.INITIALIZING
        self._master_fail_count = 0
        self._backup_fail_count = 0
        self._last_failover_time: float = 0
        self._fail_threshold = config.quorum.witness.fail_threshold
        self._cooldown_seconds = config.cluster.failover_cooldown_seconds

        # History of decisions for the UI
        self._decision_history: list[QuorumDecision] = []
        self._max_history = 100

        # Latest health results
        self.last_master_health: HealthResult | None = None
        self.last_backup_health: HealthResult | None = None

        # Callback for when a failover action is needed
        self._failover_callback: Any = None

    def set_failover_callback(self, callback: Any) -> None:
        """Set a callback function(action, decision) for failover events."""
        self._failover_callback = callback

    async def check(self) -> QuorumDecision:
        """Perform a health check cycle and make a decision."""
        ping_timeout = self.config.quorum.health_check.ping_timeout_ms / 1000

        # Check both routers in parallel
        master_task = check_router_health(
            self.master_client,
            self.config.routers.master.name,
            self.config.routers.master.api_url,
            ping_timeout=ping_timeout,
        )
        backup_task = check_router_health(
            self.slave_client,
            self.config.routers.backup.name,
            self.config.routers.backup.api_url,
            ping_timeout=ping_timeout,
        )

        master_health, backup_health = await asyncio.gather(
            master_task, backup_task, return_exceptions=True
        )

        # Handle exceptions from health checks
        if isinstance(master_health, Exception):
            master_health = HealthResult(
                router_name=self.config.routers.master.name,
                status=RouterStatus.UNREACHABLE,
                error=str(master_health),
            )
        if isinstance(backup_health, Exception):
            backup_health = HealthResult(
                router_name=self.config.routers.backup.name,
                status=RouterStatus.UNREACHABLE,
                error=str(backup_health),
            )

        self.last_master_health = master_health
        self.last_backup_health = backup_health

        # Update failure counters
        if master_health.status == RouterStatus.UNREACHABLE:
            self._master_fail_count += 1
        else:
            self._master_fail_count = 0

        if backup_health.status == RouterStatus.UNREACHABLE:
            self._backup_fail_count += 1
        else:
            self._backup_fail_count = 0

        # Make decision
        decision = self._decide(master_health, backup_health)

        # Store decision
        self._decision_history.append(decision)
        if len(self._decision_history) > self._max_history:
            self._decision_history = self._decision_history[-self._max_history:]

        # Log decision
        await log.ainfo(
            "quorum_check",
            cluster_state=decision.cluster_state.value,
            action=decision.action.value,
            reason=decision.reason,
            master_status=master_health.status.value,
            backup_status=backup_health.status.value,
            master_fails=self._master_fail_count,
            backup_fails=self._backup_fail_count,
        )

        # Execute callback if action needed
        if decision.action != FailoverAction.NONE and self._failover_callback:
            try:
                await self._failover_callback(decision.action, decision)
            except Exception as e:
                await log.aerror("failover_callback_error", error=str(e))

        return decision

    def _decide(
        self,
        master_health: HealthResult,
        backup_health: HealthResult,
    ) -> QuorumDecision:
        """Core decision logic based on health status."""
        decision = QuorumDecision(
            master_health=master_health,
            backup_health=backup_health,
        )

        m_ok = master_health.status in (RouterStatus.HEALTHY, RouterStatus.DEGRADED)
        b_ok = backup_health.status in (RouterStatus.HEALTHY, RouterStatus.DEGRADED)

        # Check cooldown
        in_cooldown = (time.time() - self._last_failover_time) < self._cooldown_seconds

        if m_ok and b_ok:
            # Both healthy - normal operation
            decision.cluster_state = ClusterState.NORMAL
            decision.action = FailoverAction.NONE
            decision.reason = "Both routers healthy"

            # If we were in failover state and master is back, consider restoring
            if self._cluster_state == ClusterState.FAILOVER and not in_cooldown:
                decision.action = FailoverAction.RESTORE_MASTER
                decision.reason = "Master recovered, restoring original roles"
                self._last_failover_time = time.time()

        elif m_ok and not b_ok:
            # Master OK, backup down
            decision.cluster_state = ClusterState.DEGRADED
            decision.action = FailoverAction.ALERT_ONLY
            decision.reason = f"Backup unreachable (fails: {self._backup_fail_count})"

        elif not m_ok and b_ok:
            # Master down, backup OK
            if self._master_fail_count >= self._fail_threshold and not in_cooldown:
                decision.cluster_state = ClusterState.FAILOVER
                decision.action = FailoverAction.PROMOTE_BACKUP
                decision.reason = (
                    f"Master unreachable for {self._master_fail_count} checks, "
                    f"promoting backup"
                )
                self._last_failover_time = time.time()
            else:
                decision.cluster_state = ClusterState.DEGRADED
                decision.action = FailoverAction.ALERT_ONLY
                reason_parts = [f"Master unreachable (fails: {self._master_fail_count})"]
                if in_cooldown:
                    reason_parts.append("in cooldown period")
                elif self._master_fail_count < self._fail_threshold:
                    reason_parts.append(
                        f"waiting for threshold ({self._fail_threshold})"
                    )
                decision.reason = ", ".join(reason_parts)

        else:
            # Both unreachable
            decision.cluster_state = ClusterState.OFFLINE
            decision.action = FailoverAction.ALERT_ONLY
            decision.reason = "Both routers unreachable - maintaining last known state"

        self._cluster_state = decision.cluster_state
        return decision

    @property
    def cluster_state(self) -> ClusterState:
        return self._cluster_state

    @property
    def decision_history(self) -> list[QuorumDecision]:
        return list(self._decision_history)

    def get_status(self) -> dict[str, Any]:
        """Get current quorum status for the API/UI."""
        return {
            "cluster_state": self._cluster_state.value,
            "master_fail_count": self._master_fail_count,
            "backup_fail_count": self._backup_fail_count,
            "last_failover_time": self._last_failover_time,
            "cooldown_seconds": self._cooldown_seconds,
            "fail_threshold": self._fail_threshold,
            "master_health": self.last_master_health.to_dict() if self.last_master_health else None,
            "backup_health": self.last_backup_health.to_dict() if self.last_backup_health else None,
        }
