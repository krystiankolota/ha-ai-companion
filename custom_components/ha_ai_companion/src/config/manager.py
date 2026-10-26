"""
Configuration Manager

Handles all configuration file operations with:
- ruamel.yaml for comment preservation
- Atomic file writes
- Automatic backups with rotation
- Home Assistant validation
- Rollback on failure
"""
import asyncio
import base64
import hashlib
import json
import os
import re
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

_LINE_NO_RE = re.compile(r"line \d+")


def _problem_key(line: str) -> str:
    # Line numbers shift when a proposal adds lines; compare problems without them.
    return _LINE_NO_RE.sub("line N", line).strip()


class ConfigurationError(Exception):
    """Base exception for configuration errors."""
    pass


class ValidationError(ConfigurationError):
    """Raised when configuration validation fails."""
    pass


class ConfigurationManager:
    """
    Manages Home Assistant configuration files with safety features.

    Features:
    - Comment-preserving YAML operations
    - Atomic file writes (write to temp, then move)
    - Automatic backups before changes
    - Backup rotation (keeps max_backups most recent)
    - Home Assistant validation integration
    - Automatic rollback on validation failure
    - Path traversal protection
    """

    def __init__(
        self,
        config_dir: str,
        backup_dir: str,
        max_backups: int = 10,
        ha_check_config_cmd: str = "ha core check",
        hass = None
    ):
        """
        Initialize ConfigurationManager.

        Args:
            config_dir: Path to Home Assistant config directory
            backup_dir: Path to backup directory
            max_backups: Maximum number of backups to keep per file
            ha_check_config_cmd: Command to run HA config validation
            hass: Home Assistant instance (for custom component mode)
        """
        self.config_dir = Path(config_dir).resolve()
        self.backup_dir = Path(backup_dir).resolve()
        self.max_backups = max_backups
        self.ha_check_config_cmd = ha_check_config_cmd
        self.hass = hass
        self._write_lock: Optional[asyncio.Lock] = None
        # Initialize ruamel.yaml with comment preservation
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.default_flow_style = False
        self.yaml.indent(mapping=2, sequence=2, offset=2)

        # Ensure directories exist
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_precheck()

        logger.info(f"ConfigurationManager initialized:")
        logger.info(f"  Config dir: {self.config_dir}")
        logger.info(f"  Backup dir: {self.backup_dir}")
        logger.info(f"  Max backups: {self.max_backups}")

    def _validate_path(self, file_path: str) -> Path:
        """
        Validate and resolve file path to prevent path traversal.

        Args:
            file_path: Relative path to config file

        Returns:
            Resolved absolute Path object

        Raises:
            ConfigurationError: If path is invalid or outside config_dir
        """
        # secrets.yaml holds raw credentials in cleartext — block all read/write
        # access at this single choke point (previously only search_config_files
        # filtered it out; propose/patch tools could still read or overwrite it).
        if Path(file_path).name == "secrets.yaml":
            raise ConfigurationError("Access to secrets.yaml is not permitted")

        # Resolve relative path against config_dir
        full_path = (self.config_dir / file_path).resolve()

        # Ensure path is within config_dir (prevents path traversal)
        # Use os.sep suffix to avoid false matches like /config2 matching /config
        config_str = str(self.config_dir)
        full_str = str(full_path)
        if full_str != config_str and not full_str.startswith(config_str + os.sep):
            raise ConfigurationError(
                f"Invalid path: {file_path} is outside config directory"
            )

        return full_path

    async def read_file_raw(self, file_path: str, allow_missing: bool = False) -> Optional[str]:
        """
        Read a configuration file as raw text without parsing.

        Args:
            file_path: Relative path to config file (e.g., "configuration.yaml")
            allow_missing: If True, return None for missing files instead of raising error

        Returns:
            Raw file content as string, or None if file doesn't exist and allow_missing=True

        Raises:
            ConfigurationError: If file cannot be read (and allow_missing=False for missing files)
        """
        full_path = self._validate_path(file_path)

        if not full_path.exists():
            if allow_missing:
                logger.debug(f"File not found (allowed): {file_path}")
                return None
            raise ConfigurationError(f"File not found: {file_path}")

        try:
            logger.debug(f"Reading raw config file: {file_path}")
            with open(full_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            raise ConfigurationError(f"Error reading {file_path}: {str(e)}")

    def _create_backup(self, file_path: Path) -> Path:
        """
        Create a timestamped backup of a file.

        Args:
            file_path: Path to file to backup

        Returns:
            Path to created backup file
        """
        if not file_path.exists():
            raise ConfigurationError(f"Cannot backup non-existent file: {file_path}")

        # Generate backup filename with timestamp (microseconds prevent same-second collisions)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}.backup"
        backup_path = self.backup_dir / backup_name

        # Copy file to backup
        shutil.copy2(file_path, backup_path)
        logger.info(f"Created backup: {backup_path.name}")

        return backup_path

    def _rotate_backups(self, file_stem: str):
        """
        Remove old backups, keeping only max_backups most recent.

        Args:
            file_stem: Stem of the original file (e.g., "configuration")
        """
        # Find all backups for this file
        pattern = f"{file_stem}_*.backup"
        backups = sorted(
            self.backup_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True  # Newest first
        )

        # Remove old backups
        for backup in backups[self.max_backups:]:
            backup.unlink()
            logger.info(f"Removed old backup: {backup.name}")

    async def write_file_raw(
        self,
        file_path: str,
        content: str,
        create_backup: bool = True
    ) -> None:
        """
        Write raw content to a configuration file without YAML parsing.

        This is useful for writing files that need exact formatting preserved,
        or for writing non-YAML files (like JSON for virtual device/entity files).

        Process:
        1. Validate path
        2. Create backup if file exists
        3. Write to temporary file
        4. Atomically move temp file to target
        5. Rotate old backups
        6. Rollback on any failure

        Note: Validation should be done separately after all files are written
        using the validate_config() method.

        Args:
            file_path: Relative path to config file
            content: Raw string content to write
            create_backup: Whether to backup existing file

        Raises:
            ConfigurationError: If write fails
        """
        # Handle virtual files (devices, entities, areas)
        if file_path.startswith("devices/"):
            await self._write_device_json(file_path, content)
            return
        elif file_path.startswith("entities/"):
            await self._write_entity_json(file_path, content)
            return
        elif file_path.startswith("areas/"):
            await self._write_area_json(file_path, content)
            return
        elif file_path == "lovelace.yaml":
            await self._write_lovelace_yaml(content, url_path=None)
            return
        elif file_path.startswith("lovelace/") and file_path.endswith(".yaml"):
            # Named dashboard: lovelace/{url_path}.yaml
            dashboard_url_path = file_path[len("lovelace/"):-len(".yaml")] or None
            await self._write_lovelace_yaml(content, url_path=dashboard_url_path)
            return

        # Regular file handling
        full_path = self._validate_path(file_path)
        backup_path = None
        temp_path = full_path.with_suffix(f"{full_path.suffix}.tmp")
        is_new_file = not full_path.exists()

        try:
            # Step 1: Create backup if file exists
            if create_backup and full_path.exists():
                backup_path = self._create_backup(full_path)

            # Step 1b: Ensure parent directory exists for new files
            if is_new_file:
                full_path.parent.mkdir(parents=True, exist_ok=True)
                logger.info(f"Creating new config file: {file_path}")
            else:
                logger.info(f"Writing raw config file: {file_path}")

            # Step 2: Write to temporary file
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(content)

            # Step 3: Atomically move temp file to target
            shutil.move(str(temp_path), str(full_path))

            # Step 4: Rotate old backups (only for existing files)
            if create_backup and not is_new_file:
                self._rotate_backups(full_path.stem)

            if is_new_file:
                logger.info(f"Successfully created new file: {file_path}")
            else:
                logger.info(f"Successfully wrote raw file: {file_path}")

        except Exception as e:
            # Cleanup temp file on any error
            if temp_path.exists():
                temp_path.unlink()

            # Rollback on write failure
            if is_new_file:
                # Remove the newly created file
                if full_path.exists():
                    full_path.unlink()
                    logger.info(f"Removed newly created file after error: {file_path}")
            elif backup_path and backup_path.exists():
                # Restore from backup for existing files
                shutil.copy2(backup_path, full_path)
                logger.info(f"Restored from backup after error: {backup_path.name}")

            raise ConfigurationError(f"Error writing raw file {file_path}: {str(e)}")

    async def _write_device_json(self, file_path: str, json_content: str):
        """
        Write device changes.

        Uses hass API in custom component mode, WebSocket in add-on mode.
        """
        import json

        device_id = file_path.replace("devices/", "").replace(".json", "")
        device = json.loads(json_content)

        # Custom component mode: use hass directly
        if self.hass is not None:
            from homeassistant.helpers import device_registry as dr

            device_reg = dr.async_get(self.hass)

            # Update device
            device_reg.async_update_device(
                device_id=device_id,
                name_by_user=device.get('name_by_user'),
                area_id=device.get('area_id'),
                disabled_by=device.get('disabled_by')
            )
            logger.info(f"Updated device via hass API: {device_id}")
            return

        # Add-on mode: use WebSocket API
        from ..ha.ha_websocket import HomeAssistantWebSocket

        supervisor_token = os.getenv('SUPERVISOR_TOKEN')
        if not supervisor_token:
            raise ConfigurationError("SUPERVISOR_TOKEN not available")

        ws_url = "ws://supervisor/core/websocket"
        ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)

        try:
            await ws_client.connect()
            await ws_client.update_device(
                device_id=device_id,
                name_by_user=device.get('name_by_user'),
                area_id=device.get('area_id'),
                labels=device.get('labels', []),
                disabled_by=device.get('disabled_by')
            )
            logger.info(f"Updated device via WebSocket: {device_id}")
        finally:
            await ws_client.close()

    async def _write_entity_json(self, file_path: str, json_content: str):
        """
        Write entity changes.

        Uses hass API in custom component mode, WebSocket in add-on mode.
        """
        import json

        entity_id = file_path.replace("entities/", "").replace(".json", "")
        entity = json.loads(json_content)

        # Custom component mode: use hass directly
        if self.hass is not None:
            from homeassistant.helpers import entity_registry as er

            entity_reg = er.async_get(self.hass)

            # Update entity
            entity_reg.async_update_entity(
                entity_id=entity_id,
                name=entity.get('name'),
                icon=entity.get('icon'),
                area_id=entity.get('area_id')
            )
            logger.info(f"Updated entity via hass API: {entity_id}")
            return

        # Add-on mode: use WebSocket API
        from ..ha.ha_websocket import HomeAssistantWebSocket

        supervisor_token = os.getenv('SUPERVISOR_TOKEN')
        if not supervisor_token:
            raise ConfigurationError("SUPERVISOR_TOKEN not available")

        ws_url = "ws://supervisor/core/websocket"
        ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)

        try:
            await ws_client.connect()
            await ws_client.update_entity(
                entity_id=entity_id,
                name=entity.get('name'),
                icon=entity.get('icon'),
                area_id=entity.get('area_id'),
                labels=entity.get('labels', [])
            )
            logger.info(f"Updated entity via WebSocket: {entity_id}")
        finally:
            await ws_client.close()

    async def _write_area_json(self, file_path: str, json_content: str):
        """
        Write area changes. Creates area if it doesn't exist.

        Uses hass API in custom component mode, WebSocket in add-on mode.
        """
        import json

        area_id = file_path.replace("areas/", "").replace(".json", "")
        area = json.loads(json_content)

        # Custom component mode: use hass directly
        if self.hass is not None:
            from homeassistant.helpers import area_registry as ar

            area_reg = ar.async_get(self.hass)

            # Check if area exists
            existing_area = area_reg.async_get_area(area_id)

            if existing_area:
                # Update existing area
                area_reg.async_update(
                    area_id=area_id,
                    name=area.get('name'),
                    picture=area.get('picture'),
                    icon=area.get('icon'),
                    aliases=set(area.get('aliases', []))
                )
                logger.info(f"Updated area via hass API: {area_id}")
            else:
                # Create new area - name is required
                if not area.get('name'):
                    raise ConfigurationError(f"Cannot create area {area_id}: 'name' is required")

                area_reg.async_create(
                    name=area.get('name'),
                    picture=area.get('picture'),
                    icon=area.get('icon'),
                    aliases=set(area.get('aliases', []))
                )
                logger.info(f"Created new area via hass API: {area.get('name')}")
            return

        # Add-on mode: use WebSocket API
        from ..ha.ha_websocket import HomeAssistantWebSocket

        supervisor_token = os.getenv('SUPERVISOR_TOKEN')
        if not supervisor_token:
            raise ConfigurationError("SUPERVISOR_TOKEN not available")

        ws_url = "ws://supervisor/core/websocket"
        ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)

        try:
            await ws_client.connect()

            # Check if area exists
            areas = await ws_client.list_areas()
            area_exists = any(a.get('area_id') == area_id for a in areas)

            if area_exists:
                # Update existing area
                await ws_client.update_area(
                    area_id=area_id,
                    name=area.get('name'),
                    picture=area.get('picture'),
                    icon=area.get('icon'),
                    aliases=area.get('aliases', [])
                )
                logger.info(f"Updated area via WebSocket: {area_id}")
            else:
                # Create new area - name is required
                if not area.get('name'):
                    raise ConfigurationError(f"Cannot create area {area_id}: 'name' is required")

                await ws_client.create_area(
                    name=area.get('name'),
                    picture=area.get('picture'),
                    icon=area.get('icon'),
                    aliases=area.get('aliases', [])
                )
                logger.info(f"Created new area via WebSocket: {area.get('name')}")
        finally:
            await ws_client.close()

    async def _write_lovelace_yaml(self, yaml_content: str, url_path: Optional[str] = None):
        """
        Write Lovelace config for a specific dashboard.

        Args:
            yaml_content: YAML string to write
            url_path: Dashboard URL path (None = default dashboard)

        Uses hass API in custom component mode, WebSocket in add-on mode.
        """
        label = url_path or "default"
        config = self.yaml.load(yaml_content)

        # Custom component mode: use hass directly
        if self.hass is not None:
            from homeassistant.components.lovelace.const import DOMAIN as LOVELACE_DOMAIN

            logger.info(f"Saving Lovelace config ({label}) via hass API")

            if LOVELACE_DOMAIN not in self.hass.data:
                raise ConfigurationError("Lovelace component not loaded")

            lovelace_data = self.hass.data.get(LOVELACE_DOMAIN)
            if not (lovelace_data and hasattr(lovelace_data, 'dashboards')):
                raise ConfigurationError("Lovelace dashboards not available")

            dashboards = lovelace_data.dashboards
            if url_path is None:
                dashboard = dashboards.get(None) or dashboards.get('lovelace')
            else:
                dashboard = dashboards.get(url_path)

            if not dashboard:
                raise ConfigurationError(f"Dashboard '{label}' not found")

            await dashboard.async_save(config)
            logger.info(f"Updated Lovelace config ({label}) via hass API")
            return

        # Add-on mode: use WebSocket API
        from ..ha.ha_websocket import HomeAssistantWebSocket

        supervisor_token = os.getenv('SUPERVISOR_TOKEN')
        if not supervisor_token:
            raise ConfigurationError("SUPERVISOR_TOKEN not available")

        ws_url = "ws://supervisor/core/websocket"
        ws_client = HomeAssistantWebSocket(ws_url, supervisor_token)

        try:
            await ws_client.connect()
            try:
                await ws_client.save_lovelace_config(config, url_path)
                logger.info(f"Updated Lovelace config ({label}) via WebSocket")
            except Exception as e:
                if "Not supported" in str(e):
                    # YAML-mode dashboard — HA rejects WebSocket writes; write file directly
                    logger.info(f"WebSocket save not supported for '{label}' (YAML mode) — writing file directly")
                    if url_path is None:
                        lv_file = self.config_dir / "lovelace.yaml"
                    else:
                        lv_file = self.config_dir / "lovelace" / f"{url_path}.yaml"
                    lv_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(lv_file, 'w', encoding='utf-8') as f:
                        f.write(yaml_content)
                    logger.info(f"Wrote Lovelace config ({label}) directly to {lv_file}")
                else:
                    raise
        finally:
            await ws_client.close()

    async def validate_config(self) -> Optional[str]:
        """
        Run Home Assistant configuration validation.

        Uses Supervisor API in add-on mode, or HA internal validation in custom component mode.

        Raises:
            ValidationError: If validation fails
        """
        logger.info("Running Home Assistant configuration validation...")

        # If running as custom component, use HA's internal validation
        if self.hass is not None:
            try:
                from homeassistant.config import async_check_ha_config_file

                logger.info("Using Home Assistant internal validation (custom component mode)")

                # Run HA's internal validation
                # Returns None if valid, or error string if invalid
                config_error = await async_check_ha_config_file(self.hass)

                if config_error:
                    logger.error(f"Configuration validation failed:\n{config_error}")
                    raise ValidationError(
                        f"Home Assistant configuration validation failed:\n{config_error}"
                    )

                logger.info("Configuration validation passed ✓")
                return

            except ValidationError:
                raise
            except Exception as e:
                logger.error(f"Validation error: {str(e)}")
                raise ValidationError(f"Validation failed: {str(e)}")

        # Add-on mode: use Supervisor API
        try:
            import aiohttp

            supervisor_token = os.getenv('SUPERVISOR_TOKEN')
            if not supervisor_token:
                logger.warning("SUPERVISOR_TOKEN not available, skipping validation")
                return

            # Use supervisor API to validate config
            url = "http://supervisor/core/api/config/core/check_config"
            headers = {
                "Authorization": f"Bearer {supervisor_token}",
                "Content-Type": "application/json"
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers) as response:
                    if response.status == 200:
                        result = await response.json()

                        # Check if validation passed
                        if result.get('result') == 'valid':
                            warnings = result.get('warnings')
                            if warnings:
                                logger.warning(f"Configuration valid with warnings:\n{warnings}")
                                return str(warnings)
                            logger.info("Configuration validation passed ✓")
                            return None
                        else:
                            # Validation failed - extract error details
                            errors = result.get('errors', 'Unknown validation error')
                            raise ValidationError(
                                f"Home Assistant configuration validation failed:\n{errors}"
                            )
                    else:
                        error_text = await response.text()
                        logger.error(f"Validation API returned status {response.status}: {error_text}")
                        raise ValidationError(
                            f"Configuration validation failed (HTTP {response.status})"
                        )

        except ValidationError:
            raise
        except Exception as e:
            logger.error(f"Validation error: {str(e)}")
            raise ValidationError(f"Validation failed: {str(e)}")

    @property
    def write_lock(self) -> asyncio.Lock:
        """Serializes disk writes between approval and the proposal pre-check (created lazily inside the loop)."""
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        return self._write_lock

    async def _check_text(self) -> str:
        """HA config-check problems as text ('' when clean)."""
        try:
            return await self.validate_config() or ""
        except ValidationError as e:
            return str(e)

    @staticmethod
    def _replace_bytes(path: Path, data: bytes) -> None:
        tmp = path.with_name(path.name + ".precheck.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    @property
    def _precheck_manifest(self) -> Path:
        return self.backup_dir / "precheck_recovery.json"

    def _restore_precheck_entry(self, entry: Dict[str, Any]) -> None:
        """Put back one pre-check file, unless it no longer holds exactly what we wrote."""
        path = self._validate_path(entry["path"])
        current = path.read_bytes() if path.exists() else None
        if current is None or hashlib.sha256(current).hexdigest() != entry["written_sha"]:
            if current != (None if entry["original"] is None else base64.b64decode(entry["original"])):
                logger.warning(f"[PRECHECK] {entry['path']} changed during pre-check; leaving it as is")
            return
        if entry["original"] is None:
            path.unlink()
        else:
            self._replace_bytes(path, base64.b64decode(entry["original"]))

    def _restore_precheck(self, entries: List[Dict[str, Any]]) -> None:
        """Restore every entry; keep only unresolved entries in the manifest for startup recovery."""
        failed, errors = [], []
        for entry in reversed(entries):
            try:
                self._restore_precheck_entry(entry)
            except Exception as e:
                failed.append(entry)
                errors.append(f"{entry['path']}: {e}")
        if failed:
            self._replace_bytes(self._precheck_manifest, json.dumps(failed).encode("utf-8"))
            logger.error(f"[PRECHECK] Restore failed, originals kept in {self._precheck_manifest}: {errors}")
            raise ConfigurationError(f"Pre-check restore failed: {errors}")
        self._precheck_manifest.unlink(missing_ok=True)

    def _recover_interrupted_precheck(self) -> None:
        if not self._precheck_manifest.exists():
            return
        logger.warning("[PRECHECK] Recovering files from an interrupted pre-check")
        try:
            self._restore_precheck(json.loads(self._precheck_manifest.read_text(encoding="utf-8")))
        except Exception as e:
            logger.error(f"[PRECHECK] Startup recovery failed: {e}")

    async def precheck_files(self, files: List[Tuple[str, str]]) -> Optional[str]:
        """
        Run HA's config check against proposed content before the user approves it.

        Briefly writes each (file_path, new_content), checks, and always restores the
        original. Originals are first saved to a recovery manifest that startup replays
        if the process dies mid-check. Returns only problems that the current config does
        not already have, or None. Skipped (None) when no HA validation backend exists.
        """
        if self.hass is None and not os.getenv("SUPERVISOR_TOKEN"):
            return None
        async with self.write_lock:
            self._recover_interrupted_precheck()
            if self._precheck_manifest.exists():
                raise ConfigurationError("Unresolved pre-check recovery; skipping pre-check")
            before = await self._check_text()
            entries = []
            for file_path, content in files:
                path = self._validate_path(file_path)
                entries.append({
                    "path": file_path,
                    "original": base64.b64encode(path.read_bytes()).decode() if path.exists() else None,
                    "written": content.encode("utf-8"),
                })
            manifest = [{"path": e["path"], "original": e["original"],
                         "written_sha": hashlib.sha256(e["written"]).hexdigest()} for e in entries]
            self._replace_bytes(self._precheck_manifest, json.dumps(manifest).encode("utf-8"))
            try:
                for e in entries:
                    path = self._validate_path(e["path"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._replace_bytes(path, e["written"])
                after = await self._check_text()
            finally:
                self._restore_precheck(manifest)
        known = {_problem_key(line) for line in before.splitlines()}
        new = [line for line in after.splitlines() if line.strip() and _problem_key(line) not in known]
        if new:
            logger.info(f"[PRECHECK] Proposal adds {len(new)} config-check problem(s)")
        return "\n".join(new) or None

    def list_backups(self, file_path: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List available backups.

        Args:
            file_path: Optional filter for specific file's backups

        Returns:
            List of backup info dictionaries with keys:
                - name: Backup filename
                - original_file: Original file name
                - timestamp: Backup creation time
                - size: File size in bytes
        """
        if file_path:
            full_path = self._validate_path(file_path)
            pattern = f"{full_path.stem}_*.backup"
        else:
            pattern = "*.backup"

        backups = []
        for backup in sorted(
            self.backup_dir.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        ):
            stat = backup.stat()

            # Parse original filename from backup name
            # Format: {stem}_{YYYYMMDD}_{HHMMSS}.{ext}.backup — timestamp has two _ parts
            parts = backup.stem.rsplit('_', 3)
            original_name = parts[0] if len(parts) > 1 else backup.stem

            backups.append({
                "name": backup.name,
                "original_file": original_name,
                "timestamp": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "size": stat.st_size
            })

        return backups

    async def restore_backup(
        self,
        backup_name: str
    ) -> None:
        """
        Restore a configuration file from a backup.

        Note: Validation should be done separately after restore using validate_config() method.

        Args:
            backup_name: Name of backup file to restore

        Raises:
            ConfigurationError: If backup not found or restore fails
        """
        backup_path = self.backup_dir / backup_name

        if not backup_path.exists():
            raise ConfigurationError(f"Backup not found: {backup_name}")

        # Parse original filename from backup
        # Format: {stem}_{YYYYMMDD}_{HHMMSS}.{ext}.backup — timestamp has two _ parts
        parts = backup_path.stem.rsplit('_', 3)
        if len(parts) < 2:
            raise ConfigurationError(f"Invalid backup name format: {backup_name}")

        original_stem = parts[0]
        # Get extension from before .backup
        ext = backup_path.suffixes[-2] if len(backup_path.suffixes) > 1 else '.yaml'
        original_name = f"{original_stem}{ext}"

        original_path = self.config_dir / original_name

        logger.info(f"Restoring {original_name} from {backup_name}")

        # Create backup of current file before restore
        temp_backup = None
        if original_path.exists():
            temp_backup = self._create_backup(original_path)

        try:
            # Copy backup to original location
            shutil.copy2(backup_path, original_path)

            logger.info(f"Successfully restored: {original_name}")

        except Exception as e:
            # Rollback on any error
            if temp_backup and temp_backup.exists():
                shutil.copy2(temp_backup, original_path)
            raise ConfigurationError(f"Error restoring backup: {str(e)}")
