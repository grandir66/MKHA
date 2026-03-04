"""Routing sync section: static routes."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_unordered
from src.sync.sections.base import SyncSection


class StaticRouteSection(SyncSection):
    """Static routes - matched by dst-address + routing-table."""

    section_name = "ip_route"
    api_path = "ip/route"
    match_keys = ["dst-address", "routing-table"]
    ignore_keys = {
        "dynamic", "inactive", "active", "connect", "ecmp",
        "hw-offloaded", "immediate-gw", "local-address",
    }
    translation_skip_keys = {".id", "routing-table"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        # Only sync static routes (not dynamic/connected/BGP etc.)
        master_items = [
            i for i in master_items
            if i.get("dynamic") != "true" and i.get("connect") != "true"
        ]
        slave_items = [
            i for i in slave_items
            if i.get("dynamic") != "true" and i.get("connect") != "true"
        ]

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )
