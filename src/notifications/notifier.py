"""Notification system for HA events (webhook, Telegram, email)."""

from __future__ import annotations

import asyncio
import json
import smtplib
from email.message import EmailMessage
from typing import Any

import httpx

from src.quorum.manager import ClusterState, FailoverAction, QuorumDecision
from src.utils.config import NotificationsConfig
from src.utils.logging import get_logger

log = get_logger(__name__)


class Notifier:
    """Sends notifications about HA events through configured channels."""

    def __init__(self, config: NotificationsConfig):
        self.config = config
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def notify_failover(self, decision: QuorumDecision) -> None:
        """Send notification about a failover event."""
        severity = "critical" if decision.action == FailoverAction.PROMOTE_BACKUP else "warning"

        message = (
            f"[MKHA {severity.upper()}] {decision.cluster_state.value}\n"
            f"Action: {decision.action.value}\n"
            f"Reason: {decision.reason}\n"
        )

        if decision.master_health:
            message += f"Master: {decision.master_health.status.value}\n"
        if decision.backup_health:
            message += f"Backup: {decision.backup_health.status.value}\n"

        payload = {
            "event": "failover",
            "severity": severity,
            "message": message,
            "decision": decision.to_dict(),
        }

        await self._send_all(message, payload)

    async def notify_sync(self, report_summary: str, success: bool) -> None:
        """Send notification about a sync event (only on errors or first success)."""
        if success:
            return  # Don't spam on successful syncs

        message = f"[MKHA WARNING] Sync failed\n{report_summary}"
        payload = {"event": "sync_error", "severity": "warning", "message": message}
        await self._send_all(message, payload)

    async def notify_state_change(
        self, old_state: ClusterState, new_state: ClusterState
    ) -> None:
        """Send notification when cluster state changes."""
        message = f"[MKHA] Cluster state: {old_state.value} → {new_state.value}"
        payload = {
            "event": "state_change",
            "old_state": old_state.value,
            "new_state": new_state.value,
            "message": message,
        }
        await self._send_all(message, payload)

    async def _send_all(self, message: str, payload: dict[str, Any]) -> None:
        """Send notification through all configured channels."""
        if self.config.webhook_url:
            await self._send_webhook(payload)

        if self.config.telegram_bot_token and self.config.telegram_chat_id:
            await self._send_telegram(message)

        if self.config.email_smtp_host and self.config.email_from and self.config.email_to:
            await self._send_email(message)

    async def _send_webhook(self, payload: dict[str, Any]) -> None:
        """Send to webhook URL."""
        try:
            client = await self._get_client()
            response = await client.post(
                self.config.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                await log.awarning("webhook_error", status=response.status_code)
        except Exception as e:
            await log.awarning("webhook_send_failed", error=str(e))

    async def _send_telegram(self, message: str) -> None:
        """Send message via Telegram Bot API."""
        try:
            client = await self._get_client()
            url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
            await client.post(url, json={
                "chat_id": self.config.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
            })
        except Exception as e:
            await log.awarning("telegram_send_failed", error=str(e))

    async def _send_email(self, message: str) -> None:
        """Send notification via SMTP email in a thread to avoid blocking."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_email_sync, message)
        except Exception as e:
            await log.awarning("email_send_failed", error=str(e))

    def _send_email_sync(self, message: str) -> None:
        """Synchronous SMTP send (runs in executor thread)."""
        msg = EmailMessage()
        msg["Subject"] = message.split("\n", 1)[0][:100]
        msg["From"] = self.config.email_from
        msg["To"] = self.config.email_to
        msg.set_content(message)

        with smtplib.SMTP(self.config.email_smtp_host, self.config.email_smtp_port, timeout=10) as server:
            server.starttls()
            server.send_message(msg)
