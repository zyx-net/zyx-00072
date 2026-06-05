import os
import time
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .scanner import ScanResult, FileInfo
from .config import ManifestConfig
from .constants import (
    FILE_STATUS_MISSING,
    FILE_STATUS_EXPIRED,
    FILE_STATUS_CORRUPT,
    FILE_STATUS_UNREGISTERED,
    FILE_STATUS_OK,
)


@dataclass
class FileDiff:
    relative_path: str
    status: str = ""
    source_checksum: str = ""
    backup_checksum: str = ""
    source_mtime: float = 0.0
    backup_mtime: float = 0.0
    source_size: int = 0
    backup_size: int = 0
    details: str = ""

    def to_dict(self) -> Dict:
        return {
            "relative_path": self.relative_path,
            "status": self.status,
            "source_checksum": self.source_checksum,
            "backup_checksum": self.backup_checksum,
            "source_mtime": self.source_mtime,
            "backup_mtime": self.backup_mtime,
            "source_size": self.source_size,
            "backup_size": self.backup_size,
            "details": self.details,
        }


@dataclass
class CompareResult:
    source_scan: ScanResult = field(default_factory=ScanResult)
    backup_scan: ScanResult = field(default_factory=ScanResult)
    diffs: List[FileDiff] = field(default_factory=list)
    compared_at: str = ""

    def __post_init__(self):
        if not self.compared_at:
            self.compared_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "compared_at": self.compared_at,
            "source_scan": self.source_scan.to_dict(),
            "backup_scan": self.backup_scan.to_dict(),
            "diffs": [d.to_dict() for d in self.diffs],
        }

    def get_by_status(self, status: str) -> List[FileDiff]:
        return [d for d in self.diffs if d.status == status]

    def has_issues(self) -> bool:
        return any(d.status != FILE_STATUS_OK for d in self.diffs)

    def summary(self) -> Dict[str, int]:
        counts = {
            FILE_STATUS_OK: 0,
            FILE_STATUS_MISSING: 0,
            FILE_STATUS_EXPIRED: 0,
            FILE_STATUS_CORRUPT: 0,
            FILE_STATUS_UNREGISTERED: 0,
        }
        for d in self.diffs:
            counts[d.status] = counts.get(d.status, 0) + 1
        return counts


def _is_in_target(path: str, targets: List[str]) -> bool:
    norm_path = path.replace("\\", "/")
    for target in targets:
        norm_target = target.rstrip("/").replace("\\", "/")
        if norm_path == norm_target or norm_path.startswith(norm_target + "/"):
            return True
    return False


def _is_expired(file_mtime: float, retention_days: int, now: Optional[float] = None) -> bool:
    if now is None:
        now = time.time()
    cutoff = now - (retention_days * 86400)
    return file_mtime < cutoff


def compare_scans(
    source_scan: ScanResult,
    backup_scan: ScanResult,
    config: ManifestConfig,
) -> CompareResult:
    result = CompareResult(
        source_scan=source_scan,
        backup_scan=backup_scan,
    )

    target_paths = [t.path for t in config.targets]

    source_map = source_scan.get_file_map()
    backup_map = backup_scan.get_file_map()

    all_paths = set(source_map.keys()) | set(backup_map.keys())

    for rel_path in sorted(all_paths):
        source_file = source_map.get(rel_path)
        backup_file = backup_map.get(rel_path)

        diff = FileDiff(relative_path=rel_path, status="")

        if source_file and not backup_file:
            diff.status = FILE_STATUS_MISSING
            diff.source_checksum = source_file.checksum
            diff.source_mtime = source_file.mtime
            diff.source_size = source_file.size
            diff.details = "File exists in source but missing in backup"

        elif backup_file and not source_file:
            if _is_in_target(rel_path, target_paths):
                if _is_expired(backup_file.mtime, config.retention_days):
                    diff.status = FILE_STATUS_EXPIRED
                    diff.backup_checksum = backup_file.checksum
                    diff.backup_mtime = backup_file.mtime
                    diff.backup_size = backup_file.size
                    diff.details = (
                        f"File in backup but not in source, "
                        f"older than {config.retention_days} days"
                    )
                else:
                    diff.status = FILE_STATUS_OK
                    diff.backup_checksum = backup_file.checksum
                    diff.backup_mtime = backup_file.mtime
                    diff.backup_size = backup_file.size
                    diff.details = (
                        f"File in backup but not in source, "
                        f"within retention period ({config.retention_days} days)"
                    )
            else:
                diff.status = FILE_STATUS_UNREGISTERED
                diff.backup_checksum = backup_file.checksum
                diff.backup_mtime = backup_file.mtime
                diff.backup_size = backup_file.size
                diff.details = "File in backup but not covered by any target"

        elif source_file and backup_file:
            diff.source_checksum = source_file.checksum
            diff.backup_checksum = backup_file.checksum
            diff.source_mtime = source_file.mtime
            diff.backup_mtime = backup_file.mtime
            diff.source_size = source_file.size
            diff.backup_size = backup_file.size

            if source_file.checksum != backup_file.checksum:
                diff.status = FILE_STATUS_CORRUPT
                diff.details = "Checksum mismatch between source and backup"
            else:
                diff.status = FILE_STATUS_OK
                diff.details = "Source and backup match"

        result.diffs.append(diff)

    return result
