"""Tests for the variable translator."""

from src.sync.variable_translator import VariableTranslator
from src.utils.config import RouterVariables


def _make_translator() -> VariableTranslator:
    master = RouterVariables(
        role_suffix="",
        variables={
            "WAN1_IP": "203.0.113.10/30",
            "WAN1_GW": "203.0.113.9",
            "ROUTER_ID": "10.255.255.1",
            "MGMT_IP": "192.168.88.1/24",
        },
    )
    slave = RouterVariables(
        role_suffix=" [slave]",
        variables={
            "WAN1_IP": "203.0.113.14/30",
            "WAN1_GW": "203.0.113.13",
            "ROUTER_ID": "10.255.255.2",
            "MGMT_IP": "192.168.88.2/24",
        },
    )
    return VariableTranslator(master, slave)


class TestTranslateValue:
    def test_simple_replacement(self):
        t = _make_translator()
        assert t.translate_value("203.0.113.10/30") == "203.0.113.14/30"
        assert t.translate_value("203.0.113.9") == "203.0.113.13"

    def test_no_match(self):
        t = _make_translator()
        assert t.translate_value("10.0.0.1") == "10.0.0.1"

    def test_embedded_value(self):
        t = _make_translator()
        # Test that IP in a larger string is also replaced
        result = t.translate_value("src-address=203.0.113.10/30")
        assert "203.0.113.14/30" in result

    def test_reverse(self):
        t = _make_translator()
        assert t.translate_value("203.0.113.14/30", reverse=True) == "203.0.113.10/30"

    def test_longer_match_first(self):
        """Ensure longer values are replaced before shorter ones (e.g., IP/mask before IP)."""
        t = _make_translator()
        # "192.168.88.1/24" should match before any partial match
        result = t.translate_value("192.168.88.1/24")
        assert result == "192.168.88.2/24"


class TestTranslateItem:
    def test_translates_string_values(self):
        t = _make_translator()
        item = {"address": "203.0.113.10/30", "interface": "ether1", "disabled": "false"}
        result = t.translate_item(item)
        assert result["address"] == "203.0.113.14/30"
        assert result["interface"] == "ether1"

    def test_skip_keys(self):
        t = _make_translator()
        item = {"address": "203.0.113.10/30", ".id": "*1"}
        result = t.translate_item(item, skip_keys={".id"})
        assert result[".id"] == "*1"
        assert result["address"] == "203.0.113.14/30"

    def test_non_string_values(self):
        t = _make_translator()
        item = {"name": "test", "priority": 100}
        result = t.translate_item(item)
        assert result["priority"] == 100


class TestRoleSuffix:
    def test_adds_suffix(self):
        t = _make_translator()
        assert t.apply_role_suffix("My Bridge") == "My Bridge [slave]"

    def test_empty_comment(self):
        t = _make_translator()
        assert t.apply_role_suffix(None) == " [slave]"
        assert t.apply_role_suffix("") == " [slave]"

    def test_no_duplicate_suffix(self):
        t = _make_translator()
        result = t.apply_role_suffix("My Bridge [slave]")
        assert result == "My Bridge [slave]"
        assert result.count("[slave]") == 1


class TestMappingSummary:
    def test_returns_all_variables(self):
        t = _make_translator()
        summary = t.get_mapping_summary()
        assert len(summary) == 4
        names = {s["variable"] for s in summary}
        assert "WAN1_IP" in names
        assert "ROUTER_ID" in names
