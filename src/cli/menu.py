"""CLI/TUI menu for MikroTik HA management via terminal."""

from __future__ import annotations

import asyncio
import sys

import httpx
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

console = Console()

# The CLI connects to the same FastAPI backend
BASE_URL = "http://localhost:8080"


async def api_get(path: str) -> dict | list:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        r = await client.get(path)
        r.raise_for_status()
        return r.json()


async def api_post(path: str, data: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        r = await client.post(path, json=data or {})
        r.raise_for_status()
        return r.json()


async def show_status():
    """Display cluster status."""
    data = await api_get("/api/status")
    cluster = data["cluster"]
    quorum = data["quorum"]

    console.print(Panel(
        f"[bold]State:[/bold] {cluster['state']}\n"
        f"[bold]Uptime:[/bold] {cluster['uptime']}s",
        title=f"Cluster: {cluster['name']}",
    ))

    # Router health table
    table = Table(title="Router Health")
    table.add_column("Router")
    table.add_column("Status")
    table.add_column("CPU")
    table.add_column("Memory")
    table.add_column("Uptime")
    table.add_column("Version")

    for role in ("master_health", "backup_health"):
        h = quorum.get(role)
        if h:
            status_style = {"healthy": "green", "degraded": "yellow", "unreachable": "red"}.get(
                h["status"], "dim"
            )
            table.add_row(
                h["router_name"],
                f"[{status_style}]{h['status']}[/]",
                f"{h['cpu_load']}%",
                f"{h['memory_used_percent']}%",
                h["uptime"] or "-",
                h["version"] or "-",
            )

    console.print(table)

    # Last sync
    if data.get("last_sync"):
        ls = data["last_sync"]
        style = "green" if ls["success"] else "red"
        console.print(f"\n[{style}]Last sync:[/] {ls['timestamp']} - "
                       f"{ls['total_changes']} changes - {ls['duration_ms']:.0f}ms")


async def show_diff():
    """Show current config diff."""
    data = await api_get("/api/diff")

    console.print(f"\n[bold]Total changes: {data['total_changes']}[/bold]\n")

    for section in data["sections"]:
        if not section["has_changes"]:
            continue
        console.print(f"[yellow]{section['summary']}[/yellow]")
        for a in section["details"]["additions"]:
            console.print(f"  [green]+ {a['data']}[/green]")
        for u in section["details"]["updates"]:
            console.print(f"  [yellow]~ {u['item_id']}: {u['changes']}[/yellow]")
        for r in section["details"]["removals"]:
            console.print(f"  [red]- {r['data']}[/red]")


async def trigger_sync(dry_run: bool = False):
    """Trigger sync."""
    label = "Dry run" if dry_run else "Sync"
    console.print(f"[bold]{label} in progress...[/bold]")
    data = await api_post("/api/sync", {"dry_run": dry_run})
    style = "green" if data.get("success", True) else "red"
    console.print(f"[{style}]{label} complete: {data['total_changes']} changes[/]")
    if data.get("errors"):
        for e in data["errors"]:
            console.print(f"  [red]{e}[/red]")


async def trigger_failover():
    """Manual failover."""
    confirm = Prompt.ask("Are you sure you want to trigger failover?", choices=["y", "n"])
    if confirm != "y":
        return
    await api_post("/api/failover", {"action": "promote_backup"})
    console.print("[bold red]Failover triggered[/bold red]")


async def show_vrrp():
    """Show VRRP status."""
    data = await api_get("/api/vrrp")
    for role in ("master", "backup"):
        table = Table(title=f"VRRP - {role.title()}")
        table.add_column("Name")
        table.add_column("VRID")
        table.add_column("Priority")
        table.add_column("State")
        for v in data.get(role, []):
            state = "MASTER" if v.get("master") else "BACKUP" if v.get("running") else "DOWN"
            table.add_row(v.get("name", "-"), str(v.get("vrid", "-")),
                          str(v.get("priority", "-")), state)
        console.print(table)


async def show_events():
    """Show recent events."""
    events = await api_get("/api/events?limit=20")
    table = Table(title="Recent Events")
    table.add_column("Time")
    table.add_column("State")
    table.add_column("Action")
    table.add_column("Reason")
    for e in events:
        from datetime import datetime
        ts = datetime.fromtimestamp(e["timestamp"]).strftime("%H:%M:%S")
        table.add_row(ts, e["cluster_state"], e["action"], e["reason"])
    console.print(table)


async def provision_menu():
    """Day Zero Provisioning sub-menu."""
    console.print("\n[bold]Day Zero Provisioning[/bold]")
    console.print("─" * 30)
    console.print("  [1] Pre-flight Check")
    console.print("  [2] Generate Plan")
    console.print("  [3] Apply Provisioning")
    console.print("  [b] Back")

    choice = Prompt.ask("\nSelect", choices=["1", "2", "3", "b"])

    if choice == "b":
        return

    force = Prompt.ask("Force (non-blank router)?", choices=["y", "n"], default="n") == "y"

    if choice == "1":
        console.print("[bold]Running pre-flight checks...[/bold]")
        data = await api_post("/api/provision/plan", {"force": force})
        pf = data.get("preflight", {})
        style = "green" if pf.get("passed") else "red"
        console.print(f"\n[{style}]Pre-flight: {'PASSED' if pf.get('passed') else 'FAILED'}[/]")
        console.print(f"  Master: {pf.get('master_version', '?')} ({pf.get('master_identity', '?')})")
        console.print(f"  Secondary: {pf.get('secondary_version', '?')} ({pf.get('secondary_identity', '?')})")
        console.print(f"  Secondary config items: {pf.get('secondary_config_items', '?')}")
        if pf.get("errors"):
            for e in pf["errors"]:
                console.print(f"  [red]{e}[/red]")
        if pf.get("warnings"):
            for w in pf["warnings"]:
                console.print(f"  [yellow]{w}[/yellow]")

    elif choice == "2":
        console.print("[bold]Generating provisioning plan...[/bold]")
        data = await api_post("/api/provision/plan", {"force": force})
        pf = data.get("preflight", {})
        if not pf.get("passed"):
            console.print("[red]Pre-flight failed, cannot generate plan.[/red]")
            return

        # System changes
        for sc in data.get("system_changes", []):
            console.print(f"  [cyan]{sc['section']}[/cyan]: {sc['action']} "
                          f"{sc.get('current', '')} -> {sc.get('proposed', '')}")

        # Network
        net = data.get("network_diff_summary", {})
        console.print(f"\n  Network sync: {net.get('total_changes', 0)} changes")

        # VRRP
        for v in data.get("vrrp_instances", []):
            console.print(f"  VRRP: {v['name']} VRID={v['vrid']} pri={v['priority']}")

        # Scripts
        for s in data.get("scripts", []):
            console.print(f"  Script: {s}")

    elif choice == "3":
        confirm = Prompt.ask("Apply provisioning to secondary router?", choices=["y", "n"])
        if confirm != "y":
            return
        skip_verify = Prompt.ask("Skip verification?", choices=["y", "n"], default="n") == "y"
        console.print("[bold]Applying provisioning...[/bold]")
        data = await api_post("/api/provision/apply", {
            "force": force, "skip_verification": skip_verify,
        })
        style = "green" if data.get("success") else "red"
        console.print(f"\n[{style}]Provisioning: {'COMPLETE' if data.get('success') else 'FAILED'}[/]")
        console.print(f"  Duration: {data.get('duration_ms', 0) / 1000:.1f}s")
        console.print(f"  Steps: {len(data.get('steps', []))}")
        if data.get("errors"):
            for e in data["errors"]:
                console.print(f"  [red]{e}[/red]")

        # Step details
        table = Table(title="Provisioning Steps")
        table.add_column("Phase")
        table.add_column("Step")
        table.add_column("Status")
        table.add_column("Detail")
        for s in data.get("steps", []):
            st_style = {"completed": "green", "failed": "red", "skipped": "yellow"}.get(
                s["status"], "dim"
            )
            table.add_row(s["phase"], s["description"],
                          f"[{st_style}]{s['status']}[/]",
                          s.get("error") or s.get("detail", ""))
        console.print(table)


MENU_ITEMS = {
    "1": ("Status", show_status),
    "2": ("Config Diff", show_diff),
    "3": ("Sync Now", lambda: trigger_sync(False)),
    "4": ("Dry Run", lambda: trigger_sync(True)),
    "5": ("VRRP Status", show_vrrp),
    "6": ("Recent Events", show_events),
    "7": ("Manual Failover", trigger_failover),
    "8": ("Day Zero Provisioning", provision_menu),
    "q": ("Quit", None),
}


async def run_menu():
    """Main interactive menu loop."""
    while True:
        console.print("\n[bold]MikroTik HA Manager[/bold]")
        console.print("─" * 30)
        for key, (label, _) in MENU_ITEMS.items():
            console.print(f"  [{key}] {label}")

        choice = Prompt.ask("\nSelect", choices=list(MENU_ITEMS.keys()))

        if choice == "q":
            break

        _, handler = MENU_ITEMS[choice]
        if handler:
            try:
                await handler()
            except httpx.ConnectError:
                console.print("[red]Cannot connect to MKHA server. Is it running?[/red]")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")


def main():
    """Entry point for the CLI."""
    try:
        asyncio.run(run_menu())
    except KeyboardInterrupt:
        console.print("\nBye!")
        sys.exit(0)


if __name__ == "__main__":
    main()
