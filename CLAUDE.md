# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MKHA (MikroTik HA Manager) is a high-availability orchestrator for MikroTik RouterOS 7 router pairs. It synchronizes configurations, monitors health via quorum consensus, manages VRRP failover, and provides a web dashboard.

## Commands

```bash
# Development setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"

# Run tests (54 tests, pytest + pytest-asyncio + pytest-httpx)
pytest
pytest -v tests/test_diff.py          # single file

# Run locally
python -m src.main -c config/ha_config.yaml
python -m src.main -c config/ha_config.yaml --log-level DEBUG --json-logs

# Docker
docker compose up -d

# Lint
ruff check src/ tests/
```

## Architecture

The system is async-first (asyncio + httpx). `HAOrchestrator` (src/main.py) is the central coordinator running three concurrent loops: health check, config sync, and web server.

**Core flow:** HAOrchestrator → QuorumManager (health decisions) → VRRPController (priority changes) + SyncEngine (config replication) + Notifier (alerts)

**Key components:**
- `src/api/routeros_client.py` — Async REST client for RouterOS 7 API (PUT=add, PATCH=update, DELETE=remove)
- `src/sync/engine.py` — SyncEngine orchestrates 30 section handlers in dependency order
- `src/sync/diff.py` — Two diff algorithms: `diff_unordered` (match by keys) and `diff_ordered` (position-sensitive, for firewall rules)
- `src/sync/sections/` — Each section defines `api_path`, `match_keys`, `diff()`, `apply()`. Registry in `__init__.py` determines sync order
- `src/sync/variable_translator.py` — Translates master-specific values to backup-specific (longest-first replacement)
- `src/quorum/manager.py` — Failure counters + thresholds + cooldown → ClusterState + FailoverAction
- `src/vrrp/controller.py` — Sets VRRP priorities on all interfaces of a router
- `src/provisioning/engine.py` — 5-phase day-zero bootstrap (preflight → system → network → VRRP → scripts → verify)
- `src/web/app.py` — FastAPI app (50+ routes), Jinja2 templates, SSE logs, session auth
- `src/utils/config.py` — Pydantic models, YAML config loading, section group expansion
- `src/utils/crypto.py` — Fernet + PBKDF2 credential encryption

**State management:** All state is in-memory. Config from single YAML + optional encrypted credentials file. No database.

**Sync order matters:** Section registry order respects dependencies (bridges before ports, pools before DHCP servers). Changes applied as REMOVE → ADD → UPDATE → MOVE.

## Config Files

- `config/ha_config.yaml` — Main config (copy from `ha_config.yaml.example`)
- `config/router_a.yaml`, `config/router_b.yaml` — Per-router variable mappings
- `.credentials.enc` — Optional Fernet-encrypted credentials

## Conventions

- Python 3.11+, async/await throughout
- Pydantic v2 for config validation
- structlog for structured logging
- All RouterOS interactions through `RouterOSClient` (never raw HTTP)
- Section handlers inherit from `SyncSection` base class in `src/sync/sections/base.py`
