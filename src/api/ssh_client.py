"""SSH client for MikroTik RouterOS CLI access via paramiko."""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urlparse

import paramiko

from src.utils.logging import get_logger

log = get_logger(__name__)


class SSHError(Exception):
    """Base exception for SSH operations."""


class SSHConnectionError(SSHError):
    """Raised when SSH connection fails."""


class MikroTikSSHClient:
    """SSH client for MikroTik RouterOS.

    Connects via SSH and executes CLI commands.  RouterOS returns
    text output which this client can parse into structured data.

    Usage::

        client = MikroTikSSHClient("192.168.88.1", "admin", "password")
        with client:
            result = client.command("/system/resource/print")
            export = client.export()
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str = "",
        port: int = 22,
        timeout: float = 10.0,
        key_file: str = "",
    ):
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.timeout = timeout
        self.key_file = key_file
        self._client: paramiko.SSHClient | None = None

    @classmethod
    def from_api_url(
        cls,
        api_url: str,
        username: str,
        password: str,
        ssh_port: int = 22,
        timeout: float = 10.0,
        key_file: str = "",
    ) -> "MikroTikSSHClient":
        """Create SSH client deriving host from the REST API URL."""
        parsed = urlparse(api_url)
        host = parsed.hostname or api_url
        return cls(
            host=host,
            username=username,
            password=password,
            port=ssh_port,
            timeout=timeout,
            key_file=key_file,
        )

    def connect(self) -> None:
        """Establish SSH connection.

        Tries multiple auth methods in order:
        1. Public key (if key_file is set)
        2. Password auth
        3. Keyboard-interactive (MikroTik RouterOS often requires this)
        """
        if self._client is not None:
            return

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            if self.key_file:
                self._client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    key_filename=self.key_file,
                    timeout=self.timeout,
                    allow_agent=False,
                    look_for_keys=False,
                )
                return

            # Try password auth first, fall back to keyboard-interactive
            try:
                self._client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=self.password,
                    timeout=self.timeout,
                    allow_agent=False,
                    look_for_keys=False,
                )
            except paramiko.ssh_exception.BadAuthenticationType:
                # Router requires keyboard-interactive (common on MikroTik)
                transport = self._client.get_transport()
                if transport is None:
                    # Need a fresh TCP connection for keyboard-interactive
                    import socket
                    sock = socket.create_connection(
                        (self.host, self.port), timeout=self.timeout
                    )
                    transport = paramiko.Transport(sock)
                    transport.connect()
                    self._client._transport = transport  # type: ignore[attr-defined]

                password = self.password

                def _kbd_interactive_handler(
                    title: str, instructions: str, prompt_list: list
                ) -> list[str]:
                    return [password] * len(prompt_list)

                transport.auth_interactive(self.username, _kbd_interactive_handler)
        except Exception as e:
            self._client = None
            raise SSHConnectionError(
                f"SSH connection to {self.host}:{self.port} failed: {e}"
            ) from e

    def close(self) -> None:
        """Close the SSH connection."""
        if self._client:
            self._client.close()
            self._client = None

    def command(self, cmd: str, timeout: float = 15.0) -> str:
        """Execute a RouterOS CLI command and return the output.

        Args:
            cmd: CLI command (e.g. "/system/resource/print",
                 "/ip/address/print", "/export")
            timeout: Command timeout in seconds.

        Returns:
            Raw text output from the command.
        """
        if not self._client:
            self.connect()

        try:
            _, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)  # type: ignore[union-attr]
            output = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            if err and "bad command" in err.lower():
                raise SSHError(f"Command failed: {err.strip()}")
            return output.strip()
        except paramiko.SSHException as e:
            raise SSHError(f"SSH command error: {e}") from e

    def export(self, verbose: bool = False) -> str:
        """Run /export and return the full configuration.

        Args:
            verbose: If True, include default values (/export verbose).

        Returns:
            Full router configuration as text.
        """
        cmd = "/export verbose" if verbose else "/export"
        return self.command(cmd, timeout=30.0)

    def export_section(self, section: str) -> str:
        """Export a specific configuration section.

        Args:
            section: Section path, e.g. "ip/firewall/filter", "ip/address"

        Returns:
            Section configuration as text.
        """
        # RouterOS /export accepts path= argument
        return self.command(f"/{section}/export", timeout=15.0)

    def get_system_info(self) -> dict[str, str]:
        """Fetch system resource info, parsed into a dict."""
        raw = self.command("/system/resource/print")
        return self._parse_print_output(raw)

    def get_identity(self) -> str:
        """Get the router identity name."""
        raw = self.command("/system/identity/print")
        info = self._parse_print_output(raw)
        return info.get("name", "unknown")

    def get_ip_addresses(self) -> list[dict[str, str]]:
        """Get IP addresses as list of dicts."""
        raw = self.command("/ip/address/print detail without-paging")
        return self._parse_detail_output(raw)

    def get_interfaces(self) -> list[dict[str, str]]:
        """Get interfaces as list of dicts."""
        raw = self.command("/interface/print detail without-paging")
        return self._parse_detail_output(raw)

    def get_routes(self) -> list[dict[str, str]]:
        """Get routes as list of dicts."""
        raw = self.command("/ip/route/print detail without-paging")
        return self._parse_detail_output(raw)

    def get_vrrp(self) -> list[dict[str, str]]:
        """Get VRRP interfaces."""
        raw = self.command("/interface/vrrp/print detail without-paging")
        return self._parse_detail_output(raw)

    def get_firewall_filter(self) -> list[dict[str, str]]:
        """Get firewall filter rules."""
        raw = self.command("/ip/firewall/filter/print detail without-paging")
        return self._parse_detail_output(raw)

    def get_firewall_nat(self) -> list[dict[str, str]]:
        """Get firewall NAT rules."""
        raw = self.command("/ip/firewall/nat/print detail without-paging")
        return self._parse_detail_output(raw)

    def is_reachable(self) -> bool:
        """Check if the router is reachable via SSH."""
        try:
            self.connect()
            self.command("/system/identity/print", timeout=5.0)
            return True
        except Exception:
            return False

    # --- Parsers for RouterOS CLI output ---

    @staticmethod
    def _parse_print_output(raw: str) -> dict[str, str]:
        """Parse single-item 'print' output into a dict.

        RouterOS output like:
            uptime: 1d2h3m4s
            version: 7.16.2
            cpu-load: 5
        """
        result: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key:
                    result[key] = value
        return result

    @staticmethod
    def _parse_detail_output(raw: str) -> list[dict[str, str]]:
        """Parse 'print detail' output into a list of dicts.

        RouterOS detail output uses numbered entries separated by blank lines:
         0   ;;; comment
             address=192.168.1.1/24 network=192.168.1.0 interface=ether1
        """
        items: list[dict[str, str]] = []
        current: dict[str, str] = {}
        current_text = ""

        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # New item starts with a number followed by spaces
            if re.match(r"^\d+\s+", stripped) or (
                re.match(r"^\s*Flags:", stripped) and current
            ):
                if current:
                    items.append(current)
                    current = {}
                # Check for ;;; comment
                if ";;;" in stripped:
                    comment_part = stripped.split(";;;", 1)[1].strip()
                    current["comment"] = comment_part
                    # Also extract key=value pairs before ;;;
                    before = stripped.split(";;;", 1)[0]
                    current_text = before
                else:
                    current_text = stripped
                    # Remove leading number
                    current_text = re.sub(r"^\d+\s+[A-Z]*\s*", "", current_text)
            elif stripped.startswith("Flags:") or stripped.startswith("Columns:"):
                continue
            else:
                current_text = stripped

            # Extract key=value pairs
            if current_text:
                for match in re.finditer(r'([\w-]+)=("[^"]*"|[^\s]+)', current_text):
                    key = match.group(1)
                    value = match.group(2).strip('"')
                    current[key] = value

        if current:
            items.append(current)

        return items

    def __enter__(self) -> "MikroTikSSHClient":
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()
