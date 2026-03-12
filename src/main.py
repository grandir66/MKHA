"""Main entry point - HAOrchestrator runs the sync loop, quorum, and web server."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path
from typing import Any

import uvicorn

from src.api.routeros_client import RouterOSClient
from src.notifications.notifier import Notifier
from src.provisioning.engine import ProvisioningEngine
from src.quorum.manager import ClusterState, FailoverAction, QuorumDecision, QuorumManager
from src.sync.engine import SyncEngine, SyncReport
from src.utils.config import HAConfig, load_config
from src.utils.logging import get_logger, setup_logging
from src.vrrp.controller import VRRPController

log = get_logger(__name__)


class HAOrchestrator:
    """Main orchestrator that ties together all HA components."""

    def __init__(self, config: HAConfig, config_path: str = "config/ha_config.yaml"):
        self.config = config
        self.config_path = config_path
        self._start_time = time.time()

        # Clients
        self.master_client = RouterOSClient(
            base_url=config.routers.master.api_url,
            username=config.routers.master.api_user,
            password=config.routers.master.api_password,
            timeout=config.quorum.health_check.api_timeout_ms / 1000,
        )
        self.slave_client = RouterOSClient(
            base_url=config.routers.backup.api_url,
            username=config.routers.backup.api_user,
            password=config.routers.backup.api_password,
            timeout=config.quorum.health_check.api_timeout_ms / 1000,
        )

        # Derive config directory from config file path
        self._config_dir = str(Path(config_path).parent)

        # Components
        self.sync_engine = SyncEngine(
            config, self.master_client, self.slave_client,
            config_dir=self._config_dir,
        )
        self.quorum = QuorumManager(config, self.master_client, self.slave_client)
        self.vrrp_controller = VRRPController(config, self.master_client, self.slave_client)
        self.notifier = Notifier(config.notifications)
        self.provisioning_engine = ProvisioningEngine(
            config, self.master_client, self.slave_client, self.sync_engine,
        )

        # State
        self.last_sync_report: SyncReport | None = None
        self.log_buffer: deque[dict[str, Any]] = deque(maxlen=500)
        self._running = False
        self._previous_cluster_state = ClusterState.INITIALIZING

    @property
    def uptime_seconds(self) -> int:
        return int(time.time() - self._start_time)

    @property
    def _client_consumers(self) -> list[Any]:
        """All components that hold references to master/slave clients."""
        return [
            self.sync_engine, self.quorum,
            self.vrrp_controller, self.provisioning_engine,
        ]

    async def reconnect_clients(self) -> None:
        """Recreate RouterOS clients from current config (after config change)."""
        await self.master_client.close()
        await self.slave_client.close()

        self.master_client = RouterOSClient(
            base_url=self.config.routers.master.api_url,
            username=self.config.routers.master.api_user,
            password=self.config.routers.master.api_password,
            timeout=self.config.quorum.health_check.api_timeout_ms / 1000,
        )
        self.slave_client = RouterOSClient(
            base_url=self.config.routers.backup.api_url,
            username=self.config.routers.backup.api_user,
            password=self.config.routers.backup.api_password,
            timeout=self.config.quorum.health_check.api_timeout_ms / 1000,
        )

        # Rewire all components with new clients
        for component in self._client_consumers:
            component.master_client = self.master_client
            component.slave_client = self.slave_client

        await self.sync_engine.initialize()
        await self.provisioning_engine.initialize()

    def _add_log(self, event: str, level: str = "info", **kwargs: Any) -> None:
        entry = {"event": event, "level": level, "timestamp": time.time(), **kwargs}
        self.log_buffer.append(entry)

    async def _handle_failover(
        self, action: FailoverAction, decision: QuorumDecision
    ) -> None:
        """Callback from quorum manager when failover action is needed."""
        self._add_log("failover_action", level="warning",
                       action=action.value, reason=decision.reason)
        await self.vrrp_controller.handle_failover(action, decision)
        await self.notifier.notify_failover(decision)

    async def _run_health_loop(self) -> None:
        """Periodic health check loop."""
        interval = self.config.quorum.health_check.interval_seconds
        while self._running:
            try:
                decision = await self.quorum.check()
                self._add_log(
                    "health_check",
                    cluster_state=decision.cluster_state.value,
                    action=decision.action.value,
                )

                # Notify on state changes
                if decision.cluster_state != self._previous_cluster_state:
                    await self.notifier.notify_state_change(
                        self._previous_cluster_state, decision.cluster_state
                    )
                    self._previous_cluster_state = decision.cluster_state

            except Exception as e:
                self._add_log("health_check_error", level="error", error=str(e))
                await log.aerror("health_loop_error", error=str(e))

            await asyncio.sleep(interval)

    async def _run_sync_loop(self) -> None:
        """Periodic config sync loop."""
        interval = self.config.cluster.sync_interval_seconds
        # Wait for first health check to complete
        await asyncio.sleep(5)

        while self._running:
            try:
                # Only sync if cluster is in a stable state
                if self.quorum.cluster_state in (
                    ClusterState.NORMAL, ClusterState.DEGRADED
                ):
                    report = await self.sync_engine.sync()
                    self.last_sync_report = report
                    self._add_log(
                        "sync_complete",
                        success=report.success,
                        changes=report.total_changes,
                        duration_ms=report.duration_ms,
                    )
                    if not report.success:
                        await self.notifier.notify_sync(report.summary(), False)
                else:
                    self._add_log("sync_skipped",
                                   reason=f"cluster state: {self.quorum.cluster_state.value}")

            except Exception as e:
                self._add_log("sync_error", level="error", error=str(e))
                await log.aerror("sync_loop_error", error=str(e))

            await asyncio.sleep(interval)

    async def start(self) -> None:
        """Start the orchestrator (health loop + sync loop + web server)."""
        self._running = True

        # Initialize sync engine and provisioning
        await self.sync_engine.initialize()
        await self.provisioning_engine.initialize()

        # Wire up failover callback
        self.quorum.set_failover_callback(self._handle_failover)

        self._add_log("orchestrator_started")
        await log.ainfo(
            "orchestrator_started",
            cluster=self.config.cluster.name,
            master=self.config.routers.master.name,
            backup=self.config.routers.backup.name,
        )

        # Start web server
        from src.web.app import app, set_orchestrator
        set_orchestrator(self)

        # Mount static files
        from fastapi.staticfiles import StaticFiles
        app.mount("/static", StaticFiles(directory="src/web/static"), name="static")

        web_config = uvicorn.Config(
            app,
            host=self.config.web.host,
            port=self.config.web.port,
            log_level="warning",
        )
        web_server = uvicorn.Server(web_config)

        # Run all loops concurrently, ensure cleanup on exit
        try:
            await asyncio.gather(
                self._run_health_loop(),
                self._run_sync_loop(),
                web_server.serve(),
            )
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Stop the orchestrator."""
        self._running = False
        await self.master_client.close()
        await self.slave_client.close()
        await self.notifier.close()
        self._add_log("orchestrator_stopped")


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="MikroTik HA Orchestrator")
    parser.add_argument(
        "-c", "--config",
        default="config/ha_config.yaml",
        help="Path to configuration file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument("--json-logs", action="store_true", help="Output logs as JSON")
    args = parser.parse_args()

    setup_logging(log_level=args.log_level, json_output=args.json_logs)

    config = load_config(args.config)

    # Decrypt credentials if an encrypted credentials file is configured
    if config.credentials_file:
        import getpass
        import sys

        from src.utils.crypto import apply_decrypted_credentials, decrypt_credentials

        creds_path = Path(args.config).parent / config.credentials_file
        if creds_path.exists():
            import os as _os
            enc_password = _os.environ.get("MKHA_ENCRYPTION_PASSWORD", "")
            if not enc_password:
                enc_password = getpass.getpass("Encryption password: ")
            try:
                creds = decrypt_credentials(creds_path.read_bytes(), enc_password)
                apply_decrypted_credentials(config, creds)
            except Exception as e:
                print(f"Failed to decrypt credentials: {e}")
                sys.exit(1)

    orchestrator = HAOrchestrator(config, config_path=args.config)

    try:
        asyncio.run(orchestrator.start())
    except KeyboardInterrupt:
        pass  # Cleanup handled by finally block in start()


if __name__ == "__main__":
    main()
