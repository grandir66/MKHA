"""Variable translator for mapping device-specific values between routers.

When a configuration is read from the master, any value matching a master variable
is replaced with the corresponding slave variable before applying to the slave.
"""

from __future__ import annotations

from typing import Any

from src.utils.config import RouterVariables
from src.utils.logging import get_logger

log = get_logger(__name__)


class VariableTranslator:
    """Translates device-specific values between master and slave routers.

    Given two sets of variables (master and slave), builds a bidirectional
    mapping table. When translating master→slave, every occurrence of a
    master variable value in the config data is replaced with the slave's
    corresponding value.
    """

    def __init__(
        self,
        master_vars: RouterVariables,
        slave_vars: RouterVariables,
    ):
        self.master_vars = master_vars
        self.slave_vars = slave_vars

        # Build master→slave value mapping
        # Sort by length descending so longer values are replaced first
        # (prevents partial matches, e.g. "10.0.0.1/24" before "10.0.0.1")
        self._m2s_map: list[tuple[str, str]] = []
        for key in master_vars.variables:
            m_val = master_vars.variables.get(key, "")
            s_val = slave_vars.variables.get(key, "")
            if m_val and s_val and m_val != s_val:
                self._m2s_map.append((m_val, s_val))

        self._m2s_map.sort(key=lambda x: len(x[0]), reverse=True)

        # Build slave→master mapping (for reverse translation)
        self._s2m_map: list[tuple[str, str]] = [(s, m) for m, s in self._m2s_map]

        # Role suffix
        self._master_suffix = master_vars.role_suffix
        self._slave_suffix = slave_vars.role_suffix

    def translate_value(self, value: str, reverse: bool = False) -> str:
        """Translate a single string value from master→slave (or reverse).

        Args:
            value: The string value to translate
            reverse: If True, translate slave→master

        Returns:
            Translated string value
        """
        mapping = self._s2m_map if reverse else self._m2s_map
        result = value
        for src, dst in mapping:
            result = result.replace(src, dst)
        return result

    def translate_item(
        self,
        item: dict[str, Any],
        skip_keys: set[str] | None = None,
        reverse: bool = False,
    ) -> dict[str, Any]:
        """Translate all string values in a config item dict.

        Args:
            item: RouterOS config item (dict of key→value)
            skip_keys: Keys to skip (e.g. ".id", "name" for some sections)
            reverse: If True, translate slave→master

        Returns:
            New dict with translated values
        """
        skip = skip_keys or set()
        translated: dict[str, Any] = {}

        for key, value in item.items():
            if key in skip:
                translated[key] = value
            elif isinstance(value, str):
                translated[key] = self.translate_value(value, reverse=reverse)
            else:
                translated[key] = value

        return translated

    def translate_items(
        self,
        items: list[dict[str, Any]],
        skip_keys: set[str] | None = None,
        reverse: bool = False,
    ) -> list[dict[str, Any]]:
        """Translate a list of config items."""
        return [self.translate_item(i, skip_keys, reverse) for i in items]

    def apply_role_suffix(self, comment: str | None) -> str:
        """Apply role suffix to a comment field.

        If the master comment has the master suffix, replace it with slave suffix.
        Otherwise, append the slave suffix.
        """
        if comment is None:
            comment = ""

        # Remove any existing role suffix
        if self._master_suffix and self._master_suffix in comment:
            comment = comment.replace(self._master_suffix, "")
        if self._slave_suffix and self._slave_suffix in comment:
            comment = comment.replace(self._slave_suffix, "")

        # Add slave suffix
        if self._slave_suffix:
            comment = comment.rstrip() + self._slave_suffix

        return comment

    def get_mapping_summary(self) -> list[dict[str, str]]:
        """Return a summary of the variable mapping for display."""
        summary = []
        for key in self.master_vars.variables:
            m_val = self.master_vars.variables.get(key, "")
            s_val = self.slave_vars.variables.get(key, "")
            summary.append({
                "variable": key,
                "master_value": m_val,
                "slave_value": s_val,
                "changed": str(m_val != s_val),
            })
        return summary
