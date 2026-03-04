"""Day Zero Provisioning Engine.

Orchestrates the full provisioning workflow:
1. Pre-flight checks
2. System-level configuration
3. Network configuration (via existing SyncEngine)
4. VRRP instance creation
5. HA script deployment
6. Post-provisioning verification
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine

from src.api.routeros_client import RouterOSClient
from src.provisioning.models import (
    ProvisioningPhase,
    ProvisioningPlan,
    ProvisioningReport,
    ProvisioningStep,
    StepStatus,
)
from src.provisioning.preflight import run_preflight
from src.provisioning.script_deploy import ScriptDeployer
from src.provisioning.system_setup import SystemSetup
from src.provisioning.verification import ProvisioningVerifier
from src.provisioning.vrrp_setup import VRRPSetup
from src.sync.engine import SyncEngine
from src.utils.config import HAConfig
from src.utils.logging import get_logger

log = get_logger(__name__)

ProgressCallback = Callable[[ProvisioningStep], Coroutine[Any, Any, None]]


class ProvisioningEngine:
    """Orchestrates Day Zero provisioning of a secondary router."""

    def __init__(
        self,
        config: HAConfig,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
        sync_engine: SyncEngine,
    ):
        self.config = config
        self.master_client = master_client
        self.slave_client = slave_client
        self.sync_engine = sync_engine

        self._system_setup = SystemSetup(config)
        self._vrrp_setup: VRRPSetup | None = None
        self._script_deployer = ScriptDeployer(config)
        self._verifier = ProvisioningVerifier()

        self._lock = asyncio.Lock()
        self._current_report: ProvisioningReport | None = None

    async def initialize(self) -> None:
        """Initialize sub-components. Call after sync_engine.initialize()."""
        if self.sync_engine.translator:
            self._vrrp_setup = VRRPSetup(self.config, self.sync_engine.translator)

    async def plan(self, force: bool = False) -> ProvisioningPlan:
        """Generate a provisioning plan (dry-run). Shows what WOULD be done."""
        plan = ProvisioningPlan()

        # 1. Pre-flight
        plan.preflight = await run_preflight(
            self.master_client, self.slave_client,
            self.config.routers.master.name,
            self.config.routers.backup.name,
            blank_threshold=self.config.provisioning.blank_threshold,
            force=force,
        )
        if not plan.preflight.passed:
            return plan

        # 2. System changes
        plan.system_changes = await self._system_setup.plan(
            self.master_client, self.slave_client
        )

        # 3. Network diff (reuse SyncEngine)
        try:
            diffs = await self.sync_engine.compute_diff()
            plan.network_diff_summary = {
                "total_changes": sum(d.total_changes for d in diffs),
                "sections": [
                    {
                        "name": d.section,
                        "additions": len(d.additions),
                        "updates": len(d.updates),
                        "removals": len(d.removals),
                        "moves": len(d.moves),
                    }
                    for d in diffs
                    if d.has_changes
                ],
            }
        except Exception as e:
            plan.network_diff_summary = {"total_changes": 0, "error": str(e), "sections": []}

        # 4. VRRP plan
        if self._vrrp_setup:
            plan.vrrp_instances_to_create = await self._vrrp_setup.plan(
                self.master_client, self.slave_client
            )

        # 5. Script plan
        plan.scripts_to_deploy, plan.schedulers_to_create = \
            await self._script_deployer.plan(self.slave_client)

        return plan

    async def provision(
        self,
        force: bool = False,
        skip_verification: bool = False,
    ) -> ProvisioningReport:
        """Execute the full provisioning workflow."""
        async with self._lock:
            report = ProvisioningReport()
            self._current_report = report

            try:
                # Phase 1: Pre-flight
                await self._run_preflight(report, force)
                if report.errors:
                    report.success = False
                    return report

                # Phase 2: System setup
                await self._run_system_setup(report)
                if any(s.status == StepStatus.FAILED for s in report.steps
                       if s.phase == ProvisioningPhase.SYSTEM_SETUP
                       and s.name in ("set_identity",)):
                    report.errors.append("Critical system setup failed")
                    report.success = False
                    return report

                # Phase 3: Network sync
                await self._run_network_sync(report)

                # Phase 4: VRRP setup
                await self._run_vrrp_setup(report)

                # Phase 5: Script deployment
                await self._run_script_deploy(report)

                # Phase 6: Verification
                if not skip_verification:
                    await self._run_verification(report)

                report.success = len(report.errors) == 0

            except Exception as e:
                report.errors.append(f"Provisioning failed: {e}")
                await log.aerror("provisioning_failed", error=str(e))

            finally:
                report.completed_at = time.time()
                self._current_report = None

            await log.ainfo("provisioning_complete",
                            success=report.success,
                            duration_ms=report.duration_ms,
                            steps=len(report.steps),
                            errors=len(report.errors))
            return report

    async def _run_preflight(self, report: ProvisioningReport, force: bool) -> None:
        step = ProvisioningStep(
            phase=ProvisioningPhase.PREFLIGHT,
            name="preflight_checks",
            description="Running pre-flight checks",
        )
        step.start()
        report.steps.append(step)

        result = await run_preflight(
            self.master_client, self.slave_client,
            self.config.routers.master.name,
            self.config.routers.backup.name,
            blank_threshold=self.config.provisioning.blank_threshold,
            force=force,
        )

        if result.passed:
            step.complete(
                f"Master: {result.master_version}, "
                f"Secondary: {result.secondary_version}, "
                f"Items: {result.secondary_config_items}"
            )
        else:
            step.fail("; ".join(result.errors))
            report.errors.extend(result.errors)

        report.warnings.extend(result.warnings)

    async def _run_system_setup(self, report: ProvisioningReport) -> None:
        steps = await self._system_setup.apply(
            self.master_client, self.slave_client
        )
        report.steps.extend(steps)
        for s in steps:
            if s.status == StepStatus.FAILED:
                await log.aerror("system_setup_failed", step=s.name, error=s.error)

    async def _run_network_sync(self, report: ProvisioningReport) -> None:
        step = ProvisioningStep(
            phase=ProvisioningPhase.NETWORK_SYNC,
            name="network_sync",
            description="Synchronizing network configuration from master",
        )
        step.start()
        report.steps.append(step)

        try:
            sync_report = await self.sync_engine.sync(dry_run=False)
            if sync_report.success:
                step.complete(
                    f"{sync_report.total_changes} changes applied "
                    f"in {sync_report.duration_ms:.0f}ms"
                )
            else:
                step.fail("; ".join(sync_report.errors))
                report.errors.extend(sync_report.errors)
        except Exception as e:
            step.fail(str(e))
            report.errors.append(f"Network sync failed: {e}")

    async def _run_vrrp_setup(self, report: ProvisioningReport) -> None:
        if self._vrrp_setup:
            steps = await self._vrrp_setup.apply(
                self.master_client, self.slave_client
            )
        else:
            steps = [ProvisioningStep(
                phase=ProvisioningPhase.VRRP_SETUP,
                name="vrrp_skip",
                description="VRRP setup",
            )]
            steps[0].skip("No variable translator available")
        report.steps.extend(steps)

    async def _run_script_deploy(self, report: ProvisioningReport) -> None:
        steps = await self._script_deployer.apply(self.slave_client)
        report.steps.extend(steps)

    async def _run_verification(self, report: ProvisioningReport) -> None:
        steps = await self._verifier.verify(
            self.master_client, self.slave_client
        )
        report.steps.extend(steps)
        for s in steps:
            if s.status == StepStatus.FAILED:
                report.warnings.append(f"Verification: {s.name} - {s.error}")

    @property
    def current_report(self) -> ProvisioningReport | None:
        """Get the in-progress report (for status polling)."""
        return self._current_report
