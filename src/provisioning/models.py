"""Data models for the Day Zero Provisioning workflow."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProvisioningPhase(str, Enum):
    PREFLIGHT = "preflight"
    SYSTEM_SETUP = "system_setup"
    NETWORK_SYNC = "network_sync"
    VRRP_SETUP = "vrrp_setup"
    SCRIPT_DEPLOY = "script_deploy"
    VERIFICATION = "verification"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class ProvisioningStep:
    """A single step within a provisioning phase."""

    phase: ProvisioningPhase
    name: str
    description: str
    status: StepStatus = StepStatus.PENDING
    detail: str = ""
    error: str | None = None
    started_at: float | None = None
    completed_at: float | None = None

    @property
    def duration_ms(self) -> float:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at) * 1000
        return 0

    def start(self) -> None:
        self.status = StepStatus.RUNNING
        self.started_at = time.time()

    def complete(self, detail: str = "") -> None:
        self.status = StepStatus.COMPLETED
        self.detail = detail
        self.completed_at = time.time()

    def fail(self, error: str) -> None:
        self.status = StepStatus.FAILED
        self.error = error
        self.completed_at = time.time()

    def skip(self, reason: str = "") -> None:
        self.status = StepStatus.SKIPPED
        self.detail = reason
        self.completed_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "detail": self.detail,
            "error": self.error,
            "duration_ms": round(self.duration_ms, 1),
        }


@dataclass
class PreflightResult:
    """Results of all pre-flight checks."""

    secondary_reachable: bool = False
    secondary_version: str = ""
    master_reachable: bool = False
    master_version: str = ""
    version_compatible: bool = False
    secondary_is_blank: bool = False
    secondary_config_items: int = 0
    master_identity: str = ""
    secondary_identity: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.secondary_reachable
            and self.master_reachable
            and self.version_compatible
            and len(self.errors) == 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "secondary_reachable": self.secondary_reachable,
            "master_reachable": self.master_reachable,
            "secondary_version": self.secondary_version,
            "master_version": self.master_version,
            "version_compatible": self.version_compatible,
            "secondary_is_blank": self.secondary_is_blank,
            "secondary_config_items": self.secondary_config_items,
            "master_identity": self.master_identity,
            "secondary_identity": self.secondary_identity,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class ProvisioningPlan:
    """The complete plan of what provisioning will do (dry-run output)."""

    preflight: PreflightResult = field(default_factory=PreflightResult)
    system_changes: list[dict[str, Any]] = field(default_factory=list)
    network_diff_summary: dict[str, Any] = field(default_factory=dict)
    vrrp_instances_to_create: list[dict[str, Any]] = field(default_factory=list)
    scripts_to_deploy: list[str] = field(default_factory=list)
    schedulers_to_create: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "preflight": self.preflight.to_dict(),
            "system_changes": self.system_changes,
            "network_diff_summary": self.network_diff_summary,
            "vrrp_instances": self.vrrp_instances_to_create,
            "scripts": self.scripts_to_deploy,
            "schedulers": self.schedulers_to_create,
        }


@dataclass
class ProvisioningReport:
    """Final report of a provisioning operation."""

    started_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    success: bool = False
    steps: list[ProvisioningStep] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        if self.completed_at:
            return (self.completed_at - self.started_at) * 1000
        return 0

    @property
    def current_phase(self) -> str | None:
        for step in reversed(self.steps):
            if step.status == StepStatus.RUNNING:
                return step.phase.value
        return None

    @property
    def progress_percent(self) -> int:
        if not self.steps:
            return 0
        done = sum(
            1 for s in self.steps
            if s.status in (StepStatus.COMPLETED, StepStatus.SKIPPED)
        )
        return int((done / len(self.steps)) * 100)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "duration_ms": round(self.duration_ms, 1),
            "progress_percent": self.progress_percent,
            "current_phase": self.current_phase,
            "steps": [s.to_dict() for s in self.steps],
            "errors": self.errors,
            "warnings": self.warnings,
        }
