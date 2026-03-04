"""DHCP and DNS sync section handlers."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_unordered
from src.sync.sections.base import SyncSection


class IPAddressSection(SyncSection):
    """IP addresses on interfaces - matched by address + interface.

    Syncs all IP addresses except those on VRRP interfaces.
    """

    section_name = "ip_address"
    api_path = "ip/address"
    match_keys = ["interface"]
    ignore_keys = {"dynamic", "invalid", "actual-interface"}
    translation_skip_keys = {".id", "interface"}

    async def read_items(self, client):
        items = await client.get(self.api_path)
        # Exclude VRRP addresses (dynamic, on vrrp interfaces)
        return [
            i for i in items
            if i.get("dynamic") != "true"
            and not str(i.get("interface", "")).startswith("vrrp")
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


class DHCPServerSection(SyncSection):
    """DHCP server instances - matched by name."""

    section_name = "ip_dhcp_server"
    api_path = "ip/dhcp-server"
    match_keys = ["name"]
    ignore_keys = {"dynamic", "invalid"}
    translation_skip_keys = {".id", "name", "interface"}

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


class DHCPNetworkSection(SyncSection):
    """DHCP network definitions - matched by address."""

    section_name = "ip_dhcp_server_network"
    api_path = "ip/dhcp-server/network"
    match_keys = ["address"]
    ignore_keys = {"dynamic"}
    translation_skip_keys = {".id"}

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


class DHCPPoolSection(SyncSection):
    """IP pools (used by DHCP) - matched by name."""

    section_name = "ip_pool"
    api_path = "ip/pool"
    match_keys = ["name"]
    ignore_keys = set()
    translation_skip_keys = {".id", "name"}

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


class DHCPLeaseSection(SyncSection):
    """Static DHCP leases - matched by mac-address."""

    section_name = "ip_dhcp_server_lease"
    api_path = "ip/dhcp-server/lease"
    match_keys = ["mac-address"]
    ignore_keys = {
        "dynamic", "status", "last-seen", "active-address",
        "active-mac-address", "active-client-id", "host-name",
        "radius", "blocked", "expires-after",
    }
    translation_skip_keys = {".id", "mac-address", "server"}

    async def read_items(self, client):
        items = await client.get(self.api_path)
        # Only sync static leases (not dynamic ones from DHCP)
        return [i for i in items if i.get("dynamic") != "true"]

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


class DNSStaticSection(SyncSection):
    """Static DNS entries - matched by name + type."""

    section_name = "ip_dns_static"
    api_path = "ip/dns/static"
    match_keys = ["name", "type"]
    ignore_keys = {"dynamic"}
    translation_skip_keys = {".id", "name", "type"}

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
