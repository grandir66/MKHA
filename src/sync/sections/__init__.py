"""Section handler registry - maps section names to handler classes."""

from __future__ import annotations

from src.sync.sections.base import SyncSection
from src.sync.sections.bridge_vlan import (
    InterfaceBondingSection,
    InterfaceBridgePortSection,
    InterfaceBridgeSection,
    InterfaceBridgeVlanSection,
    InterfaceEthernetSection,
    InterfaceListMemberSection,
    InterfaceListSection,
    InterfaceVlanSection,
)
from src.sync.sections.dhcp_dns import (
    DHCPLeaseSection,
    DHCPNetworkSection,
    DHCPPoolSection,
    DHCPServerSection,
    DNSStaticSection,
    IPAddressSection,
)
from src.sync.sections.firewall import (
    FirewallAddressListSection,
    FirewallFilterSection,
    FirewallMangleSection,
    FirewallNATSection,
    FirewallRawSection,
)
from src.sync.sections.routing import StaticRouteSection
from src.sync.sections.scripts import (
    QueueSimpleSection,
    QueueTreeSection,
    SystemSchedulerSection,
    SystemScriptSection,
)
from src.sync.sections.vpn import (
    IPsecIdentitySection,
    IPsecPeerSection,
    IPsecPolicySection,
    IPsecProfileSection,
    IPsecProposalSection,
    WireGuardInterfaceSection,
    WireGuardPeerSection,
)

# Registry mapping section config names to handler classes.
# Order matters: sections are synced in this order to respect dependencies
# (e.g. bridges before bridge ports, pools before DHCP servers).
SECTION_REGISTRY: dict[str, type[SyncSection]] = {
    # Interfaces (create structure first)
    "interface_ethernet": InterfaceEthernetSection,
    "interface_bridge": InterfaceBridgeSection,
    "interface_bridge_port": InterfaceBridgePortSection,
    "interface_bridge_vlan": InterfaceBridgeVlanSection,
    "interface_vlan": InterfaceVlanSection,
    "interface_bonding": InterfaceBondingSection,
    "interface_list": InterfaceListSection,
    "interface_list_member": InterfaceListMemberSection,
    # IP addresses
    "ip_address": IPAddressSection,
    # Firewall
    "firewall_filter": FirewallFilterSection,
    "firewall_nat": FirewallNATSection,
    "firewall_mangle": FirewallMangleSection,
    "firewall_raw": FirewallRawSection,
    "firewall_address_list": FirewallAddressListSection,
    # Routing
    "ip_route": StaticRouteSection,
    # DHCP / DNS
    "ip_pool": DHCPPoolSection,
    "ip_dhcp_server": DHCPServerSection,
    "ip_dhcp_server_network": DHCPNetworkSection,
    "ip_dhcp_server_lease": DHCPLeaseSection,
    "ip_dns_static": DNSStaticSection,
    # VPN
    "ip_ipsec_profile": IPsecProfileSection,
    "ip_ipsec_proposal": IPsecProposalSection,
    "ip_ipsec_peer": IPsecPeerSection,
    "ip_ipsec_identity": IPsecIdentitySection,
    "ip_ipsec_policy": IPsecPolicySection,
    "interface_wireguard": WireGuardInterfaceSection,
    "interface_wireguard_peer": WireGuardPeerSection,
    # Scripts / Scheduler
    "system_script": SystemScriptSection,
    "system_scheduler": SystemSchedulerSection,
    # Queues
    "queue_simple": QueueSimpleSection,
    "queue_tree": QueueTreeSection,
}

__all__ = ["SECTION_REGISTRY", "SyncSection"]
