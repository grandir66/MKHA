"""System scripts and scheduler sync section handlers."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_unordered
from src.sync.sections.base import SyncSection


class SystemScriptSection(SyncSection):
    """System scripts - matched by name.

    Scripts managed by the HA system (prefixed with "ha_") are excluded
    from sync to avoid overwriting local HA scripts.
    """

    section_name = "system_script"
    api_path = "system/script"
    match_keys = ["name"]
    ignore_keys = {"run-count", "last-started", "invalid"}
    translation_skip_keys = {".id", "name"}

    # HA-managed script prefix to exclude from sync
    HA_SCRIPT_PREFIX = "ha_"

    async def read_items(self, client):
        items = await client.get(self.api_path)
        return [
            i for i in items
            if not str(i.get("name", "")).startswith(self.HA_SCRIPT_PREFIX)
        ]

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )


class SystemSchedulerSection(SyncSection):
    """System scheduler tasks - matched by name.

    HA-managed scheduler entries (prefixed with "ha_") are excluded.
    """

    section_name = "system_scheduler"
    api_path = "system/scheduler"
    match_keys = ["name"]
    ignore_keys = {"run-count", "next-run", "invalid"}
    translation_skip_keys = {".id", "name"}

    HA_SCHEDULER_PREFIX = "ha_"

    async def read_items(self, client):
        items = await client.get(self.api_path)
        return [
            i for i in items
            if not str(i.get("name", "")).startswith(self.HA_SCHEDULER_PREFIX)
        ]

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )


class QueueSimpleSection(SyncSection):
    """Simple queues - matched by name."""

    section_name = "queue_simple"
    api_path = "queue/simple"
    match_keys = ["name"]
    ignore_keys = {
        "dynamic", "invalid", "bytes", "packets",
        "queued-bytes", "queued-packets", "rate",
    }
    translation_skip_keys = {".id", "name"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        master_items = [i for i in master_items if i.get("dynamic") != "true"]
        slave_items = [i for i in slave_items if i.get("dynamic") != "true"]

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )


class QueueTreeSection(SyncSection):
    """Queue trees - matched by name."""

    section_name = "queue_tree"
    api_path = "queue/tree"
    match_keys = ["name"]
    ignore_keys = {
        "dynamic", "invalid", "bytes", "packets",
        "queued-bytes", "queued-packets", "rate",
    }
    translation_skip_keys = {".id", "name"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        master_items = [i for i in master_items if i.get("dynamic") != "true"]
        slave_items = [i for i in slave_items if i.get("dynamic") != "true"]

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )
