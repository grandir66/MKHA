"""Tests for the diff engine."""

from src.sync.diff import DiffOp, diff_ordered, diff_unordered


class TestDiffUnordered:
    def test_no_changes(self):
        master = [{"name": "br1", "mtu": "1500", ".id": "*1"}]
        slave = [{"name": "br1", "mtu": "1500", ".id": "*A"}]
        result = diff_unordered("test", master, slave, ["name"], "test/path")
        assert not result.has_changes

    def test_addition(self):
        master = [
            {"name": "br1", "mtu": "1500", ".id": "*1"},
            {"name": "br2", "mtu": "1500", ".id": "*2"},
        ]
        slave = [{"name": "br1", "mtu": "1500", ".id": "*A"}]
        result = diff_unordered("test", master, slave, ["name"], "test/path")
        assert len(result.additions) == 1
        assert result.additions[0].data["name"] == "br2"

    def test_removal(self):
        master = [{"name": "br1", ".id": "*1"}]
        slave = [
            {"name": "br1", ".id": "*A"},
            {"name": "br2", ".id": "*B"},
        ]
        result = diff_unordered("test", master, slave, ["name"], "test/path")
        assert len(result.removals) == 1
        assert result.removals[0].item_id == "*B"

    def test_update(self):
        master = [{"name": "br1", "mtu": "9000", ".id": "*1"}]
        slave = [{"name": "br1", "mtu": "1500", ".id": "*A"}]
        result = diff_unordered("test", master, slave, ["name"], "test/path")
        assert len(result.updates) == 1
        assert result.updates[0].data == {"mtu": "9000"}
        assert result.updates[0].old_data == {"mtu": "1500"}

    def test_ignores_system_keys(self):
        master = [{"name": "br1", "mtu": "1500", ".id": "*1", "dynamic": "false"}]
        slave = [{"name": "br1", "mtu": "1500", ".id": "*A", "dynamic": "true"}]
        result = diff_unordered("test", master, slave, ["name"], "test/path")
        # dynamic is a system key, should be ignored
        assert not result.has_changes

    def test_multi_key_match(self):
        master = [{"list": "WAN", "address": "10.0.0.1", "timeout": "1d", ".id": "*1"}]
        slave = [{"list": "WAN", "address": "10.0.0.1", "timeout": "2d", ".id": "*A"}]
        result = diff_unordered("test", master, slave, ["list", "address"], "test/path")
        assert len(result.updates) == 1
        assert result.updates[0].data == {"timeout": "1d"}


class TestDiffOrdered:
    def test_no_changes(self):
        master = [
            {"chain": "input", "action": "accept", ".id": "*1"},
            {"chain": "input", "action": "drop", ".id": "*2"},
        ]
        slave = [
            {"chain": "input", "action": "accept", ".id": "*A"},
            {"chain": "input", "action": "drop", ".id": "*B"},
        ]
        result = diff_ordered("test", master, slave, "test/path")
        assert not result.additions
        assert not result.removals
        assert not result.updates

    def test_addition_at_end(self):
        master = [
            {"chain": "input", "action": "accept", ".id": "*1"},
            {"chain": "input", "action": "drop", ".id": "*2"},
        ]
        slave = [{"chain": "input", "action": "accept", ".id": "*A"}]
        result = diff_ordered("test", master, slave, "test/path")
        assert len(result.additions) == 1

    def test_removal(self):
        master = [{"chain": "input", "action": "accept", ".id": "*1"}]
        slave = [
            {"chain": "input", "action": "accept", ".id": "*A"},
            {"chain": "input", "action": "drop", ".id": "*B"},
        ]
        result = diff_ordered("test", master, slave, "test/path")
        assert len(result.removals) == 1
        assert result.removals[0].item_id == "*B"

    def test_summary(self):
        master = [{"chain": "input", "action": "accept", ".id": "*1"}]
        slave = []
        result = diff_ordered("firewall_filter", master, slave, "test/path")
        assert "firewall_filter" in result.summary()
        assert "+1 add" in result.summary()
