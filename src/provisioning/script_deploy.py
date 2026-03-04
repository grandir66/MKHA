"""Deploy HA scripts to the secondary router."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.provisioning.models import ProvisioningPhase, ProvisioningStep
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)

SCRIPTS_DIR = Path("scripts/routeros")

DEPLOYMENTS = [
    {
        "script_file": "ha_health_check.rsc",
        "script_name": "ha_health_check",
        "scheduler_name": "ha_health_check",
        "scheduler_interval": "5s",
        "scheduler_on_event": "/system script run ha_health_check",
    },
    {
        "script_file": "ha_failover_hook.rsc",
        "script_name": "ha_failover_hook",
        "scheduler_name": None,
    },
]


class ScriptDeployer:
    """Deploys HA scripts and schedulers to the secondary router."""

    def __init__(self, config: HAConfig):
        self.config = config

    async def plan(
        self, slave_client: RouterOSClient,
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """Return lists of scripts to deploy and schedulers to create."""
        scripts: list[str] = []
        schedulers: list[dict[str, Any]] = []

        try:
            existing_scripts = await slave_client.get("system/script")
            existing_script_names = {s.get("name") for s in existing_scripts}
        except RouterOSError:
            existing_script_names = set()

        try:
            existing_schedulers = await slave_client.get("system/scheduler")
            existing_scheduler_names = {s.get("name") for s in existing_schedulers}
        except RouterOSError:
            existing_scheduler_names = set()

        for dep in DEPLOYMENTS:
            script_file = SCRIPTS_DIR / dep["script_file"]
            if script_file.exists():
                scripts.append(dep["script_name"])

            sched_name = dep.get("scheduler_name")
            if sched_name and sched_name not in existing_scheduler_names:
                interval = dep.get("scheduler_interval",
                                   self.config.provisioning.health_check_interval)
                schedulers.append({
                    "name": sched_name,
                    "interval": interval,
                    "on-event": dep["scheduler_on_event"],
                })

        return scripts, schedulers

    async def apply(
        self,
        slave_client: RouterOSClient,
    ) -> list[ProvisioningStep]:
        """Deploy scripts and create schedulers."""
        steps: list[ProvisioningStep] = []

        if not self.config.provisioning.deploy_scripts:
            step = ProvisioningStep(
                phase=ProvisioningPhase.SCRIPT_DEPLOY,
                name="scripts_disabled",
                description="Script deployment",
            )
            step.skip("Script deployment disabled in config")
            steps.append(step)
            return steps

        # Get existing scripts
        try:
            existing_scripts = await slave_client.get("system/script")
            existing_map = {s.get("name"): s for s in existing_scripts}
        except RouterOSError:
            existing_map = {}

        # Deploy each script
        for dep in DEPLOYMENTS:
            script_file = SCRIPTS_DIR / dep["script_file"]
            script_name = dep["script_name"]

            step = ProvisioningStep(
                phase=ProvisioningPhase.SCRIPT_DEPLOY,
                name=f"deploy_{script_name}",
                description=f"Deploy script '{script_name}'",
            )
            step.start()

            if not script_file.exists():
                step.skip(f"Script file not found: {script_file}")
                steps.append(step)
                continue

            try:
                source = script_file.read_text()
                # Substitute orchestrator URL in script
                orch_url = self.config.provisioning.orchestrator_url
                if orch_url:
                    source = source.replace("http://ORCHESTRATOR_IP:8080", orch_url)
                    source = source.replace("ORCHESTRATOR_IP", orch_url.split("//")[-1].split(":")[0])

                if script_name in existing_map:
                    # Update existing
                    sid = existing_map[script_name].get(".id", "")
                    await slave_client.set("system/script", sid, {"source": source})
                    step.complete(f"Updated script '{script_name}'")
                else:
                    # Create new
                    await slave_client.add("system/script", {
                        "name": script_name,
                        "source": source,
                    })
                    step.complete(f"Created script '{script_name}'")
            except Exception as e:
                step.fail(str(e))
            steps.append(step)

        # Create schedulers
        try:
            existing_schedulers = await slave_client.get("system/scheduler")
            existing_sched_names = {s.get("name") for s in existing_schedulers}
        except RouterOSError:
            existing_sched_names = set()

        for dep in DEPLOYMENTS:
            sched_name = dep.get("scheduler_name")
            if not sched_name:
                continue

            step = ProvisioningStep(
                phase=ProvisioningPhase.SCRIPT_DEPLOY,
                name=f"scheduler_{sched_name}",
                description=f"Create scheduler '{sched_name}'",
            )
            step.start()

            if sched_name in existing_sched_names:
                step.complete(f"Scheduler '{sched_name}' already exists")
                steps.append(step)
                continue

            try:
                interval = dep.get("scheduler_interval",
                                   self.config.provisioning.health_check_interval)
                await slave_client.add("system/scheduler", {
                    "name": sched_name,
                    "interval": interval,
                    "on-event": dep["scheduler_on_event"],
                })
                step.complete(f"Created scheduler '{sched_name}' (interval: {interval})")
            except Exception as e:
                step.fail(str(e))
            steps.append(step)

        return steps
