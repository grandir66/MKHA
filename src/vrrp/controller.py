"""VRRP controller - manages VRRP priorities on routers via API."""

from __future__ import annotations

from typing import Any

from src.api.routeros_client import RouterOSClient
from src.quorum.manager import FailoverAction, QuorumDecision
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


class VRRPController:
    """Controls VRRP priorities on both routers to manage failover.

    Actions:
    - PROMOTE_BACKUP: Raise backup priority, lower master priority
    - DEMOTE_MASTER: Lower master priority below backup
    - RESTORE_MASTER: Restore original priorities (master > backup)
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

    async def handle_failover(
        self,
        action: FailoverAction,
        decision: QuorumDecision,
    ) -> None:
        """Handle a failover action from the quorum manager."""
        if action == FailoverAction.PROMOTE_BACKUP:
            await self._promote_backup()
        elif action == FailoverAction.DEMOTE_MASTER:
            await self._demote_master()
        elif action == FailoverAction.RESTORE_MASTER:
            await self._restore_master()
        elif action == FailoverAction.ALERT_ONLY:
            pass  # Notifications handle this

    async def _promote_backup(self) -> None:
        """Promote backup to master by adjusting VRRP priorities."""
        await log.ainfo("vrrp_promote_backup")
        mc = self.config.routers.master
        bc = self.config.routers.backup

        # Lower master priority (if reachable)
        try:
            await self._set_all_vrrp_priorities(
                self.master_client, mc.vrrp_priority_demoted
            )
        except Exception as e:
            await log.awarning("vrrp_demote_master_failed", error=str(e))

        # Raise backup priority
        try:
            await self._set_all_vrrp_priorities(
                self.slave_client, bc.vrrp_priority_master
            )
        except Exception as e:
            await log.aerror("vrrp_promote_backup_failed", error=str(e))

    async def _demote_master(self) -> None:
        """Demote master by lowering its VRRP priority."""
        await log.ainfo("vrrp_demote_master")
        mc = self.config.routers.master

        try:
            await self._set_all_vrrp_priorities(
                self.master_client, mc.vrrp_priority_demoted
            )
        except Exception as e:
            await log.aerror("vrrp_demote_master_failed", error=str(e))

    async def _restore_master(self) -> None:
        """Restore original VRRP priorities (master > backup)."""
        await log.ainfo("vrrp_restore_master")
        mc = self.config.routers.master
        bc = self.config.routers.backup

        # Restore master to high priority
        try:
            await self._set_all_vrrp_priorities(
                self.master_client, mc.vrrp_priority_master
            )
        except Exception as e:
            await log.aerror("vrrp_restore_master_failed", error=str(e))

        # Restore backup to normal priority
        try:
            await self._set_all_vrrp_priorities(
                self.slave_client, bc.vrrp_priority_backup
            )
        except Exception as e:
            await log.aerror("vrrp_restore_backup_failed", error=str(e))

    async def _set_all_vrrp_priorities(
        self, client: RouterOSClient, priority: int
    ) -> None:
        """Set priority on all VRRP interfaces of a router."""
        vrrp_interfaces = await client.get_vrrp_interfaces()
        for vrrp in vrrp_interfaces:
            vrrp_id = vrrp.get(".id")
            if vrrp_id:
                await client.set_vrrp_priority(vrrp_id, priority)
                await log.ainfo(
                    "vrrp_priority_set",
                    interface=vrrp.get("name", "?"),
                    priority=priority,
                )

    async def get_vrrp_status(self) -> dict[str, Any]:
        """Get VRRP status from both routers for the UI."""
        status: dict[str, Any] = {"master": [], "backup": []}

        try:
            master_vrrp = await self.master_client.get_vrrp_interfaces()
            status["master"] = [
                {
                    "name": v.get("name"),
                    "interface": v.get("interface"),
                    "vrid": v.get("vrid"),
                    "priority": v.get("priority"),
                    "running": v.get("running"),
                    "master": v.get("master"),
                }
                for v in master_vrrp
            ]
        except Exception as e:
            status["master_error"] = str(e)

        try:
            backup_vrrp = await self.slave_client.get_vrrp_interfaces()
            status["backup"] = [
                {
                    "name": v.get("name"),
                    "interface": v.get("interface"),
                    "vrid": v.get("vrid"),
                    "priority": v.get("priority"),
                    "running": v.get("running"),
                    "master": v.get("master"),
                }
                for v in backup_vrrp
            ]
        except Exception as e:
            status["backup_error"] = str(e)

        return status
