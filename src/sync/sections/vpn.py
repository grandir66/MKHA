"""VPN sync section handlers: IPsec, WireGuard."""

from __future__ import annotations

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult, diff_unordered
from src.sync.sections.base import SyncSection


class IPsecPeerSection(SyncSection):
    """IPsec peers - matched by name."""

    section_name = "ip_ipsec_peer"
    api_path = "ip/ipsec/peer"
    match_keys = ["name"]
    ignore_keys = {"dynamic"}
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


class IPsecIdentitySection(SyncSection):
    """IPsec identities - matched by peer."""

    section_name = "ip_ipsec_identity"
    api_path = "ip/ipsec/identity"
    match_keys = ["peer"]
    ignore_keys = {"dynamic"}
    translation_skip_keys = {".id", "peer"}

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


class IPsecPolicySection(SyncSection):
    """IPsec policies - matched by src-address + dst-address + peer."""

    section_name = "ip_ipsec_policy"
    api_path = "ip/ipsec/policy"
    match_keys = ["peer", "src-address", "dst-address"]
    ignore_keys = {"dynamic", "invalid", "active", "ph2-count", "ph2-state"}
    translation_skip_keys = {".id", "peer"}

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


class IPsecProfileSection(SyncSection):
    """IPsec profiles - matched by name."""

    section_name = "ip_ipsec_profile"
    api_path = "ip/ipsec/profile"
    match_keys = ["name"]
    ignore_keys = {"dynamic"}
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


class IPsecProposalSection(SyncSection):
    """IPsec proposals - matched by name."""

    section_name = "ip_ipsec_proposal"
    api_path = "ip/ipsec/proposal"
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


class WireGuardInterfaceSection(SyncSection):
    """WireGuard interfaces - matched by name."""

    section_name = "interface_wireguard"
    api_path = "interface/wireguard"
    match_keys = ["name"]
    ignore_keys = {"running", "public-key"}
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


class WireGuardPeerSection(SyncSection):
    """WireGuard peers - matched by public-key."""

    section_name = "interface_wireguard_peer"
    api_path = "interface/wireguard/peers"
    match_keys = ["public-key"]
    ignore_keys = {
        "dynamic", "current-endpoint-address", "current-endpoint-port",
        "last-handshake", "rx", "tx",
    }
    translation_skip_keys = {".id", "public-key", "interface"}

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
