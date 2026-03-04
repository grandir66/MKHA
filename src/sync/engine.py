"""Sync engine orchestrator - coordinates diff and apply across all sections."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.api.routeros_client import RouterOSClient, RouterOSError
from src.sync.diff import DiffResult
from src.sync.sections import SECTION_REGISTRY, SyncSection
from src.sync.variable_translator import VariableTranslator
from src.utils.config import HAConfig, RouterVariables, load_router_variables
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SyncReport:
    """Report of a sync operation."""

    timestamp: str = ""
    success: bool = False
    diffs: list[DiffResult] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0

    @property
    def has_changes(self) -> bool:
        return any(d.has_changes for d in self.diffs)

    @property
    def total_changes(self) -> int:
        return sum(d.total_changes for d in self.diffs)

    def summary(self) -> str:
        lines = [f"Sync report ({self.timestamp}) - {'OK' if self.success else 'FAILED'}"]
        lines.append(f"  Duration: {self.duration_ms:.0f}ms, Changes: {self.total_changes}")
        for d in self.diffs:
            if d.has_changes:
                lines.append(f"  {d.summary()}")
        if self.errors:
            lines.append(f"  Errors: {len(self.errors)}")
            for e in self.errors:
                lines.append(f"    - {e}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "success": self.success,
            "total_changes": self.total_changes,
            "duration_ms": self.duration_ms,
            "sections": [
                {
                    "name": d.section,
                    "additions": len(d.additions),
                    "updates": len(d.updates),
                    "removals": len(d.removals),
                    "moves": len(d.moves),
                }
                for d in self.diffs
                if d.has_changes
            ],
            "errors": self.errors,
        }


class SyncEngine:
    """Orchestrates configuration sync between master and slave routers."""

    def __init__(
        self,
        config: HAConfig,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ):
        self.config = config
        self.master_client = master_client
        self.slave_client = slave_client
        self._lock = asyncio.Lock()
        self._sections: list[SyncSection] = []
        self._translator: VariableTranslator | None = None

    async def initialize(self) -> None:
        """Load variables and initialize section handlers."""
        config_dir = "config"

        master_vars = RouterVariables()
        slave_vars = RouterVariables()

        if self.config.routers.master.variables_file:
            try:
                master_vars = load_router_variables(
                    f"{config_dir}/{self.config.routers.master.variables_file}"
                )
            except FileNotFoundError:
                await log.awarning("master_variables_file_not_found",
                                   file=self.config.routers.master.variables_file)

        if self.config.routers.backup.variables_file:
            try:
                slave_vars = load_router_variables(
                    f"{config_dir}/{self.config.routers.backup.variables_file}"
                )
            except FileNotFoundError:
                await log.awarning("slave_variables_file_not_found",
                                   file=self.config.routers.backup.variables_file)

        self._translator = VariableTranslator(master_vars, slave_vars)

        # Build section handlers in registry order, filtered by config
        enabled_sections = set(self.config.sync.sections)
        self._sections = []
        for section_name, section_cls in SECTION_REGISTRY.items():
            if section_name in enabled_sections:
                self._sections.append(section_cls(self._translator))

        await log.ainfo(
            "sync_engine_initialized",
            sections=[s.section_name for s in self._sections],
            variable_mappings=len(self._translator._m2s_map),
        )

    async def compute_diff(self) -> list[DiffResult]:
        """Compute diffs for all enabled sections without applying."""
        results: list[DiffResult] = []

        for section in self._sections:
            try:
                diff = await section.diff(self.master_client, self.slave_client)
                results.append(diff)
                if diff.has_changes:
                    await log.ainfo("section_diff", section=section.section_name,
                                    changes=diff.total_changes)
            except RouterOSError as e:
                await log.aerror("section_diff_error", section=section.section_name,
                                 error=str(e))
                error_result = DiffResult(section=section.section_name)
                results.append(error_result)
            except Exception as e:
                await log.aerror("section_diff_unexpected_error",
                                 section=section.section_name, error=str(e))
                error_result = DiffResult(section=section.section_name)
                results.append(error_result)

        return results

    async def sync(self, dry_run: bool = False) -> SyncReport:
        """Perform a full sync: compute diff and apply changes.

        Args:
            dry_run: If True, only compute diff without applying.

        Returns:
            SyncReport with results.
        """
        async with self._lock:
            report = SyncReport(
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            start = asyncio.get_event_loop().time()

            try:
                # Compute diffs
                report.diffs = await self.compute_diff()

                if dry_run:
                    report.success = True
                    report.duration_ms = (asyncio.get_event_loop().time() - start) * 1000
                    return report

                # Apply changes section by section
                for section, diff in zip(self._sections, report.diffs):
                    if not diff.has_changes:
                        continue

                    try:
                        applied = await section.apply(self.slave_client, diff)
                        report.applied.extend(applied)
                        await log.ainfo(
                            "section_applied",
                            section=section.section_name,
                            operations=len(applied),
                        )
                    except RouterOSError as e:
                        error_msg = f"[{section.section_name}] Apply error: {e}"
                        report.errors.append(error_msg)
                        await log.aerror("section_apply_error",
                                         section=section.section_name, error=str(e))
                    except Exception as e:
                        error_msg = f"[{section.section_name}] Unexpected error: {e}"
                        report.errors.append(error_msg)
                        await log.aerror("section_apply_unexpected_error",
                                         section=section.section_name, error=str(e))

                report.success = len(report.errors) == 0

            except Exception as e:
                report.errors.append(f"Sync failed: {e}")
                await log.aerror("sync_failed", error=str(e))

            report.duration_ms = (asyncio.get_event_loop().time() - start) * 1000
            await log.ainfo("sync_complete", report=report.summary())
            return report

    @property
    def translator(self) -> VariableTranslator | None:
        return self._translator

    @property
    def sections(self) -> list[SyncSection]:
        return self._sections
