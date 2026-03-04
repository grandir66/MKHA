"""Bridge and VLAN sync section handlers."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_unordered
from src.sync.sections.base import SyncSection


class InterfaceBridgeSection(SyncSection):
    """Bridge interfaces - matched by name."""

    section_name = "interface_bridge"
    api_path = "interface/bridge"
    match_keys = ["name"]
    ignore_keys = {"running", "l2mtu", "mac-address", "actual-mtu"}
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


class InterfaceBridgePortSection(SyncSection):
    """Bridge ports - matched by interface + bridge."""

    section_name = "interface_bridge_port"
    api_path = "interface/bridge/port"
    match_keys = ["interface", "bridge"]
    ignore_keys = {"dynamic", "status", "hw", "inactive", "debug-info", "point-to-point-port"}
    translation_skip_keys = {".id", "interface", "bridge"}

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


class InterfaceBridgeVlanSection(SyncSection):
    """Bridge VLAN filtering entries - matched by bridge + vlan-ids."""

    section_name = "interface_bridge_vlan"
    api_path = "interface/bridge/vlan"
    match_keys = ["bridge", "vlan-ids"]
    ignore_keys = {"dynamic", "disabled"}
    translation_skip_keys = {".id", "bridge", "vlan-ids"}

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


class InterfaceVlanSection(SyncSection):
    """VLAN interfaces - matched by name."""

    section_name = "interface_vlan"
    api_path = "interface/vlan"
    match_keys = ["name"]
    ignore_keys = {"running", "mac-address", "l2mtu"}
    translation_skip_keys = {".id", "name", "interface"}

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


class InterfaceEthernetSection(SyncSection):
    """Ethernet interface parameters - matched by name.

    Only syncs configurable parameters (MTU, speed, etc.), not dynamic state.
    """

    section_name = "interface_ethernet"
    api_path = "interface/ethernet"
    match_keys = ["name"]
    ignore_keys = {
        "running", "slave", "mac-address", "orig-mac-address",
        "factory-mac-address", "driver-rx-byte", "driver-tx-byte",
        "driver-rx-packet", "driver-tx-packet", "rx-bytes", "tx-bytes",
        "rx-packet", "tx-packet", "fp-rx-byte", "fp-tx-byte",
        "fp-rx-packet", "fp-tx-packet", "link-downs",
    }
    translation_skip_keys = {".id", "name", "default-name"}

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

    async def apply(self, slave_client, diff_result):
        """Ethernet interfaces can only be updated, not added/removed."""
        applied = []
        for entry in diff_result.updates:
            if entry.item_id:
                await slave_client.set(entry.path, entry.item_id, entry.data)
                applied.append(
                    f"UPDATE {entry.path} id={entry.item_id} "
                    f"fields={list(entry.data.keys())}"
                )
        return applied


class InterfaceBondingSection(SyncSection):
    """Bonding interfaces - matched by name."""

    section_name = "interface_bonding"
    api_path = "interface/bonding"
    match_keys = ["name"]
    ignore_keys = {"running", "mac-address", "l2mtu"}
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


class InterfaceListSection(SyncSection):
    """Interface lists - matched by name."""

    section_name = "interface_list"
    api_path = "interface/list"
    match_keys = ["name"]
    ignore_keys = {"builtin", "dynamic"}
    translation_skip_keys = {".id", "name"}

    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        master_items = await self.read_items(master_client)
        slave_items = await self.read_items(slave_client)

        master_items = [i for i in master_items if i.get("builtin") != "true" and i.get("dynamic") != "true"]
        slave_items = [i for i in slave_items if i.get("builtin") != "true" and i.get("dynamic") != "true"]

        translated = self.translate_master_items(master_items)

        return diff_unordered(
            section=self.section_name,
            master_items=translated,
            slave_items=slave_items,
            match_keys=self.match_keys,
            path=self.api_path,
            ignore_keys=self.ignore_keys,
        )


class InterfaceListMemberSection(SyncSection):
    """Interface list members - matched by list + interface."""

    section_name = "interface_list_member"
    api_path = "interface/list/member"
    match_keys = ["list", "interface"]
    ignore_keys = {"dynamic"}
    translation_skip_keys = {".id", "list", "interface"}

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
