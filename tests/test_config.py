"""Tests for configuration loading and validation."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.utils.config import HAConfig, RouterVariables, load_config, load_router_variables


class TestHAConfig:
    def test_minimal_config(self, tmp_path):
        config_data = {
            "routers": {
                "master": {
                    "name": "router-a",
                    "api_url": "https://10.0.0.1/rest",
                    "api_password": "test123",
                },
                "backup": {
                    "name": "router-b",
                    "api_url": "https://10.0.0.2/rest",
                    "api_password": "test456",
                },
            }
        }
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.routers.master.name == "router-a"
        assert config.routers.backup.name == "router-b"
        assert config.cluster.sync_interval_seconds == 60  # default
        assert config.quorum.witness.type == "ping"  # default

    def test_full_config(self, tmp_path):
        config_data = {
            "cluster": {"name": "test-cluster", "sync_interval_seconds": 30},
            "routers": {
                "master": {
                    "name": "router-a",
                    "api_url": "https://10.0.0.1/rest",
                    "api_user": "admin",
                    "api_password": "pass1",
                    "variables_file": "router_a.yaml",
                    "vrrp_priority_master": 200,
                },
                "backup": {
                    "name": "router-b",
                    "api_url": "https://10.0.0.2/rest",
                    "api_password": "pass2",
                },
            },
            "quorum": {
                "witness": {"type": "http", "target": "https://witness.example.com"},
            },
            "web": {"port": 9090},
        }
        config_file = tmp_path / "full_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.cluster.name == "test-cluster"
        assert config.cluster.sync_interval_seconds == 30
        assert config.routers.master.vrrp_priority_master == 200
        assert config.quorum.witness.type == "http"
        assert config.web.port == 9090

    def test_password_from_env(self, tmp_path):
        os.environ["TEST_ROUTER_PASS"] = "env_password"
        config_data = {
            "routers": {
                "master": {
                    "name": "router-a",
                    "api_url": "https://10.0.0.1/rest",
                    "api_password_env": "TEST_ROUTER_PASS",
                },
                "backup": {
                    "name": "router-b",
                    "api_url": "https://10.0.0.2/rest",
                    "api_password": "direct",
                },
            }
        }
        config_file = tmp_path / "env_config.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert config.routers.master.api_password == "env_password"
        del os.environ["TEST_ROUTER_PASS"]

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path.yaml")

    def test_default_sync_sections(self, tmp_path):
        config_data = {
            "routers": {
                "master": {"name": "a", "api_url": "https://1/rest", "api_password": "x"},
                "backup": {"name": "b", "api_url": "https://2/rest", "api_password": "y"},
            }
        }
        config_file = tmp_path / "defaults.yaml"
        config_file.write_text(yaml.dump(config_data))

        config = load_config(config_file)
        assert "firewall_filter" in config.sync.sections
        assert "interface_bridge" in config.sync.sections
        assert "ip_route" in config.sync.sections


class TestRouterVariables:
    def test_load_variables(self, tmp_path):
        var_data = {
            "role_suffix": " [slave]",
            "variables": {
                "WAN1_IP": "10.0.0.1/30",
                "WAN1_GW": "10.0.0.2",
            },
        }
        var_file = tmp_path / "vars.yaml"
        var_file.write_text(yaml.dump(var_data))

        vars = load_router_variables(var_file)
        assert vars.role_suffix == " [slave]"
        assert vars.variables["WAN1_IP"] == "10.0.0.1/30"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_router_variables("/nonexistent/vars.yaml")
