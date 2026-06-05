import os
import yaml
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from .constants import EXIT_CONFIG_ERROR, EXIT_DUPLICATE_TARGET, CONFIG_FILENAME


@dataclass
class TargetConfig:
    path: str
    description: str = ""


@dataclass
class ManifestConfig:
    name: str = "backup-check"
    source_dir: str = ""
    backup_dir: str = ""
    targets: List[TargetConfig] = field(default_factory=list)
    retention_days: int = 30
    exclude_patterns: List[str] = field(default_factory=list)
    hash_algorithm: str = "sha256"
    config_path: str = ""

    @classmethod
    def from_dict(cls, data: Dict, config_path: str) -> "ManifestConfig":
        manifest = data.get("manifest", {})
        targets_data = manifest.get("targets", [])
        targets = [TargetConfig(**t) for t in targets_data]

        return cls(
            name=manifest.get("name", "backup-check"),
            source_dir=manifest.get("source_dir", ""),
            backup_dir=manifest.get("backup_dir", ""),
            targets=targets,
            retention_days=manifest.get("retention_days", 30),
            exclude_patterns=manifest.get("exclude_patterns", []),
            hash_algorithm=manifest.get("hash_algorithm", "sha256"),
            config_path=config_path,
        )

    def to_dict(self) -> Dict:
        return {
            "manifest": {
                "name": self.name,
                "source_dir": self.source_dir,
                "backup_dir": self.backup_dir,
                "targets": [
                    {"path": t.path, "description": t.description}
                    for t in self.targets
                ],
                "retention_days": self.retention_days,
                "exclude_patterns": self.exclude_patterns,
                "hash_algorithm": self.hash_algorithm,
            }
        }

    def resolve_paths(self) -> None:
        base_dir = os.path.dirname(os.path.abspath(self.config_path))
        if not os.path.isabs(self.source_dir):
            self.source_dir = os.path.normpath(os.path.join(base_dir, self.source_dir))
        if not os.path.isabs(self.backup_dir):
            self.backup_dir = os.path.normpath(os.path.join(base_dir, self.backup_dir))


class ConfigError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_CONFIG_ERROR):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


class DuplicateTargetError(ConfigError):
    def __init__(self, duplicates: List[str]):
        paths = ", ".join(duplicates)
        super().__init__(
            f"Duplicate target paths found: {paths}",
            EXIT_DUPLICATE_TARGET,
        )
        self.duplicates = duplicates


def init_config(
    output_dir: str,
    source_dir: str,
    backup_dir: str,
    name: str = "backup-check",
) -> str:
    config_path = os.path.join(output_dir, CONFIG_FILENAME)

    if os.path.exists(config_path):
        raise ConfigError(f"Config file already exists: {config_path}")

    config = ManifestConfig(
        name=name,
        source_dir=source_dir,
        backup_dir=backup_dir,
        targets=[
            TargetConfig(path="documents/", description="Important documents"),
            TargetConfig(path="database/", description="Database backups"),
        ],
        exclude_patterns=["*.tmp", "*.log", "*.swp", ".DS_Store"],
        hash_algorithm="sha256",
    )

    os.makedirs(output_dir, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)

    return config_path


def load_config(config_path: str) -> ManifestConfig:
    if not os.path.exists(config_path):
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"Invalid YAML format: {e}")

    if not isinstance(data, dict) or "manifest" not in data:
        raise ConfigError("Invalid config: missing 'manifest' section")

    config = ManifestConfig.from_dict(data, config_path)
    _validate_config(config)
    config.resolve_paths()

    return config


def _validate_config(config: ManifestConfig) -> None:
    if not config.source_dir:
        raise ConfigError("source_dir is required")

    if not config.backup_dir:
        raise ConfigError("backup_dir is required")

    if not config.targets:
        raise ConfigError("At least one target is required")

    seen_paths = set()
    duplicates = []
    for target in config.targets:
        if not target.path:
            raise ConfigError("Target path cannot be empty")
        if target.path in seen_paths:
            duplicates.append(target.path)
        else:
            seen_paths.add(target.path)

    if duplicates:
        raise DuplicateTargetError(duplicates)

    if config.hash_algorithm not in ["md5", "sha1", "sha256", "sha512"]:
        raise ConfigError(
            f"Unsupported hash algorithm: {config.hash_algorithm}. "
            f"Use one of: md5, sha1, sha256, sha512"
        )


def find_config(start_dir: Optional[str] = None) -> str:
    if start_dir is None:
        start_dir = os.getcwd()

    current = os.path.abspath(start_dir)
    while True:
        candidate = os.path.join(current, CONFIG_FILENAME)
        if os.path.exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    raise ConfigError(
        f"No {CONFIG_FILENAME} found in {start_dir} or any parent directory"
    )
