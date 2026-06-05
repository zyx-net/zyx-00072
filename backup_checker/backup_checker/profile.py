import os
import json
import yaml
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from .constants import (
    EXIT_PROFILE_CONFLICT,
    EXIT_PROFILE_INVALID_JSON,
    EXIT_PROFILE_PERMISSION_DENIED,
    EXIT_PROFILE_UNKNOWN_ALGORITHM,
    EXIT_PROFILE_INVALID_CONFIG,
    EXIT_DUPLICATE_TARGET,
    CONFIG_FILENAME,
    PROFILE_LOG_DIRNAME,
    PROFILE_LOG_FILENAME,
    SUPPORTED_HASH_ALGORITHMS,
)
from .config import (
    ManifestConfig,
    TargetConfig,
    ConfigError,
    DuplicateTargetError,
    _validate_config,
)


class ProfileError(Exception):
    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


class ProfileConflictError(ProfileError):
    def __init__(self, message: str, conflicts: Dict[str, Tuple[str, str]]):
        super().__init__(message, EXIT_PROFILE_CONFLICT)
        self.conflicts = conflicts


class ProfileInvalidJsonError(ProfileError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_PROFILE_INVALID_JSON)


class ProfilePermissionError(ProfileError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_PROFILE_PERMISSION_DENIED)


class ProfileUnknownAlgorithmError(ProfileError):
    def __init__(self, algorithm: str):
        super().__init__(
            f"Unsupported hash algorithm: {algorithm}. "
            f"Use one of: {', '.join(sorted(SUPPORTED_HASH_ALGORITHMS))}",
            EXIT_PROFILE_UNKNOWN_ALGORITHM,
        )
        self.algorithm = algorithm


class ProfileInvalidConfigError(ProfileError):
    def __init__(self, message: str):
        super().__init__(message, EXIT_PROFILE_INVALID_CONFIG)


@dataclass
class ProfileOperationLog:
    timestamp: str
    operation: str
    target_config: str
    backup_path: Optional[str]
    status: str
    details: str


def export_profile(config_path: str, output_path: str) -> str:
    if not os.path.exists(config_path):
        raise ProfileInvalidConfigError(f"Config file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ProfileInvalidConfigError(f"Invalid YAML format: {e}")

    if not isinstance(data, dict) or "manifest" not in data:
        raise ProfileInvalidConfigError("Invalid config: missing 'manifest' section")

    manifest = data["manifest"]
    export_data = {
        "profile_version": "1.0",
        "exported_at": datetime.now().isoformat(),
        "source_config": os.path.abspath(config_path),
        "manifest": {
            "name": manifest.get("name", "backup-check"),
            "source_dir": manifest.get("source_dir", ""),
            "backup_dir": manifest.get("backup_dir", ""),
            "targets": manifest.get("targets", []),
            "exclude_patterns": manifest.get("exclude_patterns", []),
            "hash_algorithm": manifest.get("hash_algorithm", "sha256"),
            "retention_days": manifest.get("retention_days", 30),
        },
    }

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir, exist_ok=True)
        except PermissionError:
            raise ProfilePermissionError(
                f"Permission denied: cannot create directory {output_dir}"
            )

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
    except PermissionError:
        raise ProfilePermissionError(
            f"Permission denied: cannot write to {output_path}"
        )
    except OSError as e:
        raise ProfileError(
            f"Failed to write export file: {e}", EXIT_PROFILE_INVALID_CONFIG
        )

    _log_operation(
        config_path,
        "export",
        output_path,
        None,
        "success",
        f"Exported profile to {output_path}",
    )

    return output_path


def import_profile(
    json_path: str,
    target_config_path: str,
    force: bool = False,
) -> Tuple[str, Optional[str]]:
    if not os.path.exists(json_path):
        raise ProfileInvalidJsonError(f"JSON file not found: {json_path}")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            import_data = json.load(f)
    except json.JSONDecodeError as e:
        raise ProfileInvalidJsonError(f"Invalid JSON format: {e}")
    except PermissionError:
        raise ProfilePermissionError(
            f"Permission denied: cannot read {json_path}"
        )

    _validate_import_data(import_data)

    manifest = import_data["manifest"]
    config_exists = os.path.exists(target_config_path)

    existing_config = None
    if config_exists:
        try:
            with open(target_config_path, "r", encoding="utf-8") as f:
                existing_data = yaml.safe_load(f)
            if existing_data and "manifest" in existing_data:
                existing_config = existing_data["manifest"]
        except (yaml.YAMLError, PermissionError) as e:
            if isinstance(e, PermissionError):
                raise ProfilePermissionError(
                    f"Permission denied: cannot read {target_config_path}"
                )
            raise ProfileInvalidConfigError(
                f"Existing config has invalid YAML: {e}"
            )

    conflicts = {}
    if existing_config:
        conflicts = _detect_conflicts(existing_config, manifest)

    if conflicts and not force:
        conflict_msg = _format_conflict_message(conflicts)
        _log_operation(
            target_config_path,
            "import",
            json_path,
            None,
            "conflict",
            f"Import rejected due to conflicts: {list(conflicts.keys())}",
        )
        raise ProfileConflictError(conflict_msg, conflicts)

    backup_path = None
    if config_exists:
        backup_path = _create_rollback_backup(target_config_path)

    new_config = _build_config_from_import(manifest, target_config_path)

    try:
        _validate_imported_config(new_config)
    except DuplicateTargetError as e:
        if backup_path and os.path.exists(backup_path):
            _restore_from_backup(backup_path, target_config_path)
            _log_operation(
                target_config_path,
                "import",
                json_path,
                backup_path,
                "rolled_back",
                f"Rolled back due to duplicate targets: {e.duplicates}",
            )
        raise ProfileError(
            f"Duplicate target paths found: {', '.join(e.duplicates)}",
            EXIT_DUPLICATE_TARGET,
        )
    except ConfigError as e:
        if backup_path and os.path.exists(backup_path):
            _restore_from_backup(backup_path, target_config_path)
            _log_operation(
                target_config_path,
                "import",
                json_path,
                backup_path,
                "rolled_back",
                f"Rolled back due to config error: {e.message}",
            )
        raise ProfileInvalidConfigError(e.message)

    target_dir = os.path.dirname(os.path.abspath(target_config_path))
    if target_dir and not os.path.exists(target_dir):
        try:
            os.makedirs(target_dir, exist_ok=True)
        except PermissionError:
            if backup_path and os.path.exists(backup_path):
                _log_operation(
                    target_config_path,
                    "import",
                    json_path,
                    backup_path,
                    "rolled_back",
                    f"Rolled back due to permission denied creating directory",
                )
            raise ProfilePermissionError(
                f"Permission denied: cannot create directory {target_dir}"
            )

    try:
        with open(target_config_path, "w", encoding="utf-8") as f:
            yaml.dump(new_config.to_dict(), f, default_flow_style=False, sort_keys=False)
    except PermissionError:
        if backup_path and os.path.exists(backup_path):
            _restore_from_backup(backup_path, target_config_path)
            _log_operation(
                target_config_path,
                "import",
                json_path,
                backup_path,
                "rolled_back",
                f"Rolled back due to permission denied writing config",
            )
        raise ProfilePermissionError(
            f"Permission denied: cannot write to {target_config_path}"
        )
    except OSError as e:
        if backup_path and os.path.exists(backup_path):
            _restore_from_backup(backup_path, target_config_path)
            _log_operation(
                target_config_path,
                "import",
                json_path,
                backup_path,
                "rolled_back",
                f"Rolled back due to OS error: {e}",
            )
        raise ProfileError(
            f"Failed to write config file: {e}", EXIT_PROFILE_INVALID_CONFIG
        )

    operation = "overwrite" if config_exists else "import"
    status = "overwritten" if config_exists else "imported"
    _log_operation(
        target_config_path,
        operation,
        json_path,
        backup_path,
        status,
        f"Successfully imported profile from {json_path}"
        + (f" (overwrote existing config, backup at {backup_path})" if config_exists else ""),
    )

    return target_config_path, backup_path


def _validate_import_data(data: Dict) -> None:
    if not isinstance(data, dict):
        raise ProfileInvalidJsonError("Import data must be a JSON object")

    if "manifest" not in data:
        raise ProfileInvalidJsonError("Import data missing 'manifest' section")

    manifest = data["manifest"]
    if not isinstance(manifest, dict):
        raise ProfileInvalidJsonError("'manifest' must be an object")

    required_fields = ["source_dir", "backup_dir", "targets"]
    for field in required_fields:
        if field not in manifest:
            raise ProfileInvalidJsonError(
                f"Import data missing required field: manifest.{field}"
            )

    if not isinstance(manifest["targets"], list):
        raise ProfileInvalidJsonError("manifest.targets must be an array")

    if "hash_algorithm" in manifest:
        algo = manifest["hash_algorithm"]
        if algo not in SUPPORTED_HASH_ALGORITHMS:
            raise ProfileUnknownAlgorithmError(algo)

    if "exclude_patterns" in manifest and not isinstance(manifest["exclude_patterns"], list):
        raise ProfileInvalidJsonError("manifest.exclude_patterns must be an array")


def _detect_conflicts(
    existing: Dict, incoming: Dict
) -> Dict[str, Tuple[str, str]]:
    conflicts = {}

    key_fields = [
        ("source_dir", "source directory"),
        ("backup_dir", "backup directory"),
        ("hash_algorithm", "hash algorithm"),
    ]

    for key, display in key_fields:
        existing_val = existing.get(key)
        incoming_val = incoming.get(key)
        if existing_val is not None and incoming_val is not None and existing_val != incoming_val:
            conflicts[key] = (str(existing_val), str(incoming_val))

    existing_target_paths = {t.get("path", "") for t in existing.get("targets", [])}
    incoming_target_paths = {t.get("path", "") for t in incoming.get("targets", [])}
    if existing_target_paths != incoming_target_paths:
        conflicts["targets"] = (
            f"{len(existing_target_paths)} targets: {', '.join(sorted(existing_target_paths))}",
            f"{len(incoming_target_paths)} targets: {', '.join(sorted(incoming_target_paths))}",
        )

    existing_excludes = set(existing.get("exclude_patterns", []))
    incoming_excludes = set(incoming.get("exclude_patterns", []))
    if existing_excludes != incoming_excludes:
        conflicts["exclude_patterns"] = (
            f"{len(existing_excludes)} patterns",
            f"{len(incoming_excludes)} patterns",
        )

    return conflicts


def _format_conflict_message(conflicts: Dict[str, Tuple[str, str]]) -> str:
    lines = ["Configuration conflicts detected. Use --force to overwrite:"]
    for key, (existing, incoming) in conflicts.items():
        lines.append(f"  {key}:")
        lines.append(f"    existing: {existing}")
        lines.append(f"    incoming: {incoming}")
    return "\n".join(lines)


def _create_rollback_backup(config_path: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{config_path}.bak.{timestamp}"
    try:
        shutil.copy2(config_path, backup_path)
    except PermissionError:
        raise ProfilePermissionError(
            f"Permission denied: cannot create backup at {backup_path}"
        )
    return backup_path


def _restore_from_backup(backup_path: str, target_path: str) -> None:
    try:
        shutil.copy2(backup_path, target_path)
    except Exception:
        pass


def _build_config_from_import(manifest: Dict, config_path: str) -> ManifestConfig:
    targets_data = manifest.get("targets", [])
    targets = [TargetConfig(**t) for t in targets_data]

    return ManifestConfig(
        name=manifest.get("name", "backup-check"),
        source_dir=manifest.get("source_dir", ""),
        backup_dir=manifest.get("backup_dir", ""),
        targets=targets,
        retention_days=manifest.get("retention_days", 30),
        exclude_patterns=manifest.get("exclude_patterns", []),
        hash_algorithm=manifest.get("hash_algorithm", "sha256"),
        config_path=config_path,
    )


def _validate_imported_config(config: ManifestConfig) -> None:
    _validate_config(config)


def _log_operation(
    config_path: str,
    operation: str,
    target_file: str,
    backup_path: Optional[str],
    status: str,
    details: str,
) -> None:
    base_dir = os.path.dirname(os.path.abspath(config_path))
    log_dir = os.path.join(base_dir, PROFILE_LOG_DIRNAME)

    try:
        os.makedirs(log_dir, exist_ok=True)
    except (PermissionError, OSError):
        return

    log_path = os.path.join(log_dir, PROFILE_LOG_FILENAME)
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "config_path": os.path.abspath(config_path),
        "target_file": os.path.abspath(target_file) if target_file else None,
        "backup_path": os.path.abspath(backup_path) if backup_path else None,
        "status": status,
        "details": details,
    }

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except (PermissionError, OSError):
        pass


def read_operation_logs(config_path: str) -> List[ProfileOperationLog]:
    base_dir = os.path.dirname(os.path.abspath(config_path))
    log_dir = os.path.join(base_dir, PROFILE_LOG_DIRNAME)
    log_path = os.path.join(log_dir, PROFILE_LOG_FILENAME)

    logs = []
    if not os.path.exists(log_path):
        return logs

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    logs.append(
                        ProfileOperationLog(
                            timestamp=data.get("timestamp", ""),
                            operation=data.get("operation", ""),
                            target_config=data.get("target_file", ""),
                            backup_path=data.get("backup_path"),
                            status=data.get("status", ""),
                            details=data.get("details", ""),
                        )
                    )
                except json.JSONDecodeError:
                    continue
    except (PermissionError, OSError):
        pass

    return logs
