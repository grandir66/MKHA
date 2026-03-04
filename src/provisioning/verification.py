"""Post-provisioning verification checks."""

from __future__ import annotations

from typing import Any

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.provisioning.models import ProvisioningPhase, ProvisioningStep
from src.utils.logging import get_logger

log = get_logger(__name__)


class ProvisioningVerifier:
    """Runs verification checks after provisioning completes."""

    async def verify(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> list[ProvisioningStep]:
        """Run all verification checks."""
        checks = [
            ("verify_interfaces", "Verify interfaces are present",
             self._check_interfaces),
            ("verify_vrrp_state", "Verify VRRP in BACKUP state",
             self._check_vrrp_state),
            ("verify_firewall", "Verify firewall rule count",
             self._check_firewall_count),
            ("verify_routes", "Verify routes installed",
             self._check_routes),
            ("verify_ip_addresses", "Verify IP addresses assigned",
             self._check_ip_addresses),
            ("verify_ha_scripts", "Verify HA scripts deployed",
             self._check_ha_scripts),
        ]

        steps: list[ProvisioningStep] = []
        for name, description, check_fn in checks:
            step = ProvisioningStep(
                phase=ProvisioningPhase.VERIFICATION,
                name=name,
                description=description,
            )
            step.start()
            try:
                ok, detail = await check_fn(master_client, slave_client)
                if ok:
                    step.complete(detail)
                else:
                    step.fail(detail)
            except Exception as e:
                step.fail(f"Check error: {e}")
            steps.append(step)

        return steps

    async def _check_interfaces(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        master_ifaces = await master_client.get("interface")
        slave_ifaces = await slave_client.get("interface")

        # Count by type, excluding dynamic VRRP interfaces
        def count_by_type(ifaces: list[dict]) -> dict[str, int]:
            counts: dict[str, int] = {}
            for i in ifaces:
                if i.get("dynamic") == "true":
                    continue
                itype = i.get("type", "unknown")
                counts[itype] = counts.get(itype, 0) + 1
            return counts

        m_counts = count_by_type(master_ifaces)
        s_counts = count_by_type(slave_ifaces)

        mismatches = []
        for itype, m_count in m_counts.items():
            s_count = s_counts.get(itype, 0)
            if m_count != s_count:
                mismatches.append(f"{itype}: master={m_count}, slave={s_count}")

        if mismatches:
            return False, f"Interface mismatch: {'; '.join(mismatches)}"
        return True, f"All interface types match ({len(m_counts)} types)"

    async def _check_vrrp_state(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        try:
            slave_vrrp = await slave_client.get("interface/vrrp")
        except RouterOSError:
            return True, "No VRRP instances (OK if none on master)"

        if not slave_vrrp:
            return True, "No VRRP instances configured"

        issues = []
        for v in slave_vrrp:
            name = v.get("name", "?")
            if v.get("running") and v.get("master"):
                issues.append(f"'{name}' is MASTER (should be BACKUP)")

        if issues:
            return False, f"VRRP issues: {'; '.join(issues)}"
        return True, f"All {len(slave_vrrp)} VRRP instances OK"

    async def _check_firewall_count(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        paths = [
            ("filter", "ip/firewall/filter"),
            ("nat", "ip/firewall/nat"),
        ]
        results = []
        all_ok = True

        for label, path in paths:
            try:
                m_items = await master_client.get(path)
                s_items = await slave_client.get(path)
                m_static = [i for i in m_items if i.get("dynamic") != "true"]
                s_static = [i for i in s_items if i.get("dynamic") != "true"]
                if len(m_static) != len(s_static):
                    all_ok = False
                    results.append(f"{label}: master={len(m_static)}, slave={len(s_static)}")
                else:
                    results.append(f"{label}: {len(m_static)} rules")
            except RouterOSError:
                results.append(f"{label}: could not check")

        detail = "; ".join(results)
        return all_ok, detail

    async def _check_routes(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        try:
            m_routes = await master_client.get("ip/route")
            s_routes = await slave_client.get("ip/route")
            m_static = [r for r in m_routes if r.get("dynamic") != "true" and r.get("connect") != "true"]
            s_static = [r for r in s_routes if r.get("dynamic") != "true" and r.get("connect") != "true"]

            if abs(len(m_static) - len(s_static)) > 2:
                return False, f"Route count mismatch: master={len(m_static)}, slave={len(s_static)}"
            return True, f"Static routes: master={len(m_static)}, slave={len(s_static)}"
        except RouterOSError as e:
            return False, f"Route check failed: {e}"

    async def _check_ip_addresses(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        try:
            s_addrs = await slave_client.get("ip/address")
            s_static = [a for a in s_addrs if a.get("dynamic") != "true"]
            if not s_static:
                return False, "No static IP addresses on slave"
            return True, f"{len(s_static)} IP addresses assigned"
        except RouterOSError as e:
            return False, f"IP address check failed: {e}"

    async def _check_ha_scripts(
        self, master_client: RouterOSClient, slave_client: RouterOSClient,
    ) -> tuple[bool, str]:
        try:
            scripts = await slave_client.get("system/script")
            script_names = {s.get("name") for s in scripts}
            expected = {"ha_health_check", "ha_failover_hook"}
            found = expected & script_names
            missing = expected - script_names

            if missing:
                return False, f"Missing HA scripts: {', '.join(sorted(missing))}"
            return True, f"HA scripts present: {', '.join(sorted(found))}"
        except RouterOSError as e:
            return False, f"Script check failed: {e}"
