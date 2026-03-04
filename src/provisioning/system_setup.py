"""System-level configuration for the secondary router.

Handles settings NOT part of the normal sync engine:
identity, timezone, NTP, DNS, IP services, API user, logging.
"""

from __future__ import annotations

import time
from typing import Any

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.provisioning.models import (
    ProvisioningPhase,
    ProvisioningStep,
    StepStatus,
)
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


class SystemSetup:
    """Configures system-level settings on the secondary router."""

    def __init__(self, config: HAConfig):
        self.config = config

    async def plan(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> list[dict[str, Any]]:
        """Compute what system changes would be made (dry-run)."""
        changes: list[dict[str, Any]] = []

        # Identity
        try:
            slave_id = await slave_client.get("system/identity")
            current_name = slave_id[0].get("name", "") if slave_id else ""
            target_name = self.config.routers.backup.name
            if current_name != target_name:
                changes.append({
                    "section": "identity",
                    "action": "set",
                    "current": current_name,
                    "proposed": target_name,
                })
        except RouterOSError:
            changes.append({
                "section": "identity",
                "action": "set",
                "current": "unknown",
                "proposed": self.config.routers.backup.name,
            })

        # Timezone
        try:
            master_clock = await master_client.get("system/clock")
            slave_clock = await slave_client.get("system/clock")
            m_tz = master_clock[0].get("time-zone-name", "") if master_clock else ""
            s_tz = slave_clock[0].get("time-zone-name", "") if slave_clock else ""
            if m_tz and m_tz != s_tz:
                changes.append({
                    "section": "timezone",
                    "action": "set",
                    "current": s_tz,
                    "proposed": m_tz,
                })
        except RouterOSError:
            pass

        # NTP
        try:
            master_ntp = await master_client.get("system/ntp/client")
            slave_ntp = await slave_client.get("system/ntp/client")
            m_ntp = master_ntp[0] if master_ntp else {}
            s_ntp = slave_ntp[0] if slave_ntp else {}
            m_servers = m_ntp.get("servers", m_ntp.get("server-dns-names", ""))
            s_servers = s_ntp.get("servers", s_ntp.get("server-dns-names", ""))
            if m_servers != s_servers:
                changes.append({
                    "section": "ntp",
                    "action": "set",
                    "current": s_servers,
                    "proposed": m_servers,
                })
        except RouterOSError:
            pass

        # DNS upstream
        try:
            master_dns = await master_client.get("ip/dns")
            slave_dns = await slave_client.get("ip/dns")
            m_dns = master_dns[0] if master_dns else {}
            s_dns = slave_dns[0] if slave_dns else {}
            if m_dns.get("servers", "") != s_dns.get("servers", ""):
                changes.append({
                    "section": "dns",
                    "action": "set",
                    "current": s_dns.get("servers", ""),
                    "proposed": m_dns.get("servers", ""),
                })
        except RouterOSError:
            pass

        # Services to disable
        try:
            services = await slave_client.get("ip/service")
            for svc in services:
                name = svc.get("name", "")
                if name in self.config.provisioning.disable_services:
                    if svc.get("disabled") != "true":
                        changes.append({
                            "section": "service",
                            "action": "disable",
                            "current": f"{name}: enabled",
                            "proposed": f"{name}: disabled",
                        })
        except RouterOSError:
            pass

        # API user
        try:
            users = await slave_client.get("user")
            user_names = {u.get("name") for u in users}
            target_user = self.config.provisioning.api_user
            if target_user not in user_names:
                changes.append({
                    "section": "user",
                    "action": "create",
                    "current": "not present",
                    "proposed": f"user '{target_user}' (group: {self.config.provisioning.api_group})",
                })
        except RouterOSError:
            pass

        return changes

    async def apply(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> list[ProvisioningStep]:
        """Apply system-level configuration to the secondary."""
        steps: list[ProvisioningStep] = []

        # Identity
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="set_identity",
            description="Set system identity",
        )
        step.start()
        try:
            target_name = self.config.routers.backup.name
            await slave_client.set("system/identity", "", {"name": target_name})
            step.complete(f"Identity set to '{target_name}'")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # Timezone
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="set_timezone",
            description="Copy timezone from master",
        )
        step.start()
        try:
            master_clock = await master_client.get("system/clock")
            if master_clock:
                tz = master_clock[0].get("time-zone-name", "")
                if tz:
                    await slave_client.set("system/clock", "", {"time-zone-name": tz})
                    step.complete(f"Timezone set to '{tz}'")
                else:
                    step.skip("No timezone on master")
            else:
                step.skip("Could not read master clock")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # NTP
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="set_ntp",
            description="Copy NTP client config from master",
        )
        step.start()
        try:
            master_ntp = await master_client.get("system/ntp/client")
            if master_ntp:
                ntp_data = {}
                m = master_ntp[0]
                for key in ("enabled", "servers", "server-dns-names", "mode"):
                    if key in m:
                        ntp_data[key] = m[key]
                if ntp_data:
                    await slave_client.set("system/ntp/client", "", ntp_data)
                    step.complete(f"NTP configured: {ntp_data.get('servers', ntp_data.get('server-dns-names', ''))}")
                else:
                    step.skip("No NTP config to copy")
            else:
                step.skip("No NTP on master")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # DNS upstream
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="set_dns",
            description="Copy DNS client config from master",
        )
        step.start()
        try:
            master_dns = await master_client.get("ip/dns")
            if master_dns:
                dns_data = {}
                m = master_dns[0]
                for key in ("servers", "allow-remote-requests", "max-udp-packet-size",
                            "cache-size", "cache-max-ttl"):
                    if key in m:
                        dns_data[key] = m[key]
                if dns_data:
                    await slave_client.set("ip/dns", "", dns_data)
                    step.complete(f"DNS servers: {dns_data.get('servers', '')}")
                else:
                    step.skip("No DNS config to copy")
            else:
                step.skip("No DNS on master")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # Disable insecure services
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="disable_services",
            description="Disable insecure services",
        )
        step.start()
        disabled: list[str] = []
        try:
            services = await slave_client.get("ip/service")
            for svc in services:
                name = svc.get("name", "")
                svc_id = svc.get(".id", "")
                if name in self.config.provisioning.disable_services and svc.get("disabled") != "true":
                    await slave_client.set("ip/service", svc_id, {"disabled": "true"})
                    disabled.append(name)
            step.complete(f"Disabled: {', '.join(disabled)}" if disabled else "No services to disable")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # Create API user
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="create_api_user",
            description="Create/verify API user for orchestrator",
        )
        step.start()
        try:
            users = await slave_client.get("user")
            user_names = {u.get("name") for u in users}
            target_user = self.config.provisioning.api_user
            if target_user not in user_names:
                password = self.config.routers.backup.api_password
                await slave_client.add("user", {
                    "name": target_user,
                    "group": self.config.provisioning.api_group,
                    "password": password,
                })
                step.complete(f"Created user '{target_user}'")
            else:
                step.complete(f"User '{target_user}' already exists")
        except Exception as e:
            step.fail(str(e))
        steps.append(step)

        # Disable auto-upgrade
        step = ProvisioningStep(
            phase=ProvisioningPhase.SYSTEM_SETUP,
            name="disable_auto_upgrade",
            description="Disable auto-upgrade to prevent version mismatch",
        )
        step.start()
        try:
            await slave_client.set("system/routerboard/settings", "", {"auto-upgrade": "false"})
            step.complete("Auto-upgrade disabled")
        except Exception as e:
            # Non-critical, some models don't support this
            step.skip(f"Not applicable: {e}")
        steps.append(step)

        return steps
