"""FastAPI web application - dashboard, API, and quorum health endpoint."""

from __future__ import annotations

import io
import json
import secrets as _secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.api.routeros_client import (
    RouterOSClient,
    RouterOSError,
    RouterOSNotMikroTikError,
)
from src.api.ssh_client import MikroTikSSHClient, SSHError
from src.utils.config import SECTION_GROUPS, AuthUser, HAConfig, save_config
from src.version import __version__

if TYPE_CHECKING:
    from src.main import HAOrchestrator

app = FastAPI(title="MikroTik HA Manager", version=__version__)

# Session middleware for authentication
app.add_middleware(SessionMiddleware, secret_key=_secrets.token_hex(32), session_cookie="mkha_session")

_orchestrator: HAOrchestrator | None = None

templates = Jinja2Templates(directory="src/web/templates")
templates.env.globals["app_version"] = __version__


def set_orchestrator(orchestrator: HAOrchestrator) -> None:
    global _orchestrator
    _orchestrator = orchestrator


def get_orchestrator() -> "HAOrchestrator":
    if _orchestrator is None:
        raise HTTPException(503, "Orchestrator not initialized")
    return _orchestrator


# ============================================================
# Authentication middleware
# ============================================================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated users to /login when auth_users are configured."""
    # Skip auth if orchestrator not ready or no users configured
    if _orchestrator is None or not _orchestrator.config.web.auth_users:
        return await call_next(request)

    path = request.url.path
    # Public paths
    if path in ("/login", "/health") or path.startswith("/static"):
        return await call_next(request)

    user = request.session.get("user")
    if not user:
        if path.startswith("/api/"):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return RedirectResponse("/login", status_code=302)

    return await call_next(request)


# ============================================================
# Health endpoint (quorum witness)
# ============================================================

@app.get("/health")
async def health_endpoint():
    orch = get_orchestrator()
    return {
        "status": "ok",
        "cluster_state": orch.quorum.cluster_state.value,
        "uptime": orch.uptime_seconds,
    }


# ============================================================
# Login / Logout
# ============================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
async def login_submit(request: Request):
    from src.utils.auth import verify_password

    form = await request.form()
    username = str(form.get("username", ""))
    password = str(form.get("password", ""))

    orch = get_orchestrator()
    for user in orch.config.web.auth_users:
        if user.username == username and verify_password(password, user.password_hash, user.salt):
            request.session["user"] = username
            return RedirectResponse("/", status_code=302)

    return templates.TemplateResponse("login.html", {
        "request": request,
        "error": "Invalid credentials",
    })


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


# ============================================================
# HTML Pages
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "dashboard",
    })


@app.get("/diff", response_class=HTMLResponse)
async def diff_page(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("diff.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "diff",
        "section_groups": SECTION_GROUPS,
    })


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("config.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "config",
    })


@app.get("/provision", response_class=HTMLResponse)
async def provision_page(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("provision.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "provision",
    })


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("setup.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "setup",
        "section_groups": SECTION_GROUPS,
    })


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    orch = get_orchestrator()
    return templates.TemplateResponse("logs.html", {
        "request": request,
        "cluster_name": orch.config.cluster.name,
        "active": "logs",
    })


# ============================================================
# API: Cluster Status
# ============================================================

@app.get("/api/status")
async def api_status():
    orch = get_orchestrator()
    return {
        "cluster": {
            "name": orch.config.cluster.name,
            "state": orch.quorum.cluster_state.value,
            "uptime": orch.uptime_seconds,
        },
        "quorum": orch.quorum.get_status(),
        "last_sync": orch.last_sync_report.to_dict() if orch.last_sync_report else None,
    }


# ============================================================
# API: VRRP
# ============================================================

@app.get("/api/vrrp")
async def api_vrrp():
    orch = get_orchestrator()
    return await orch.vrrp_controller.get_vrrp_status()


@app.post("/api/vrrp/{router}/{vrrp_id}/priority")
async def api_set_vrrp_priority(router: str, vrrp_id: str, request: Request):
    orch = get_orchestrator()
    body = await request.json()
    priority = int(body.get("priority", 100))

    if router == "master":
        await orch.master_client.set_vrrp_priority(vrrp_id, priority)
    elif router == "backup":
        await orch.slave_client.set_vrrp_priority(vrrp_id, priority)
    else:
        raise HTTPException(400, f"Unknown router: {router}")

    return {"status": "ok", "priority": priority}


# ============================================================
# API: Diff & Sync
# ============================================================

@app.get("/api/diff")
async def api_diff():
    orch = get_orchestrator()
    diffs = await orch.sync_engine.compute_diff()

    # Map section names to groups for UI filtering
    section_to_group: dict[str, str] = {}
    for group_name, group_info in SECTION_GROUPS.items():
        for section in group_info["sections"]:
            section_to_group[section] = group_name

    return {
        "has_changes": any(d.has_changes for d in diffs),
        "total_changes": sum(d.total_changes for d in diffs),
        "sections": [
            {
                "name": d.section,
                "group": section_to_group.get(d.section, "other"),
                "has_changes": d.has_changes,
                "summary": d.summary(),
                "additions": len(d.additions),
                "updates": len(d.updates),
                "removals": len(d.removals),
                "moves": len(d.moves),
                "details": {
                    "additions": [
                        {"data": e.data, "position": e.position}
                        for e in d.additions
                    ],
                    "updates": [
                        {"item_id": e.item_id, "changes": e.data, "old": e.old_data}
                        for e in d.updates
                    ],
                    "removals": [
                        {"item_id": e.item_id, "data": e.data}
                        for e in d.removals
                    ],
                },
            }
            for d in diffs
        ],
    }


@app.post("/api/sync")
async def api_sync(request: Request):
    orch = get_orchestrator()
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    dry_run = body.get("dry_run", False)
    report = await orch.sync_engine.sync(dry_run=dry_run)
    return report.to_dict()


# ============================================================
# API: Failover
# ============================================================

@app.post("/api/failover")
async def api_failover(request: Request):
    orch = get_orchestrator()
    body = await request.json()
    action = body.get("action", "promote_backup")

    from src.quorum.manager import FailoverAction, QuorumDecision
    fa = FailoverAction(action)
    decision = QuorumDecision(action=fa, reason="Manual failover from web UI")
    await orch.vrrp_controller.handle_failover(fa, decision)
    return {"status": "ok", "action": action}


# ============================================================
# API: Router Config (per-device, not auto-synced)
# ============================================================

@app.get("/api/config/{router}")
async def api_get_router_config(router: str, section: Optional[str] = None):
    orch = get_orchestrator()
    client = orch.master_client if router == "master" else orch.slave_client

    result: dict[str, Any] = {}

    if section is None or section == "vrrp":
        result["vrrp"] = await client.get("interface/vrrp")
    if section is None or section == "identity":
        result["identity"] = await client.get("system/identity")
    if section is None or section == "ntp":
        result["ntp"] = await client.get("system/ntp/client")
    if section is None or section == "dns":
        result["dns"] = await client.get("ip/dns")
    if section is None or section == "users":
        result["users"] = await client.get("user")

    return result


@app.patch("/api/config/{router}/{path:path}")
async def api_set_router_config(router: str, path: str, request: Request):
    orch = get_orchestrator()
    client = orch.master_client if router == "master" else orch.slave_client
    body = await request.json()
    item_id = body.pop(".id", None)

    if item_id:
        await client.set(path, item_id, body)
    else:
        await client.add(path, body)

    return {"status": "ok"}


# ============================================================
# API: Config Export (textual, organized by section)
# ============================================================

def _parse_export_sections(export_text: str) -> list[dict[str, Any]]:
    """Parse RouterOS /export text into organized sections.

    Returns a list of dicts with 'path', 'commands', and 'comment_lines'.
    """
    sections: list[dict[str, Any]] = []
    header_lines: list[str] = []
    current_path = ""
    current_commands: list[str] = []

    continuation_buf = ""  # Buffer for lines ending with \

    for line in export_text.splitlines():
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            continue

        # Handle continuation lines (ending with \)
        if continuation_buf:
            # Append this line to the continuation buffer
            continuation_buf += " " + stripped
            if continuation_buf.endswith("\\"):
                # Still continuing, strip trailing \ and keep accumulating
                continuation_buf = continuation_buf[:-1].rstrip()
                continue
            # Continuation complete — use the joined line
            stripped = continuation_buf
            continuation_buf = ""
        elif stripped.endswith("\\"):
            # Start of a continuation — strip trailing \ and buffer
            continuation_buf = stripped[:-1].rstrip()
            continue

        # Header comments (before first section)
        if stripped.startswith("#") and not current_path:
            header_lines.append(stripped)
            continue

        # Section header: starts with /
        if stripped.startswith("/"):
            # Save previous section
            if current_path and current_commands:
                sections.append({
                    "path": current_path,
                    "commands": current_commands,
                    "count": len([c for c in current_commands if not c.startswith("#")]),
                })
            current_path = stripped
            current_commands = []
            continue

        # Command or comment within a section
        if current_path:
            current_commands.append(stripped)

    # Flush any remaining continuation buffer
    if continuation_buf and current_path:
        current_commands.append(continuation_buf)

    # Save last section
    if current_path and current_commands:
        sections.append({
            "path": current_path,
            "commands": current_commands,
            "count": len([c for c in current_commands if not c.startswith("#")]),
        })

    # Add header as first element if present
    if header_lines:
        sections.insert(0, {
            "path": "# header",
            "commands": header_lines,
            "count": 0,
        })

    return sections


def _rest_to_export_lines(items: list[dict[str, Any]], path_label: str) -> list[str]:
    """Convert REST API items to RouterOS-like export text lines."""
    lines: list[str] = []
    # Fields to skip (internal / not part of config)
    skip_fields = {".id", ".nextid", "dynamic", "builtin", "default",
                   "invalid", "running", "slave", "inactive", ".dead"}

    for item in items:
        parts: list[str] = []
        for k, v in item.items():
            if k in skip_fields or v == "" or v is None:
                continue
            # Quote values with spaces
            if isinstance(v, str) and (" " in v or ";" in v):
                parts.append(f'{k}="{v}"')
            else:
                parts.append(f"{k}={v}")
        if parts:
            lines.append("add " + " ".join(parts))
    return lines


@app.get("/api/config/export/{role}")
async def api_config_export(role: str):
    """Fetch full router config export, organized by sections.

    Tries SSH export first (full text). Falls back to REST API data
    formatted as pseudo-export if SSH is unavailable.

    Returns:
        {
            "source": "ssh" | "rest",
            "identity": "...",
            "version": "...",
            "sections": [
                {"path": "/ip/address", "commands": ["add ..."], "count": 3},
                ...
            ],
            "raw_export": "..." (only if SSH)
        }
    """
    orch = get_orchestrator()
    if role not in ("master", "backup"):
        raise HTTPException(400, f"Unknown role: {role}")

    router_cfg = (
        orch.config.routers.master if role == "master"
        else orch.config.routers.backup
    )
    client = orch.master_client if role == "master" else orch.slave_client

    result: dict[str, Any] = {"role": role, "source": "none", "sections": []}

    # Get identity and version via REST (quick)
    try:
        resource = await client.get_system_resource()
        result["identity"] = await client.get_identity()
        result["version"] = resource.get("version", "")
        result["board"] = resource.get("board-name", "")
    except Exception as e:
        result["error"] = f"Router unreachable: {e}"
        return result

    # Try SSH export first
    ssh_ok = False
    if router_cfg.ssh_enabled:
        import asyncio

        ssh = MikroTikSSHClient.from_api_url(
            api_url=router_cfg.api_url,
            username=router_cfg.api_user,
            password=router_cfg.api_password,
            ssh_port=router_cfg.ssh_port,
            timeout=10.0,
            key_file=router_cfg.ssh_key_file,
        )

        def _ssh_export() -> str | None:
            try:
                ssh.connect()
                text = ssh.export(verbose=False)
                return text
            except Exception:
                return None
            finally:
                ssh.close()

        loop = asyncio.get_event_loop()
        export_text = await loop.run_in_executor(None, _ssh_export)

        if export_text:
            ssh_ok = True
            result["source"] = "ssh"
            result["raw_export"] = export_text
            result["sections"] = _parse_export_sections(export_text)

    # Fallback: build pseudo-export from REST API data
    if not ssh_ok:
        result["source"] = "rest"
        rest_sections = {
            "/interface/bridge": "interface/bridge",
            "/interface/bridge/port": "interface/bridge/port",
            "/interface/bridge/vlan": "interface/bridge/vlan",
            "/interface/vlan": "interface/vlan",
            "/interface/bonding": "interface/bonding",
            "/interface/list": "interface/list",
            "/interface/list/member": "interface/list/member",
            "/interface/vrrp": "interface/vrrp",
            "/interface/wireguard": "interface/wireguard",
            "/interface/wireguard/peers": "interface/wireguard/peers",
            "/ip/address": "ip/address",
            "/ip/pool": "ip/pool",
            "/ip/dhcp-server": "ip/dhcp-server",
            "/ip/dhcp-server/network": "ip/dhcp-server/network",
            "/ip/dhcp-server/lease": "ip/dhcp-server/lease",
            "/ip/dns/static": "ip/dns/static",
            "/ip/firewall/filter": "ip/firewall/filter",
            "/ip/firewall/nat": "ip/firewall/nat",
            "/ip/firewall/mangle": "ip/firewall/mangle",
            "/ip/firewall/raw": "ip/firewall/raw",
            "/ip/firewall/address-list": "ip/firewall/address-list",
            "/ip/ipsec/peer": "ip/ipsec/peer",
            "/ip/ipsec/policy": "ip/ipsec/policy",
            "/ip/route": "ip/route",
            "/ip/service": "ip/service",
            "/system/script": "system/script",
            "/system/scheduler": "system/scheduler",
            "/queue/simple": "queue/simple",
            "/queue/tree": "queue/tree",
        }

        sections = []
        # Add header
        sections.append({
            "path": "# header",
            "commands": [
                f"# Generated from REST API data",
                f"# {result.get('identity', '?')} — v{result.get('version', '?')}",
                f"# {result.get('board', '')}",
            ],
            "count": 0,
        })

        for path_label, api_path in rest_sections.items():
            try:
                items = await client.get(api_path)
                static = [i for i in items if i.get("dynamic") != "true"]
                if not static:
                    continue
                commands = _rest_to_export_lines(static, path_label)
                if commands:
                    sections.append({
                        "path": path_label,
                        "commands": commands,
                        "count": len(commands),
                    })
            except Exception:
                continue

        result["sections"] = sections

    return result


# ============================================================
# API: Logs & Events
# ============================================================

@app.get("/api/logs")
async def api_logs(limit: int = 50):
    orch = get_orchestrator()
    return {"logs": orch.log_buffer[-limit:], "total": len(orch.log_buffer)}


@app.get("/api/events")
async def api_events(limit: int = 20):
    orch = get_orchestrator()
    history = orch.quorum.decision_history[-limit:]
    return [d.to_dict() for d in history]


@app.get("/api/variables")
async def api_variables():
    orch = get_orchestrator()
    if orch.sync_engine.translator:
        return orch.sync_engine.translator.get_mapping_summary()
    return []


# ============================================================
# API: Setup (cluster configuration page)
# ============================================================

@app.get("/api/setup/config")
async def api_setup_get_config():
    """Return current config (without passwords)."""
    orch = get_orchestrator()
    c = orch.config
    return {
        "cluster_name": c.cluster.name,
        "sync_interval": c.cluster.sync_interval_seconds,
        "master": {
            "name": c.routers.master.name,
            "api_url": c.routers.master.api_url,
            "api_user": c.routers.master.api_user,
            "ssh_port": c.routers.master.ssh_port,
            "ssh_enabled": c.routers.master.ssh_enabled,
            "has_password": bool(c.routers.master.api_password),
            "variables_file": c.routers.master.variables_file,
        },
        "backup": {
            "name": c.routers.backup.name,
            "api_url": c.routers.backup.api_url,
            "api_user": c.routers.backup.api_user,
            "ssh_port": c.routers.backup.ssh_port,
            "ssh_enabled": c.routers.backup.ssh_enabled,
            "has_password": bool(c.routers.backup.api_password),
            "variables_file": c.routers.backup.variables_file,
        },
        "enabled_groups": c.sync.enabled_groups,
        "provisioning": {
            "orchestrator_url": c.provisioning.orchestrator_url,
            "api_user": c.provisioning.api_user,
            "deploy_scripts": c.provisioning.deploy_scripts,
        },
    }


@app.post("/api/setup/config")
async def api_setup_save_config(request: Request):
    """Save router connection settings, persist to YAML, and reconnect clients."""
    orch = get_orchestrator()
    body = await request.json()

    def _apply_router_fields(cfg_router: Any, data: dict[str, Any]) -> None:
        if "name" in data:
            cfg_router.name = data["name"]
        if "api_url" in data:
            cfg_router.api_url = data["api_url"]
        if "api_user" in data:
            cfg_router.api_user = data["api_user"]
        if "api_password" in data and data["api_password"]:
            cfg_router.api_password = data["api_password"]
            # Clear env var reference — password is now managed via UI
            cfg_router.api_password_env = ""
        if "ssh_port" in data:
            cfg_router.ssh_port = int(data["ssh_port"])
        if "ssh_enabled" in data:
            cfg_router.ssh_enabled = bool(data["ssh_enabled"])

    # Update master settings
    if "master" in body:
        _apply_router_fields(orch.config.routers.master, body["master"])

    # Update backup settings
    if "backup" in body:
        _apply_router_fields(orch.config.routers.backup, body["backup"])

    # Update cluster name if provided
    if "cluster_name" in body:
        orch.config.cluster.name = body["cluster_name"]

    # Persist to YAML
    save_config(orch.config, orch.config_path)

    # Reconnect clients with new credentials
    await orch.reconnect_clients()

    return {"status": "ok", "message": "Configuration saved and clients reconnected"}


@app.get("/api/setup/discover-master")
async def api_setup_discover_master():
    """Fetch comprehensive master router config to prepare slave provisioning.

    Returns system info, interfaces, IP addresses, VRRP instances, firewall
    summary, routes, and other key configuration from the master router.
    Only requires the master to be reachable.
    """
    orch = get_orchestrator()
    result: dict[str, Any] = {"reachable": False}

    try:
        resource = await orch.master_client.get_system_resource()
        identity = await orch.master_client.get_identity()
        result["reachable"] = True
        result["identity"] = identity
        result["version"] = resource.get("version", "")
        result["board"] = resource.get("board-name", "")
        result["uptime"] = resource.get("uptime", "")
        result["cpu_load"] = resource.get("cpu-load", "")
        result["architecture"] = resource.get("architecture-name", "")
    except Exception as e:
        result["error"] = f"Master unreachable: {e}"
        return result

    # Fetch key sections from master
    discovery_paths = {
        "interfaces": "interface",
        "ip_addresses": "ip/address",
        "vrrp": "interface/vrrp",
        "firewall_filter": "ip/firewall/filter",
        "firewall_nat": "ip/firewall/nat",
        "firewall_mangle": "ip/firewall/mangle",
        "firewall_raw": "ip/firewall/raw",
        "address_lists": "ip/firewall/address-list",
        "routes": "ip/route",
        "dns": "ip/dns",
        "ntp": "system/ntp/client",
        "dhcp_servers": "ip/dhcp-server",
        "dhcp_networks": "ip/dhcp-server/network",
        "dhcp_leases": "ip/dhcp-server/lease",
        "ip_pools": "ip/pool",
        "bridges": "interface/bridge",
        "bridge_ports": "interface/bridge/port",
        "vlans": "interface/vlan",
        "bonding": "interface/bonding",
        "interface_lists": "interface/list",
        "scripts": "system/script",
        "schedulers": "system/scheduler",
        "users": "user",
        "ip_services": "ip/service",
        "wireguard": "interface/wireguard",
        "wireguard_peers": "interface/wireguard/peers",
        "ipsec_peers": "ip/ipsec/peer",
        "ipsec_policies": "ip/ipsec/policy",
        "queue_simple": "queue/simple",
        "queue_tree": "queue/tree",
    }

    sections: dict[str, Any] = {}
    for key, path in discovery_paths.items():
        try:
            items = await orch.master_client.get(path)
            static_items = [i for i in items if i.get("dynamic") != "true"]
            sections[key] = {
                "total": len(items),
                "static": len(static_items),
                "items": static_items,
            }
        except Exception:
            sections[key] = {"total": 0, "static": 0, "items": [], "error": True}

    result["sections"] = sections

    # Build a summary for easy consumption
    summary = {}
    for key, data in sections.items():
        summary[key] = data["static"]
    result["summary"] = summary

    return result


@app.get("/api/setup/router-info/{role}")
async def api_setup_router_info(role: str):
    """Fetch live data from a router (REST + SSH).

    Returns system info, IP addresses, interfaces summary, VRRP,
    firewall counts, routes, and optionally SSH-sourced export.
    """
    orch = get_orchestrator()
    if role not in ("master", "backup"):
        raise HTTPException(400, f"Unknown role: {role}")

    router_cfg = orch.config.routers.master if role == "master" else orch.config.routers.backup
    client = orch.master_client if role == "master" else orch.slave_client

    result: dict[str, Any] = {
        "role": role,
        "name": router_cfg.name,
        "api_url": router_cfg.api_url,
        "rest_reachable": False,
        "ssh_reachable": False,
    }

    # --- REST API data ---
    try:
        resource = await client.get_system_resource()
        identity = await client.get_identity()
        result["rest_reachable"] = True
        result["identity"] = identity
        result["version"] = resource.get("version", "")
        result["board"] = resource.get("board-name", "")
        result["architecture"] = resource.get("architecture-name", "")
        result["uptime"] = resource.get("uptime", "")
        result["cpu"] = resource.get("cpu", "")
        result["cpu_count"] = resource.get("cpu-count", "")
        result["cpu_load"] = resource.get("cpu-load", "")
        result["total_memory"] = resource.get("total-memory", "")
        result["free_memory"] = resource.get("free-memory", "")
        result["total_hdd"] = resource.get("total-hdd-space", "")
        result["free_hdd"] = resource.get("free-hdd-space", "")
    except RouterOSNotMikroTikError as e:
        result["rest_error"] = str(e)
        result["not_mikrotik"] = True
    except Exception as e:
        result["rest_error"] = str(e)

    # Fetch key sections via REST if reachable
    if result["rest_reachable"]:
        sections: dict[str, Any] = {}
        rest_paths = {
            "ip_addresses": "ip/address",
            "interfaces": "interface",
            "vrrp": "interface/vrrp",
            "routes": "ip/route",
            "firewall_filter": "ip/firewall/filter",
            "firewall_nat": "ip/firewall/nat",
            "firewall_mangle": "ip/firewall/mangle",
            "firewall_raw": "ip/firewall/raw",
            "address_lists": "ip/firewall/address-list",
            "bridges": "interface/bridge",
            "bridge_ports": "interface/bridge/port",
            "vlans": "interface/vlan",
            "bonding": "interface/bonding",
            "interface_lists": "interface/list",
            "dhcp_servers": "ip/dhcp-server",
            "dhcp_networks": "ip/dhcp-server/network",
            "dhcp_leases": "ip/dhcp-server/lease",
            "ip_pools": "ip/pool",
            "dns_static": "ip/dns/static",
            "scripts": "system/script",
            "schedulers": "system/scheduler",
            "users": "user",
            "ip_services": "ip/service",
            "wireguard": "interface/wireguard",
            "wireguard_peers": "interface/wireguard/peers",
            "ipsec_peers": "ip/ipsec/peer",
            "ipsec_policies": "ip/ipsec/policy",
            "queue_simple": "queue/simple",
            "queue_tree": "queue/tree",
        }
        for key, path in rest_paths.items():
            try:
                items = await client.get(path)
                static = [i for i in items if i.get("dynamic") != "true"]
                sections[key] = {"count": len(static), "items": static}
            except Exception:
                sections[key] = {"count": 0, "items": [], "error": True}
        result["sections"] = sections

    # --- SSH data ---
    if router_cfg.ssh_enabled:
        import asyncio
        loop = asyncio.get_event_loop()
        try:
            ssh = MikroTikSSHClient.from_api_url(
                api_url=router_cfg.api_url,
                username=router_cfg.api_user,
                password=router_cfg.api_password,
                ssh_port=router_cfg.ssh_port,
                timeout=8.0,
                key_file=router_cfg.ssh_key_file,
            )
            # Run SSH in a thread to avoid blocking the event loop
            def _ssh_fetch() -> dict[str, Any]:
                ssh_data: dict[str, Any] = {}
                try:
                    ssh.connect()
                    ssh_data["reachable"] = True
                    ssh_data["identity"] = ssh.get_identity()
                    ssh_data["system_info"] = ssh.get_system_info()
                    ssh_data["export_compact"] = ssh.export(verbose=False)
                except SSHError as e:
                    ssh_data["reachable"] = False
                    ssh_data["error"] = str(e)
                except Exception as e:
                    ssh_data["reachable"] = False
                    ssh_data["error"] = str(e)
                finally:
                    ssh.close()
                return ssh_data

            ssh_result = await loop.run_in_executor(None, _ssh_fetch)
            result["ssh_reachable"] = ssh_result.get("reachable", False)
            result["ssh"] = ssh_result
        except Exception as e:
            result["ssh"] = {"reachable": False, "error": str(e)}

    return result


@app.post("/api/setup/test-connection")
async def api_setup_test_connection(request: Request):
    """Test connection to a router."""
    body = await request.json()
    url = body.get("url", "")
    username = body.get("username", "admin")
    password = body.get("password", "")

    if not url:
        return {"reachable": False, "error": "No URL provided"}

    client = RouterOSClient(
        base_url=url, username=username, password=password,
        timeout=5.0, max_retries=1, retry_delay=0.5,
    )
    try:
        async with client:
            resource = await client.get_system_resource()
            identity = await client.get_identity()
            return {
                "reachable": True,
                "version": resource.get("version", "unknown"),
                "identity": identity,
                "uptime": resource.get("uptime", ""),
                "board": resource.get("board-name", ""),
                "cpu_load": resource.get("cpu-load", ""),
            }
    except RouterOSNotMikroTikError as e:
        return {
            "reachable": False,
            "not_mikrotik": True,
            "error": str(e),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}


@app.get("/api/setup/section-groups")
async def api_setup_section_groups():
    """Return section groups with enabled/disabled state."""
    orch = get_orchestrator()
    enabled = set(orch.config.sync.enabled_groups)
    return [
        {
            "name": name,
            "label": info["label"],
            "enabled": name in enabled,
            "sections": info["sections"],
            "section_count": len(info["sections"]),
        }
        for name, info in SECTION_GROUPS.items()
    ]


@app.post("/api/setup/section-groups")
async def api_setup_update_section_groups(request: Request):
    """Update which section groups are enabled."""
    body = await request.json()
    groups = body.get("groups", [])

    orch = get_orchestrator()
    orch.config.sync.enabled_groups = groups
    # Re-expand sections from groups
    from src.utils.config import expand_groups_to_sections
    orch.config.sync.sections = expand_groups_to_sections(groups)
    # Re-initialize sync engine with new sections
    await orch.sync_engine.initialize()

    return {"status": "ok", "enabled_groups": groups, "sections": orch.config.sync.sections}


@app.get("/api/setup/section-counts")
async def api_setup_section_counts():
    """Count items per section group on both routers."""
    orch = get_orchestrator()
    result: list[dict[str, Any]] = []

    # Map API paths for each group
    group_paths: dict[str, list[str]] = {
        "interfaces": [
            "interface", "interface/bridge", "interface/bridge/port",
            "interface/vlan", "interface/bonding",
            "interface/list",
        ],
        "ip_addressing": ["ip/address"],
        "firewall": [
            "ip/firewall/filter", "ip/firewall/nat",
            "ip/firewall/mangle", "ip/firewall/raw",
            "ip/firewall/address-list",
        ],
        "routing": ["ip/route"],
        "dhcp_dns": [
            "ip/dhcp-server", "ip/dhcp-server/network",
            "ip/dhcp-server/lease", "ip/pool", "ip/dns/static",
        ],
        "vpn": ["ip/ipsec/peer", "ip/ipsec/policy", "interface/wireguard"],
        "scripts": ["system/script", "system/scheduler"],
        "queues": ["queue/simple", "queue/tree"],
    }

    for group_name, group_info in SECTION_GROUPS.items():
        paths = group_paths.get(group_name, [])
        master_count = 0
        slave_count = 0

        for path in paths:
            try:
                m_items = await orch.master_client.get(path)
                master_count += len([i for i in m_items if i.get("dynamic") != "true"])
            except Exception:
                pass
            try:
                s_items = await orch.slave_client.get(path)
                slave_count += len([i for i in s_items if i.get("dynamic") != "true"])
            except Exception:
                pass

        result.append({
            "name": group_name,
            "label": group_info["label"],
            "master_count": master_count,
            "slave_count": slave_count,
        })

    return result


# ============================================================
# API: Provisioning
# ============================================================

@app.post("/api/provision/plan")
async def api_provision_plan(request: Request):
    """Generate provisioning plan (dry-run)."""
    orch = get_orchestrator()
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    force = body.get("force", False)

    plan = await orch.provisioning_engine.plan(force=force)
    return plan.to_dict()


@app.post("/api/provision/apply")
async def api_provision_apply(request: Request):
    """Execute provisioning."""
    orch = get_orchestrator()
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    force = body.get("force", False)
    skip_verification = body.get("skip_verification", False)

    report = await orch.provisioning_engine.provision(
        force=force, skip_verification=skip_verification,
    )
    return report.to_dict()


@app.get("/api/provision/status")
async def api_provision_status():
    """Get current provisioning status (polling)."""
    orch = get_orchestrator()
    report = orch.provisioning_engine.current_report
    if report:
        return {"running": True, "report": report.to_dict()}
    return {"running": False, "report": None}


# ============================================================
# API: User Management (authentication)
# ============================================================

@app.post("/api/setup/auth/create-user")
async def api_create_user(request: Request):
    """Create or update a web UI user."""
    from src.utils.auth import hash_password

    orch = get_orchestrator()
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))

    if not username or not password:
        raise HTTPException(400, "Username and password required")
    if len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")

    pw_hash, salt = hash_password(password)
    new_user = AuthUser(username=username, password_hash=pw_hash, salt=salt)

    # Update existing or add new
    for i, user in enumerate(orch.config.web.auth_users):
        if user.username == username:
            orch.config.web.auth_users[i] = new_user
            save_config(orch.config, orch.config_path)
            return {"status": "ok", "message": f"User '{username}' updated"}

    orch.config.web.auth_users.append(new_user)
    save_config(orch.config, orch.config_path)
    return {"status": "ok", "message": f"User '{username}' created"}


@app.delete("/api/setup/auth/delete-user/{username}")
async def api_delete_user(username: str):
    """Delete a web UI user."""
    orch = get_orchestrator()
    before = len(orch.config.web.auth_users)
    orch.config.web.auth_users = [
        u for u in orch.config.web.auth_users if u.username != username
    ]
    if len(orch.config.web.auth_users) == before:
        raise HTTPException(404, f"User '{username}' not found")
    save_config(orch.config, orch.config_path)
    return {"status": "ok", "message": f"User '{username}' deleted"}


@app.get("/api/setup/auth/users")
async def api_list_users():
    """List configured auth users (without password hashes)."""
    orch = get_orchestrator()
    return {
        "users": [{"username": u.username} for u in orch.config.web.auth_users],
        "auth_enabled": len(orch.config.web.auth_users) > 0,
    }


# ============================================================
# API: Credential Encryption
# ============================================================

@app.post("/api/setup/encrypt-credentials")
async def api_encrypt_credentials(request: Request):
    """Encrypt current router passwords and save to credentials file."""
    from src.utils.crypto import collect_sensitive_fields, encrypt_credentials

    orch = get_orchestrator()
    body = await request.json()
    enc_password = str(body.get("password", ""))
    if not enc_password:
        raise HTTPException(400, "Encryption password required")
    if len(enc_password) < 4:
        raise HTTPException(400, "Encryption password must be at least 4 characters")

    creds = collect_sensitive_fields(orch.config)
    if not creds:
        raise HTTPException(400, "No sensitive fields to encrypt")

    encrypted = encrypt_credentials(creds, enc_password)

    config_dir = Path(orch.config_path).parent
    creds_filename = ".credentials.enc"
    creds_path = config_dir / creds_filename
    creds_path.write_bytes(encrypted)

    orch.config.credentials_file = creds_filename
    save_config(orch.config, orch.config_path)

    return {"status": "ok", "credentials_count": len(creds), "file": creds_filename}


@app.get("/api/setup/encryption-status")
async def api_encryption_status():
    """Check whether credentials are encrypted."""
    orch = get_orchestrator()
    config_dir = Path(orch.config_path).parent
    creds_file = orch.config.credentials_file
    has_file = bool(creds_file) and (config_dir / creds_file).exists()
    return {
        "encrypted": has_file,
        "credentials_file": creds_file if has_file else None,
    }


# ============================================================
# API: Backup & Restore
# ============================================================

@app.get("/api/backup/create")
async def api_backup_create(password: str = ""):
    """Create and download a full configuration backup."""
    from src.utils.backup import create_backup

    orch = get_orchestrator()
    zip_bytes, filename = await create_backup(orch, encryption_password=password)

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/api/backup/validate")
async def api_backup_validate(request: Request):
    """Validate an uploaded backup file."""
    from src.utils.backup import validate_backup

    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(400, "No file uploaded")
    content = await upload.read()
    return validate_backup(content)


@app.post("/api/backup/restore")
async def api_backup_restore(request: Request):
    """Restore configuration from an uploaded backup file."""
    from src.utils.backup import restore_backup

    orch = get_orchestrator()
    form = await request.form()
    upload = form.get("file")
    if not upload:
        raise HTTPException(400, "No file uploaded")
    content = await upload.read()
    enc_password = str(form.get("encryption_password", ""))
    result = await restore_backup(content, orch, encryption_password=enc_password)
    return {"status": "ok", **result}
