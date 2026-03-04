"""VRRP instance creation on the secondary router.

The normal SyncEngine explicitly excludes VRRP (device-specific).
During provisioning, we create VRRP instances on the secondary
with backup priority.
"""

from __future__ import annotations

from typing import Any

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.provisioning.models import ProvisioningPhase, ProvisioningStep
from src.sync.variable_translator import VariableTranslator
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)

# VRRP properties to copy from master
VRRP_COPY_KEYS = {
    "name", "interface", "vrid", "interval",
    "preemption-mode", "authentication", "password",
    "version", "v3-protocol", "group-master",
    "comment", "disabled",
}

# Keys that need variable translation
VRRP_TRANSLATE_KEYS = {"interface"}


class VRRPSetup:
    """Creates VRRP instances on the secondary to match master."""

    def __init__(self, config: HAConfig, translator: VariableTranslator):
        self.config = config
        self.translator = translator
        self._backup_priority = config.routers.backup.vrrp_priority_backup

    async def plan(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> list[dict[str, Any]]:
        """Return list of VRRP instances that would be created."""
        try:
            master_vrrp = await master_client.get("interface/vrrp")
        except RouterOSError:
            return []

        try:
            slave_vrrp = await slave_client.get("interface/vrrp")
        except RouterOSError:
            slave_vrrp = []

        slave_names = {v.get("name") for v in slave_vrrp}
        to_create: list[dict[str, Any]] = []

        for m_vrrp in master_vrrp:
            name = m_vrrp.get("name", "")
            if name in slave_names:
                continue
            instance = self._build_slave_vrrp(m_vrrp)
            to_create.append(instance)

        return to_create

    def _build_slave_vrrp(self, master_vrrp: dict[str, Any]) -> dict[str, Any]:
        """Build VRRP instance dict for slave from master instance."""
        instance: dict[str, Any] = {}
        for key in VRRP_COPY_KEYS:
            if key in master_vrrp:
                value = master_vrrp[key]
                if key in VRRP_TRANSLATE_KEYS and isinstance(value, str):
                    value = self.translator.translate_value(value)
                instance[key] = value

        # Override priority to backup
        instance["priority"] = str(self._backup_priority)

        # Add slave suffix to comment
        if self.translator:
            comment = instance.get("comment", "")
            instance["comment"] = self.translator.apply_role_suffix(comment)

        # Copy address (virtual IP) - these are shared IPs, no translation
        if "address" in master_vrrp:
            instance["address"] = master_vrrp["address"]

        return instance

    async def apply(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> list[ProvisioningStep]:
        """Create VRRP instances on the secondary."""
        steps: list[ProvisioningStep] = []
        planned = await self.plan(master_client, slave_client)

        if not planned:
            step = ProvisioningStep(
                phase=ProvisioningPhase.VRRP_SETUP,
                name="vrrp_none",
                description="VRRP setup",
            )
            step.skip("No VRRP instances to create")
            steps.append(step)
            return steps

        for vrrp_data in planned:
            name = vrrp_data.get("name", "?")
            step = ProvisioningStep(
                phase=ProvisioningPhase.VRRP_SETUP,
                name=f"create_vrrp_{name}",
                description=f"Create VRRP '{name}'",
            )
            step.start()
            try:
                await slave_client.add("interface/vrrp", vrrp_data)
                step.complete(
                    f"Created VRRP '{name}' VRID={vrrp_data.get('vrid', '?')} "
                    f"priority={vrrp_data.get('priority', '?')} "
                    f"interface={vrrp_data.get('interface', '?')}"
                )
                await log.ainfo("vrrp_created", name=name,
                                vrid=vrrp_data.get("vrid"),
                                priority=vrrp_data.get("priority"))
            except Exception as e:
                step.fail(str(e))
                await log.aerror("vrrp_create_failed", name=name, error=str(e))
            steps.append(step)

        return steps
