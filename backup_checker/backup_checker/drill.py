import os
import shutil
import tempfile
import hashlib
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .config import ManifestConfig
from .scanner import ScanResult, FileInfo, scan_directory
from .comparator import (
    CompareResult,
    compare_scans,
    FILE_STATUS_MISSING,
    FILE_STATUS_CORRUPT,
)
from .constants import EXIT_DRILL_FAILED, EXIT_MISSING_FILE, EXIT_CHECKSUM_MISMATCH


class DrillError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_DRILL_FAILED):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


@dataclass
class DrillFileResult:
    relative_path: str
    status: str
    expected_checksum: str
    restored_checksum: str = ""
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "relative_path": self.relative_path,
            "status": self.status,
            "expected_checksum": self.expected_checksum,
            "restored_checksum": self.restored_checksum,
            "error": self.error,
        }


@dataclass
class DrillResult:
    restore_dir: str
    files: List[DrillFileResult] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self):
        if not self.started_at:
            self.started_at = datetime.now().isoformat()

    def complete(self):
        self.completed_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "restore_dir": self.restore_dir,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "files": [f.to_dict() for f in self.files],
            "summary": self.summary(),
        }

    def summary(self) -> Dict[str, int]:
        counts = {"success": 0, "failed": 0, "skipped": 0}
        for f in self.files:
            counts[f.status] = counts.get(f.status, 0) + 1
        return counts

    def all_successful(self) -> bool:
        return all(f.status == "success" for f in self.files)

    def has_failures(self) -> bool:
        return any(f.status == "failed" for f in self.files)


def _calculate_checksum(file_path: str, algorithm: str) -> str:
    hash_obj = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def run_drill(
    config: ManifestConfig,
    source_scan: ScanResult,
    compare_result: CompareResult,
    restore_dir: Optional[str] = None,
    keep_restore: bool = False,
    verify_only: Optional[List[str]] = None,
) -> DrillResult:
    missing_files = compare_result.get_by_status(FILE_STATUS_MISSING)
    if missing_files:
        paths = ", ".join(d.relative_path for d in missing_files[:3])
        if len(missing_files) > 3:
            paths += f" and {len(missing_files) - 3} more"
        raise DrillError(
            f"Drill aborted: {len(missing_files)} file(s) missing in backup: {paths}",
            EXIT_MISSING_FILE,
        )

    corrupt_files = compare_result.get_by_status(FILE_STATUS_CORRUPT)
    if corrupt_files:
        paths = ", ".join(d.relative_path for d in corrupt_files[:3])
        if len(corrupt_files) > 3:
            paths += f" and {len(corrupt_files) - 3} more"
        raise DrillError(
            f"Drill aborted: {len(corrupt_files)} file(s) have checksum mismatch: {paths}",
            EXIT_CHECKSUM_MISMATCH,
        )

    temp_dir = None
    if restore_dir is None:
        temp_dir = tempfile.mkdtemp(prefix="backup_drill_")
        restore_dir = temp_dir
    else:
        if os.path.exists(restore_dir):
            if os.listdir(restore_dir):
                raise DrillError(
                    f"Restore directory is not empty: {restore_dir}",
                    EXIT_DRILL_FAILED,
                )
        else:
            os.makedirs(restore_dir, exist_ok=True)

    result = DrillResult(restore_dir=restore_dir)

    try:
        source_map = source_scan.get_file_map()
        files_to_verify = list(source_map.keys())

        if verify_only:
            files_to_verify = [f for f in files_to_verify if f in verify_only]
            missing_requested = [f for f in verify_only if f not in source_map]
            if missing_requested:
                raise DrillError(
                    f"Requested files not found in source: {', '.join(missing_requested)}",
                    EXIT_DRILL_FAILED,
                )

        for rel_path in files_to_verify:
            source_file = source_map[rel_path]
            drill_file = DrillFileResult(
                relative_path=rel_path,
                status="failed",
                expected_checksum=source_file.checksum,
            )

            try:
                backup_path = os.path.join(config.backup_dir, rel_path)
                restore_path = os.path.join(restore_dir, rel_path)

                os.makedirs(os.path.dirname(restore_path), exist_ok=True)
                shutil.copy2(backup_path, restore_path)

                restored_checksum = _calculate_checksum(
                    restore_path, config.hash_algorithm
                )
                drill_file.restored_checksum = restored_checksum

                if restored_checksum == source_file.checksum:
                    drill_file.status = "success"
                else:
                    drill_file.error = (
                        f"Checksum mismatch after restore: "
                        f"expected {source_file.checksum[:16]}..., "
                        f"got {restored_checksum[:16]}..."
                    )

            except Exception as e:
                drill_file.error = str(e)

            result.files.append(drill_file)

    finally:
        if temp_dir and not keep_restore:
            shutil.rmtree(temp_dir, ignore_errors=True)

    result.complete()

    if result.has_failures():
        failed_files = [f.relative_path for f in result.files if f.status == "failed"]
        raise DrillError(
            f"Drill failed: {len(failed_files)} file(s) failed verification: {', '.join(failed_files)}",
            EXIT_DRILL_FAILED,
        )

    return result


def run_drill_from_history(
    config: ManifestConfig,
    history_data: Dict,
    restore_dir: Optional[str] = None,
    keep_restore: bool = False,
) -> DrillResult:
    from .scanner import ScanResult

    source_data = history_data["compare_result"]["source_scan"]
    source_files = [
        FileInfo(
            relative_path=f["relative_path"],
            absolute_path=f["absolute_path"],
            size=f["size"],
            mtime=f["mtime"],
            checksum=f["checksum"],
        )
        for f in source_data["files"]
    ]
    source_scan = ScanResult(
        directory=source_data["directory"],
        algorithm=source_data["algorithm"],
        files=source_files,
        scanned_at=source_data["scanned_at"],
    )

    diffs_data = history_data["compare_result"]["diffs"]
    backup_data = history_data["compare_result"]["backup_scan"]
    backup_files = [
        FileInfo(
            relative_path=f["relative_path"],
            absolute_path=f["absolute_path"],
            size=f["size"],
            mtime=f["mtime"],
            checksum=f["checksum"],
        )
        for f in backup_data["files"]
    ]
    backup_scan = ScanResult(
        directory=backup_data["directory"],
        algorithm=backup_data["algorithm"],
        files=backup_files,
        scanned_at=backup_data["scanned_at"],
    )

    compare_result = compare_scans(source_scan, backup_scan, config)

    return run_drill(config, source_scan, compare_result, restore_dir, keep_restore)
