"""Configuration diff engine for comparing master and slave configurations.

Compares lists of RouterOS items and produces operations (add, update, remove, move)
needed to make the slave match the master.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.utils.logging import get_logger

log = get_logger(__name__)


class DiffOp(str, Enum):
    ADD = "add"
    UPDATE = "update"
    REMOVE = "remove"
    MOVE = "move"


@dataclass
class DiffEntry:
    """A single diff operation."""

    op: DiffOp
    path: str
    item_id: str | None = None  # .id on the slave (for update/remove/move)
    data: dict[str, Any] = field(default_factory=dict)
    position: int | None = None  # target position (for move/add in ordered lists)
    old_data: dict[str, Any] = field(default_factory=dict)  # previous values (for update)


@dataclass
class DiffResult:
    """Result of comparing two configuration sections."""

    section: str
    additions: list[DiffEntry] = field(default_factory=list)
    updates: list[DiffEntry] = field(default_factory=list)
    removals: list[DiffEntry] = field(default_factory=list)
    moves: list[DiffEntry] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.additions or self.updates or self.removals or self.moves)

    @property
    def total_changes(self) -> int:
        return len(self.additions) + len(self.updates) + len(self.removals) + len(self.moves)

    def summary(self) -> str:
        parts = []
        if self.additions:
            parts.append(f"+{len(self.additions)} add")
        if self.updates:
            parts.append(f"~{len(self.updates)} update")
        if self.removals:
            parts.append(f"-{len(self.removals)} remove")
        if self.moves:
            parts.append(f">{len(self.moves)} move")
        return f"[{self.section}] {', '.join(parts)}" if parts else f"[{self.section}] no changes"


# Keys that should never be compared or synced
SYSTEM_KEYS = {".id", ".nextid", ".dead", "dynamic", "invalid", "running", "slave", "builtin"}


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    """Remove system/dynamic keys from an item for comparison."""
    return {k: v for k, v in item.items() if k not in SYSTEM_KEYS}


def _items_match(
    master_item: dict[str, Any],
    slave_item: dict[str, Any],
    match_keys: list[str],
) -> bool:
    """Check if two items match on the given keys (identity matching)."""
    for key in match_keys:
        if master_item.get(key) != slave_item.get(key):
            return False
    return True


def _compute_item_diff(
    master_item: dict[str, Any],
    slave_item: dict[str, Any],
    ignore_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Compute the changed fields between master and slave items.

    Returns a dict of {key: master_value} for keys that differ.
    All values are compared as strings to match RouterOS REST API semantics.
    Missing keys on the slave side are treated as different (None sentinel).
    """
    ignore = SYSTEM_KEYS | (ignore_keys or set())
    _MISSING = object()
    changes: dict[str, Any] = {}

    for key, master_val in master_item.items():
        if key in ignore:
            continue
        slave_val = slave_item.get(key, _MISSING)
        if slave_val is _MISSING or str(master_val) != str(slave_val):
            changes[key] = master_val

    return changes


def diff_unordered(
    section: str,
    master_items: list[dict[str, Any]],
    slave_items: list[dict[str, Any]],
    match_keys: list[str],
    path: str,
    ignore_keys: set[str] | None = None,
) -> DiffResult:
    """Diff two lists of items where order doesn't matter.

    Items are matched by `match_keys` (e.g. ["name"] for interfaces,
    ["address", "interface"] for IP addresses).

    Args:
        section: Section name for reporting
        master_items: Items from the master router
        slave_items: Items from the slave router
        match_keys: Keys used to identify matching items
        path: RouterOS REST API path
        ignore_keys: Additional keys to ignore during comparison
    """
    result = DiffResult(section=section)
    slave_matched: set[int] = set()

    for m_item in master_items:
        m_norm = _normalize_item(m_item)
        matched = False

        for s_idx, s_item in enumerate(slave_items):
            if s_idx in slave_matched:
                continue
            s_norm = _normalize_item(s_item)

            if _items_match(m_norm, s_norm, match_keys):
                # Found a match - check for updates
                slave_matched.add(s_idx)
                matched = True
                changes = _compute_item_diff(m_norm, s_norm, ignore_keys)
                if changes:
                    result.updates.append(DiffEntry(
                        op=DiffOp.UPDATE,
                        path=path,
                        item_id=s_item.get(".id"),
                        data=changes,
                        old_data={k: s_norm.get(k) for k in changes},
                    ))
                break

        if not matched:
            result.additions.append(DiffEntry(
                op=DiffOp.ADD,
                path=path,
                data=m_norm,
            ))

    # Items on slave that have no match on master → remove
    for s_idx, s_item in enumerate(slave_items):
        if s_idx not in slave_matched:
            s_norm = _normalize_item(s_item)
            result.removals.append(DiffEntry(
                op=DiffOp.REMOVE,
                path=path,
                item_id=s_item.get(".id"),
                data=s_norm,
            ))

    return result


def diff_ordered(
    section: str,
    master_items: list[dict[str, Any]],
    slave_items: list[dict[str, Any]],
    path: str,
    match_keys: list[str] | None = None,
    ignore_keys: set[str] | None = None,
) -> DiffResult:
    """Diff two ordered lists (like firewall rules) where position matters.

    Strategy:
    1. Match items by match_keys if provided, otherwise by content equality.
    2. Detect additions, removals, updates.
    3. After sync, verify order and generate move operations if needed.

    For firewall rules, match_keys might be sparse (rules can be duplicated),
    so we use a position-aware matching approach.
    """
    result = DiffResult(section=section)
    ignore = ignore_keys or set()

    # Normalize all items
    m_items = [_normalize_item(i) for i in master_items]
    s_items = [_normalize_item(i) for i in slave_items]
    s_ids = [i.get(".id") for i in slave_items]

    # Build matching: for each master item, find best slave match
    slave_used: set[int] = set()
    matches: list[tuple[int, int | None]] = []  # (master_idx, slave_idx or None)

    for m_idx, m_item in enumerate(m_items):
        best_match: int | None = None

        if match_keys:
            # Try matching by keys
            for s_idx, s_item in enumerate(s_items):
                if s_idx in slave_used:
                    continue
                if _items_match(m_item, s_item, match_keys):
                    best_match = s_idx
                    break
        else:
            # Match by full content equality
            for s_idx, s_item in enumerate(s_items):
                if s_idx in slave_used:
                    continue
                if m_item == s_item:
                    best_match = s_idx
                    break

        if best_match is not None:
            slave_used.add(best_match)
        matches.append((m_idx, best_match))

    # Generate operations
    for m_idx, s_idx in matches:
        if s_idx is None:
            # New item to add at position m_idx
            result.additions.append(DiffEntry(
                op=DiffOp.ADD,
                path=path,
                data=m_items[m_idx],
                position=m_idx,
            ))
        else:
            # Check for property changes
            changes = _compute_item_diff(m_items[m_idx], s_items[s_idx], ignore)
            if changes:
                result.updates.append(DiffEntry(
                    op=DiffOp.UPDATE,
                    path=path,
                    item_id=s_ids[s_idx],
                    data=changes,
                    old_data={k: s_items[s_idx].get(k) for k in changes},
                ))

    # Slave items not matched → remove
    for s_idx in range(len(s_items)):
        if s_idx not in slave_used:
            result.removals.append(DiffEntry(
                op=DiffOp.REMOVE,
                path=path,
                item_id=s_ids[s_idx],
                data=s_items[s_idx],
            ))

    # After additions/removals, check if remaining items need reordering
    # This is determined after the actual sync, so we mark potential moves
    matched_slave_order = [s_idx for _, s_idx in matches if s_idx is not None]
    if matched_slave_order != sorted(matched_slave_order):
        # Items need reordering - generate move operations
        for target_pos, (m_idx, s_idx) in enumerate(matches):
            if s_idx is not None:
                result.moves.append(DiffEntry(
                    op=DiffOp.MOVE,
                    path=path,
                    item_id=s_ids[s_idx],
                    position=target_pos,
                ))

    return result
