import os
import hashlib
import fnmatch
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .config import ManifestConfig


@dataclass
class FileInfo:
    relative_path: str = ""
    absolute_path: str = ""
    size: int = 0
    mtime: float = 0.0
    checksum: str = ""
    is_registered: bool = False

    def to_dict(self) -> Dict:
        return {
            "relative_path": self.relative_path,
            "absolute_path": self.absolute_path,
            "size": self.size,
            "mtime": self.mtime,
            "checksum": self.checksum,
        }


@dataclass
class ScanResult:
    directory: str = ""
    algorithm: str = ""
    files: List[FileInfo] = field(default_factory=list)
    scanned_at: str = ""

    def __post_init__(self):
        if not self.scanned_at:
            self.scanned_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return {
            "directory": self.directory,
            "algorithm": self.algorithm,
            "scanned_at": self.scanned_at,
            "files": [f.to_dict() for f in self.files],
        }

    def get_file_map(self) -> Dict[str, FileInfo]:
        return {f.relative_path: f for f in self.files}


def _should_exclude(path: str, exclude_patterns: List[str]) -> bool:
    basename = os.path.basename(path)
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(basename, pattern):
            return True
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _calculate_checksum(file_path: str, algorithm: str) -> str:
    hash_obj = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()


def scan_directory(
    directory: str,
    algorithm: str = "sha256",
    exclude_patterns: Optional[List[str]] = None,
    sub_paths: Optional[List[str]] = None,
) -> ScanResult:
    if exclude_patterns is None:
        exclude_patterns = []

    result = ScanResult(
        directory=os.path.abspath(directory),
        algorithm=algorithm,
    )

    if not os.path.exists(directory):
        return result

    scan_roots = []
    if sub_paths:
        for sub_path in sub_paths:
            full_path = os.path.join(directory, sub_path)
            if os.path.exists(full_path):
                scan_roots.append(full_path)
    else:
        scan_roots.append(directory)

    for root_dir in scan_roots:
        if os.path.isfile(root_dir):
            rel_path = os.path.relpath(root_dir, directory)
            _add_file_to_result(result, root_dir, rel_path, algorithm, exclude_patterns)
        else:
            for root, dirs, files in os.walk(root_dir):
                dirs[:] = [
                    d for d in dirs
                    if not _should_exclude(os.path.relpath(os.path.join(root, d), directory), exclude_patterns)
                ]

                for filename in files:
                    abs_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(abs_path, directory)

                    if _should_exclude(rel_path, exclude_patterns):
                        continue

                    _add_file_to_result(result, abs_path, rel_path, algorithm, exclude_patterns)

    return result


def _add_file_to_result(
    result: ScanResult,
    abs_path: str,
    rel_path: str,
    algorithm: str,
    exclude_patterns: List[str],
) -> None:
    try:
        stat = os.stat(abs_path)
        checksum = _calculate_checksum(abs_path, algorithm)

        file_info = FileInfo(
            relative_path=rel_path.replace(os.sep, "/"),
            absolute_path=abs_path,
            size=stat.st_size,
            mtime=stat.st_mtime,
            checksum=checksum,
        )
        result.files.append(file_info)
    except (OSError, PermissionError):
        pass


def scan_source(config: ManifestConfig) -> ScanResult:
    sub_paths = [t.path.rstrip("/") for t in config.targets]
    return scan_directory(
        config.source_dir,
        algorithm=config.hash_algorithm,
        exclude_patterns=config.exclude_patterns,
        sub_paths=sub_paths,
    )


def scan_backup(config: ManifestConfig) -> ScanResult:
    return scan_directory(
        config.backup_dir,
        algorithm=config.hash_algorithm,
        exclude_patterns=config.exclude_patterns,
        sub_paths=None,
    )
