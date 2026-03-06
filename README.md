# MKHA — MikroTik High Availability Manager

**Automated HA orchestrator for MikroTik RouterOS 7 router pairs.**

MKHA keeps two MikroTik routers in sync, manages VRRP failover, monitors health via quorum witness, and provides a web dashboard for real-time control.

> **Version:** 0.51-beta — Python 3.11+ — Apache 2.0

---

## Features

| Area | What it does |
|------|-------------|
| **Config Sync** | Selective sync of 30 RouterOS sections (firewall, routing, VPN, DHCP, queues, etc.) with per-router variable substitution |
| **VRRP Failover** | Automatic priority management, manual promote/demote, cooldown protection |
| **Quorum & Health** | Ping witness + API/ping health checks with configurable thresholds |
| **Day-Zero Provisioning** | Bootstrap a blank router into the HA cluster: users, services, VRRP, scripts |
| **Web Dashboard** | Real-time status, config diff viewer, side-by-side RouterOS export, live logs (SSE) |
| **Authentication** | Session-based login with PBKDF2-SHA256 password hashing |
| **Encrypted Credentials** | Fernet-encrypted password storage with master password protection |
| **Backup & Restore** | Full config export/import as downloadable zip archive |
| **Notifications** | Webhook, Telegram, and email alerts |
| **CLI / TUI** | Rich terminal menu for headless management |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│               MKHA Orchestrator             │
│  ┌──────────┐ ┌──────────┐ ┌─────────────┐ │
│  │  Sync    │ │  Quorum  │ │    VRRP     │ │
│  │  Engine  │ │  Manager │ │  Controller │ │
│  └────┬─────┘ └────┬─────┘ └──────┬──────┘ │
│       │            │               │        │
│  ┌────┴────────────┴───────────────┴─────┐  │
│  │         RouterOS REST + SSH           │  │
│  └────┬──────────────────────────┬───────┘  │
│       │                          │          │
│  ┌────▼─────┐            ┌──────▼──────┐   │
│  │ Master   │◄──VRRP───►│   Backup    │   │
│  │ Router   │            │   Router    │   │
│  └──────────┘            └─────────────┘   │
└─────────────────────────────────────────────┘
```

MKHA communicates with routers via the **RouterOS 7 REST API** (`/rest`) for configuration changes and **SSH** for textual `/export` retrieval. No packages or agents need to be installed on the routers.

---

## Quick Start

### Option 1 — Docker (recommended)

```bash
git clone https://github.com/grandir66/MKHA.git
cd MKHA
cp config/ha_config.yaml.example config/ha_config.yaml
# Edit config/ha_config.yaml with your router IPs and credentials
docker compose up -d
```

Open `http://localhost:8080` in your browser.

### Option 2 — Local install

```bash
git clone https://github.com/grandir66/MKHA.git
cd MKHA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/ha_config.yaml.example config/ha_config.yaml
# Edit config/ha_config.yaml with your router IPs and credentials
python -m src.main -c config/ha_config.yaml
```

### Option 3 — One-line installer

```bash
curl -fsSL https://raw.githubusercontent.com/grandir66/MKHA/main/install.sh | bash
```

This creates `~/mkha/`, installs dependencies in a virtualenv, and copies the example config.

---

## Configuration

Copy the example and edit it:

```bash
cp config/ha_config.yaml.example config/ha_config.yaml
```

### Minimal config

```yaml
cluster:
  name: my-cluster
  sync_interval_seconds: 60

routers:
  master:
    api_url: http://192.168.1.1/rest
    api_user: ha-sync
    api_password: your-password
  backup:
    api_url: http://192.168.1.2/rest
    api_user: ha-sync
    api_password: your-password

sync:
  enabled_groups:
    - firewall
    - routing
    - dhcp_dns
```

### Router credentials

Credentials can be provided in three ways (in order of preference):

1. **Encrypted file** — passwords stored in a Fernet-encrypted `.credentials.enc` file, protected by a master password (set via web UI or `MKHA_ENCRYPTION_PASSWORD` env var)
2. **Environment variable** — use `api_password_env: ROUTER_B_PASSWORD` to read from env
3. **Plaintext in YAML** — use `api_password: changeme` (not recommended for production)

### Sync groups

Enable only the sections you need:

| Group | RouterOS sections synced |
|-------|------------------------|
| `interfaces` | ethernet, bridge, bridge ports, bridge VLANs, VLANs, bonding, interface lists |
| `ip_addressing` | IP addresses |
| `firewall` | filter, NAT, mangle, raw, address lists |
| `routing` | static routes |
| `dhcp_dns` | pools, DHCP server/networks/leases, DNS static |
| `vpn` | IPsec (profiles, proposals, peers, identities, policies), WireGuard |
| `scripts` | system scripts, schedulers |
| `queues` | simple queues, queue trees |

### Variable substitution

Define per-router variables in `config/router_a.yaml` and `config/router_b.yaml`:

```yaml
variables:
  ROUTER_ID: 10.255.255.1
  LOOPBACK: 10.255.255.1/32
  WAN_IP: 198.51.100.10/30
  WAN_GW: 198.51.100.9
```

The sync engine automatically translates these values when replicating config from master to backup.

---

## Web UI

The dashboard runs on port **8080** (configurable) and provides six pages:

| Page | Description |
|------|-------------|
| **Dashboard** | Cluster status, VRRP state, router health, manual failover controls |
| **Config Diff** | Side-by-side diff of each sync section between master and backup |
| **Router Config** | Full RouterOS `/export` viewer with continuation-line joining |
| **Provisioning** | Day-zero setup wizard for blank routers |
| **Setup** | Router connections, variables, sync toggles, security, encryption, backup |
| **Logs** | Live structured log stream (SSE) |

### Authentication

By default, the web UI is open (no authentication). To enable login:

1. Go to **Setup > Security**
2. Create a user with username and password
3. The login page will appear on next access

Sessions are cookie-based and invalidated on service restart.

### Credential encryption

1. Go to **Setup > Credential Encryption**
2. Enter a master password and click **Encrypt Credentials**
3. Passwords are removed from `ha_config.yaml` and stored in `.credentials.enc`
4. On next startup, provide the password via `MKHA_ENCRYPTION_PASSWORD` env var or interactive prompt

### Backup & Restore

- **Download** a full zip backup from **Setup > Backup & Restore** (optionally encrypts credentials)
- **Restore** by uploading a previously downloaded backup zip

---

## Docker

### Build and run

```bash
docker compose up -d
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `ROUTER_A_PASSWORD` | Master router API password |
| `ROUTER_B_PASSWORD` | Backup router API password |
| `MKHA_ENCRYPTION_PASSWORD` | Master password to decrypt `.credentials.enc` |

### Volumes

Mount your config directory:

```yaml
volumes:
  - ./config:/app/config:ro
```

---

## RouterOS Setup

MKHA requires a dedicated API user on each router:

```routeros
/user group add name=ha-sync policy=api,read,write,policy,test,sensitive
/user add name=ha-sync group=ha-sync password=your-password
```

Enable the REST API (RouterOS 7.1+):

```routeros
/ip service enable www-ssl
/certificate add name=https-cert common-name=router
/ip service set www-ssl certificate=https-cert
```

Or use HTTP (less secure):

```routeros
/ip service enable www
```

### VRRP hook scripts

MKHA can deploy failover hook scripts automatically via the **Provisioning** page, or you can install them manually:

```routeros
/system script add name=ha_failover_hook source=[/tool fetch url="http://mkha-host:8080/static/ha_failover_hook.rsc" as-value output=user]->"data"
/interface vrrp set [find] on-master="/system script run ha_failover_hook" on-backup="/system script run ha_failover_hook"
```

---

## Development

### Prerequisites

- Python 3.11+
- RouterOS 7.1+ routers (for integration testing)

### Setup

```bash
git clone https://github.com/grandir66/MKHA.git
cd MKHA
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
```

### Run tests

```bash
pytest
```

54 tests covering: API client, config parsing, sync diff engine, quorum logic, variable translator, authentication, and encryption.

### Project structure

```
src/
├── api/              # RouterOS REST and SSH clients
├── cli/              # TUI menu (Textual)
├── notifications/    # Webhook, Telegram, email
├── provisioning/     # Day-zero setup engine
├── quorum/           # Health checks and quorum decisions
├── sync/             # Config sync engine
│   └── sections/     # 30 RouterOS section handlers
├── utils/            # Config, auth, crypto, backup, logging
├── vrrp/             # VRRP priority controller
├── web/              # FastAPI app, templates, static assets
├── main.py           # Entry point and orchestrator
└── version.py        # Central version constant
```

---

## License

This project is released under the [Apache License 2.0](LICENSE).
