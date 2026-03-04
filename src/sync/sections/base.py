"""Base class for sync section handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.api.routeros_client import RouterOSClient
from src.sync.diff import DiffResult
from src.sync.variable_translator import VariableTranslator
from src.utils.logging import get_logger

log = get_logger(__name__)


class SyncSection(ABC):
    """Abstract base for a configuration section sync handler.

    Each section knows:
    - Its RouterOS REST API path(s)
    - How to read items from a router
    - How to match items between master and slave
    - How to compute a diff
    - How to apply changes

    Subclasses must implement `diff()` and `apply()`.
    """

    section_name: str = ""
    api_path: str = ""
    ordered: bool = False  # True for firewall rules where order matters

    # Keys used to match corresponding items between master and slave
    match_keys: list[str] = []

    # Keys to ignore when comparing items
    ignore_keys: set[str] = set()

    # Keys to skip during variable translation
    translation_skip_keys: set[str] = set()

    # Whether to apply role suffix to the "comment" field
    apply_comment_suffix: bool = True

    def __init__(self, translator: VariableTranslator):
        self.translator = translator

    async def read_items(self, client: RouterOSClient) -> list[dict[str, Any]]:
        """Read all items from the router for this section."""
        return await client.get(self.api_path)

    def translate_master_items(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Translate master items to slave context."""
        translated = self.translator.translate_items(
            items, skip_keys=self.translation_skip_keys
        )
        if self.apply_comment_suffix:
            for item in translated:
                if "comment" in item:
                    item["comment"] = self.translator.apply_role_suffix(
                        item.get("comment")
                    )
        return translated

    @abstractmethod
    async def diff(
        self,
        master_client: RouterOSClient,
        slave_client: RouterOSClient,
    ) -> DiffResult:
        """Compute the diff between master and slave for this section.

        Returns:
            DiffResult with additions, updates, removals, moves.
        """

    async def apply(
        self,
        slave_client: RouterOSClient,
        diff_result: DiffResult,
    ) -> list[str]:
        """Apply the diff to the slave router.

        Returns:
            List of log messages describing applied changes.
        """
        applied: list[str] = []

        # Remove first (to avoid conflicts)
        for entry in reversed(diff_result.removals):
            if entry.item_id:
                await slave_client.remove(entry.path, entry.item_id)
                applied.append(f"REMOVE {entry.path} id={entry.item_id}")

        # Add new items
        for entry in diff_result.additions:
            result = await slave_client.add(entry.path, entry.data)
            new_id = result.get(".id", "?") if isinstance(result, dict) else "?"
            applied.append(f"ADD {entry.path} id={new_id}")

        # Update existing items
        for entry in diff_result.updates:
            if entry.item_id:
                await slave_client.set(entry.path, entry.item_id, entry.data)
                applied.append(
                    f"UPDATE {entry.path} id={entry.item_id} "
                    f"fields={list(entry.data.keys())}"
                )

        # Move items (reorder)
        for entry in diff_result.moves:
            if entry.item_id and entry.position is not None:
                await slave_client.move(entry.path, entry.item_id, entry.position)
                applied.append(
                    f"MOVE {entry.path} id={entry.item_id} to position {entry.position}"
                )

        return applied
