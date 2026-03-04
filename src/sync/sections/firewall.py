"""Firewall sync section handlers: filter, NAT, mangle, raw, address lists."""

from __future__ import annotations

from typing import Any

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_ordered, diff_unordered
from src.sync.sections.base import SyncSection


class _FirewallRulesBase(SyncSection):
    """Base for ordered firewall rule sections (filter, NAT, mangle, raw)."""

    ordered = True
    ignore_keys = {"bytes", "packets", "dynamic", "invalid"}
    translation_skip_keys = {".id"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        # Filter out dynamic/builtin rules
        master_items = [i for i in master_items if i.get("dynamic") != "true"]
        slave_items = [i for i in slave_items if i.get("dynamic") != "true"]

        # Translate master items to slave context
        translated = self.translate_master_items(master_items)

        return diff_ordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )


class FirewallFilterSection(_FirewallRulesBase):
    section_name = "firewall_filter"
    api_path = "ip/firewall/filter"


class FirewallNATSection(_FirewallRulesBase):
    section_name = "firewall_nat"
    api_path = "ip/firewall/nat"


class FirewallMangleSection(_FirewallRulesBase):
    section_name = "firewall_mangle"
    api_path = "ip/firewall/mangle"


class FirewallRawSection(_FirewallRulesBase):
    section_name = "firewall_raw"
    api_path = "ip/firewall/raw"


class FirewallAddressListSection(SyncSection):
    """Address list entries - unordered, matched by list name + address."""

    section_name = "firewall_address_list"
    api_path = "ip/firewall/address-list"
    match_keys = ["list", "address"]
    ignore_keys = {"dynamic", "creation-time"}
    translation_skip_keys = {".id", "list"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        # Filter out dynamic entries
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
