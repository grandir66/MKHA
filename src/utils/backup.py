"""Configuration backup and restore utilities."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.main import HAOrchestrator


async def create_backup(
    orchestrator: HAOrchestrator,
    encryption_password: str = "",
) -> tuple[bytes, str]:
    """Create a zip backup of all configuration files.

    If *encryption_password* is provided, sensitive credentials are encrypted
    and stored inside the zip as ``.credentials.enc``.

    Returns ``(zip_bytes, filename)``.
    """
    from src.version import __version__

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"mkha-backup-{timestamp}.zip"

    buf = io.BytesIO()
    config_dir = Path(orchestrator.config_path).parent

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Main config
        config_path = Path(orchestrator.config_path)
        if config_path.exists():
            zf.write(config_path, "ha_config.yaml")

        # 2. Variable files
        for role in ("master", "backup"):
            router_cfg = getattr(orchestrator.config.routers, role)
            if router_cfg.variables_file:
                var_path = config_dir / router_cfg.variables_file
                if var_path.exists():
                    zf.write(var_path, f"variables/{router_cfg.variables_file}")

        # 3. Live router exports (best-effort)
        for role in ("master", "backup"):
            try:
                from src.web.app import api_config_export
                export_data = await api_config_export(role)
                raw = export_data.get("raw_export", "")
                if not raw:
                    lines: list[str] = []
                    for section in export_data.get("sections", []):
                        lines.append(section["path"])
                        lines.extend(section.get("commands", []))
                    raw = "\n".join(lines)
                if raw:
                    zf.writestr(f"exports/{role}_export.rsc", raw)
            except Exception:
                pass  # Router may be unreachable

        # 4. Encrypt credentials if password provided
        if encryption_password:
            from src.utils.crypto import collect_sensitive_fields, encrypt_credentials
            creds = collect_sensitive_fields(orchestrator.config)
            if creds:
                encrypted = encrypt_credentials(creds, encryption_password)
                zf.writestr(".credentials.enc", encrypted)
        else:
            # Include existing credentials file if present
            creds_path = config_dir / ".credentials.enc"
            if creds_path.exists():
                zf.write(creds_path, ".credentials.enc")

        # 5. Metadata
        meta: dict[str, Any] = {
            "mkha_version": __version__,
            "created_at": datetime.now().isoformat(),
            "cluster_name": orchestrator.config.cluster.name,
        }
        zf.writestr("backup_meta.json", json.dumps(meta, indent=2))

    return buf.getvalue(), filename


def validate_backup(zip_bytes: bytes) -> dict[str, Any]:
    """Validate a backup zip and return its metadata."""
    zbuf = io.BytesIO(zip_bytes)
    with zipfile.ZipFile(zbuf, "r") as zf:
        names = zf.namelist()
        has_config = "ha_config.yaml" in names
        has_creds = ".credentials.enc" in names
        meta: dict[str, Any] = {}
        if "backup_meta.json" in names:
            meta = json.loads(zf.read("backup_meta.json"))
        return {
            "valid": has_config,
            "has_credentials": has_creds,
            "files": names,
            "meta": meta,
        }


async def restore_backup(
    zip_bytes: bytes,
    orchestrator: HAOrchestrator,
    encryption_password: str = "",
) -> dict[str, Any]:
    """Restore configuration from a backup zip."""
    config_dir = Path(orchestrator.config_path).parent
    zbuf = io.BytesIO(zip_bytes)
    restored: list[str] = []

    with zipfile.ZipFile(zbuf, "r") as zf:
        # Main config
        if "ha_config.yaml" in zf.namelist():
            (config_dir / "ha_config.yaml").write_bytes(zf.read("ha_config.yaml"))
            restored.append("ha_config.yaml")

        # Variable files
        for name in zf.namelist():
            if name.startswith("variables/"):
                target = config_dir / Path(name).name
                target.write_bytes(zf.read(name))
                restored.append(name)

        # Credentials file
        if ".credentials.enc" in zf.namelist():
            (config_dir / ".credentials.enc").write_bytes(zf.read(".credentials.enc"))
            restored.append(".credentials.enc")

    # Reload config
    from src.utils.config import load_config
    new_config = load_config(orchestrator.config_path)

    # Decrypt credentials if password provided and file was restored
    if encryption_password and ".credentials.enc" in restored:
        from src.utils.crypto import apply_decrypted_credentials, decrypt_credentials
        creds_path = config_dir / ".credentials.enc"
        creds = decrypt_credentials(creds_path.read_bytes(), encryption_password)
        apply_decrypted_credentials(new_config, creds)

    orchestrator.config = new_config
    await orchestrator.reconnect_clients()

    return {"restored_files": restored}
