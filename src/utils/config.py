"""Configuration loading and validation using Pydantic."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator


# --- Section Groups: logical grouping of sync sections for UI toggles ---

SECTION_GROUPS: dict[str, dict[str, Any]] = {
    "interfaces": {
        "label": "Interfaces (ethernet, bridge, VLAN, bonding, lists)",
        "sections": [
            "interface_ethernet", "interface_bridge", "interface_bridge_port",
            "interface_bridge_vlan", "interface_vlan", "interface_bonding",
            "interface_list", "interface_list_member",
        ],
    },
    "ip_addressing": {
        "label": "IP Addresses",
        "sections": ["ip_address"],
    },
    "firewall": {
        "label": "Firewall (filter, NAT, mangle, raw, address lists)",
        "sections": [
            "firewall_filter", "firewall_nat", "firewall_mangle",
            "firewall_raw", "firewall_address_list",
        ],
    },
    "routing": {
        "label": "Static Routes",
        "sections": ["ip_route"],
    },
    "dhcp_dns": {
        "label": "DHCP & DNS",
        "sections": [
            "ip_pool", "ip_dhcp_server", "ip_dhcp_server_network",
            "ip_dhcp_server_lease", "ip_dns_static",
        ],
    },
    "vpn": {
        "label": "VPN (IPsec, WireGuard)",
        "sections": [
            "ip_ipsec_profile", "ip_ipsec_proposal", "ip_ipsec_peer",
            "ip_ipsec_identity", "ip_ipsec_policy",
            "interface_wireguard", "interface_wireguard_peer",
        ],
    },
    "scripts": {
        "label": "Scripts & Scheduler",
        "sections": ["system_script", "system_scheduler"],
    },
    "queues": {
        "label": "Queues (simple, tree)",
        "sections": ["queue_simple", "queue_tree"],
    },
}

ALL_GROUP_NAMES = list(SECTION_GROUPS.keys())


def expand_groups_to_sections(enabled_groups: list[str]) -> list[str]:
    """Expand group names into individual section names, preserving order."""
    sections: list[str] = []
    for group_name in ALL_GROUP_NAMES:
        if group_name in enabled_groups:
            for section in SECTION_GROUPS[group_name]["sections"]:
                if section not in sections:
                    sections.append(section)
    return sections


class RouterVariables(BaseModel):
    """Device-specific variables for a router."""

    role_suffix: str = ""
    variables: dict[str, str] = Field(default_factory=dict)


class RouterConfig(BaseModel):
    """Configuration for a single router."""

    name: str
    api_url: str
    api_user: str = "admin"
    api_password_env: str = ""
    api_password: str = ""
    ssh_port: int = 22
    ssh_enabled: bool = True
    ssh_key_file: str = ""
    variables_file: str = ""
    vrrp_priority_master: int = 150
    vrrp_priority_backup: int = 100
    vrrp_priority_demoted: int = 50

    @model_validator(mode="after")
    def resolve_password(self) -> "RouterConfig":
        if self.api_password_env and not self.api_password:
            self.api_password = os.environ.get(self.api_password_env, "")
        return self


class RoutersConfig(BaseModel):
    """Configuration for the router pair."""

    master: RouterConfig
    backup: RouterConfig


class WitnessConfig(BaseModel):
    """Quorum witness configuration."""

    type: Literal["ping", "http", "dns"] = "ping"
    target: str = "8.8.8.8"
    timeout_ms: int = 2000
    fail_threshold: int = 3


class HealthCheckConfig(BaseModel):
    """Health check timing configuration."""

    interval_seconds: int = 5
    api_timeout_ms: int = 3000
    ping_timeout_ms: int = 1000


class QuorumConfig(BaseModel):
    """Quorum configuration."""

    witness: WitnessConfig = Field(default_factory=WitnessConfig)
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)


class SyncConfig(BaseModel):
    """Sync configuration with group-based toggle support."""

    enabled_groups: list[str] = Field(default_factory=lambda: list(ALL_GROUP_NAMES))
    sections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def resolve_sections(self) -> "SyncConfig":
        """If sections is empty, expand from enabled_groups."""
        if not self.sections:
            self.sections = expand_groups_to_sections(self.enabled_groups)
        return self


class AuthUser(BaseModel):
    """A web UI user (password stored as PBKDF2-SHA256 hash)."""

    username: str
    password_hash: str
    salt: str


class WebConfig(BaseModel):
    """Web UI configuration."""

    host: str = "0.0.0.0"
    port: int = 8080
    auth_users: list[AuthUser] = Field(default_factory=list)


class NotificationsConfig(BaseModel):
    """Notification channels configuration."""

    webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_from: str = ""
    email_to: str = ""


class ProvisioningConfig(BaseModel):
    """Day Zero Provisioning settings."""

    blank_threshold: int = 5
    orchestrator_url: str = ""
    api_user: str = "ha-sync"
    api_group: str = "full"
    disable_services: list[str] = Field(
        default_factory=lambda: ["telnet", "ftp", "www"]
    )
    deploy_scripts: bool = True
    health_check_interval: str = "5s"


class ClusterConfig(BaseModel):
    """Top-level cluster configuration."""

    name: str = "mikrotik-ha-cluster"
    sync_interval_seconds: int = 60
    failover_cooldown_seconds: int = 30


class HAConfig(BaseModel):
    """Root configuration model."""

    cluster: ClusterConfig = Field(default_factory=ClusterConfig)
    routers: RoutersConfig
    quorum: QuorumConfig = Field(default_factory=QuorumConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    provisioning: ProvisioningConfig = Field(default_factory=ProvisioningConfig)
    credentials_file: str = ""


def load_config(config_path: str | Path) -> HAConfig:
    """Load and validate the HA configuration from a YAML file."""
    config_path = Path(config_path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return HAConfig.model_validate(raw)


def save_config(config: HAConfig, config_path: str | Path) -> None:
    """Save the HA configuration to a YAML file."""
    config_path = Path(config_path)
    data = config.model_dump(exclude_defaults=False)

    for role in ("master", "backup"):
        router = data["routers"][role]
        if data.get("credentials_file"):
            # Encrypted mode — strip all plaintext passwords from YAML
            router.pop("api_password", None)
            router.pop("api_password_env", None)
        elif router.get("api_password_env"):
            router.pop("api_password", None)
        else:
            router.pop("api_password_env", None)

    data["sync"].pop("sections", None)  # Derived from enabled_groups

    with open(config_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def load_router_variables(variables_path: str | Path) -> RouterVariables:
    """Load router-specific variables from a YAML file."""
    variables_path = Path(variables_path)
    if not variables_path.exists():
        raise FileNotFoundError(f"Variables file not found: {variables_path}")

    with open(variables_path) as f:
        raw = yaml.safe_load(f)

    return RouterVariables.model_validate(raw)


def save_router_variables(variables: RouterVariables, variables_path: str | Path) -> None:
    """Save router-specific variables to a YAML file."""
    variables_path = Path(variables_path)
    with open(variables_path, "w") as f:
        yaml.dump(variables.model_dump(), f, default_flow_style=False, sort_keys=False)
