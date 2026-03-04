"""Tests for the quorum manager decision logic."""

from src.quorum.health import HealthResult, RouterStatus
from src.quorum.manager import ClusterState, FailoverAction, QuorumManager
from src.utils.config import (
    ClusterConfig, HAConfig, HealthCheckConfig, QuorumConfig,
    RouterConfig, RoutersConfig, SyncConfig, WitnessConfig,
)


def _make_config(**overrides) -> HAConfig:
    return HAConfig(
        cluster=ClusterConfig(
            failover_cooldown_seconds=overrides.get("cooldown", 0),
        ),
        routers=RoutersConfig(
            master=RouterConfig(
                name="master", api_url="https://10.0.0.1/rest", api_password="x"
            ),
            backup=RouterConfig(
                name="backup", api_url="https://10.0.0.2/rest", api_password="y"
            ),
        ),
        quorum=QuorumConfig(
            witness=WitnessConfig(fail_threshold=overrides.get("threshold", 3)),
        ),
    )


class TestQuorumDecision:
    def test_both_healthy(self):
        config = _make_config()
        qm = QuorumManager(config, None, None)  # type: ignore

        m = HealthResult(router_name="master", status=RouterStatus.HEALTHY, api_reachable=True)
        b = HealthResult(router_name="backup", status=RouterStatus.HEALTHY, api_reachable=True)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.NORMAL
        assert decision.action == FailoverAction.NONE

    def test_master_down_below_threshold(self):
        config = _make_config(threshold=3)
        qm = QuorumManager(config, None, None)  # type: ignore
        qm._master_fail_count = 1  # Below threshold

        m = HealthResult(router_name="master", status=RouterStatus.UNREACHABLE)
        b = HealthResult(router_name="backup", status=RouterStatus.HEALTHY, api_reachable=True)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.DEGRADED
        assert decision.action == FailoverAction.ALERT_ONLY

    def test_master_down_above_threshold(self):
        config = _make_config(threshold=3, cooldown=0)
        qm = QuorumManager(config, None, None)  # type: ignore
        qm._master_fail_count = 3  # At threshold

        m = HealthResult(router_name="master", status=RouterStatus.UNREACHABLE)
        b = HealthResult(router_name="backup", status=RouterStatus.HEALTHY, api_reachable=True)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.FAILOVER
        assert decision.action == FailoverAction.PROMOTE_BACKUP

    def test_backup_down(self):
        config = _make_config()
        qm = QuorumManager(config, None, None)  # type: ignore

        m = HealthResult(router_name="master", status=RouterStatus.HEALTHY, api_reachable=True)
        b = HealthResult(router_name="backup", status=RouterStatus.UNREACHABLE)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.DEGRADED
        assert decision.action == FailoverAction.ALERT_ONLY

    def test_both_down(self):
        config = _make_config()
        qm = QuorumManager(config, None, None)  # type: ignore

        m = HealthResult(router_name="master", status=RouterStatus.UNREACHABLE)
        b = HealthResult(router_name="backup", status=RouterStatus.UNREACHABLE)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.OFFLINE
        assert decision.action == FailoverAction.ALERT_ONLY

    def test_restore_after_failover(self):
        config = _make_config(cooldown=0)
        qm = QuorumManager(config, None, None)  # type: ignore
        qm._cluster_state = ClusterState.FAILOVER  # Was in failover

        m = HealthResult(router_name="master", status=RouterStatus.HEALTHY, api_reachable=True)
        b = HealthResult(router_name="backup", status=RouterStatus.HEALTHY, api_reachable=True)

        decision = qm._decide(m, b)
        assert decision.action == FailoverAction.RESTORE_MASTER

    def test_degraded_master(self):
        config = _make_config()
        qm = QuorumManager(config, None, None)  # type: ignore

        m = HealthResult(
            router_name="master", status=RouterStatus.DEGRADED,
            api_reachable=True, cpu_load=95,
        )
        b = HealthResult(router_name="backup", status=RouterStatus.HEALTHY, api_reachable=True)

        decision = qm._decide(m, b)
        assert decision.cluster_state == ClusterState.NORMAL
        assert decision.action == FailoverAction.NONE
