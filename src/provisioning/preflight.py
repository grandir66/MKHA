"""Pre-flight checks for Day Zero Provisioning."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.provisioning.models import PreflightResult
from src.utils.logging import get_logger

log = get_logger(__name__)

# Paths to check for "blankness"
BLANK_CHECK_PATHS = [
    "ip/firewall/filter",
    "ip/firewall/nat",
    "ip/address",
    "ip/route",
    "interface/bridge",
    "interface/vlan",
]


async def run_preflight(
    master_client: RouterOSClient,
    slave_client: RouterOSClient,
    master_name: str,
    slave_name: str,
    blank_threshold: int = 5,
    force: bool = False,
) -> PreflightResult:
    """Run all pre-flight checks before provisioning."""
    result = PreflightResult()

    # 1. Check master reachability
    try:
        master_res = await master_client.get_system_resource()
        result.master_reachable = True
        result.master_version = master_res.get("version", "unknown")
        identity = await master_client.get_identity()
        result.master_identity = identity
        await log.ainfo("preflight_master_ok", version=result.master_version,
                        identity=identity)
    except RouterOSError as e:
        result.master_reachable = False
        result.errors.append(f"Master ({master_name}) unreachable: {e}")
        await log.aerror("preflight_master_unreachable", error=str(e))
    except Exception as e:
        result.master_reachable = False
        result.errors.append(f"Master ({master_name}) error: {e}")

    # 2. Check secondary reachability
    try:
        slave_res = await slave_client.get_system_resource()
        result.secondary_reachable = True
        result.secondary_version = slave_res.get("version", "unknown")
        identity = await slave_client.get_identity()
        result.secondary_identity = identity
        await log.ainfo("preflight_secondary_ok", version=result.secondary_version,
                        identity=identity)
    except RouterOSError as e:
        result.secondary_reachable = False
        result.errors.append(f"Secondary ({slave_name}) unreachable: {e}")
        await log.aerror("preflight_secondary_unreachable", error=str(e))
    except Exception as e:
        result.secondary_reachable = False
        result.errors.append(f"Secondary ({slave_name}) error: {e}")

    if not result.master_reachable or not result.secondary_reachable:
        return result

    # 3. Version compatibility (major version must match)
    master_major = result.master_version.split(".")[0] if result.master_version else ""
    slave_major = result.secondary_version.split(".")[0] if result.secondary_version else ""

    if master_major and slave_major:
        if master_major == slave_major:
            result.version_compatible = True
            if result.master_version != result.secondary_version:
                result.warnings.append(
                    f"Minor version mismatch: master={result.master_version}, "
                    f"secondary={result.secondary_version}"
                )
        else:
            result.version_compatible = False
            result.errors.append(
                f"Major version mismatch: master={result.master_version}, "
                f"secondary={result.secondary_version}"
            )
    else:
        result.version_compatible = True
        result.warnings.append("Could not parse RouterOS versions for comparison")

    # 4. Check if secondary is "blank"
    total_items = 0
    try:
        for path in BLANK_CHECK_PATHS:
            try:
                items = await slave_client.get(path)
                # Filter out dynamic/default items
                static_items = [
                    i for i in items
                    if i.get("dynamic") != "true" and i.get("builtin") != "true"
                ]
                total_items += len(static_items)
            except RouterOSError:
                pass  # Some paths may not exist on blank router

        result.secondary_config_items = total_items
        result.secondary_is_blank = total_items <= blank_threshold

        if not result.secondary_is_blank and not force:
            result.warnings.append(
                f"Secondary has {total_items} config items (threshold: {blank_threshold}). "
                f"Use force=true to provision anyway."
            )
        await log.ainfo("preflight_blank_check", items=total_items,
                        is_blank=result.secondary_is_blank)
    except Exception as e:
        result.warnings.append(f"Could not check blank status: {e}")

    # 5. Check RouterOS packages match
    try:
        master_pkgs = await master_client.get("system/package")
        slave_pkgs = await slave_client.get("system/package")
        master_pkg_names = {p.get("name") for p in master_pkgs if not p.get("disabled")}
        slave_pkg_names = {p.get("name") for p in slave_pkgs if not p.get("disabled")}
        missing = master_pkg_names - slave_pkg_names
        if missing:
            result.warnings.append(
                f"Secondary missing packages: {', '.join(sorted(missing))}"
            )
    except Exception:
        pass  # Non-critical

    return result
