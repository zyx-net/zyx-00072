import os
import json
import re
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from datetime import datetime

from .comparator import CompareResult, FileDiff
from .config import ManifestConfig
from .constants import HISTORY_DIRNAME, EXIT_HISTORY_ERROR, FILE_STATUS_OK


class HistoryError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_HISTORY_ERROR):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


@dataclass
class HistoryDiff:
    path: str
    previous_status: str
    current_status: str
    change_type: str

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "previous_status": self.previous_status,
            "current_status": self.current_status,
            "change_type": self.change_type,
        }


@dataclass
class HistoryComparison:
    previous_timestamp: str
    current_timestamp: str
    previous_summary: Dict[str, int]
    current_summary: Dict[str, int]
    changes: List[HistoryDiff] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "previous_timestamp": self.previous_timestamp,
            "current_timestamp": self.current_timestamp,
            "previous_summary": self.previous_summary,
            "current_summary": self.current_summary,
            "changes": [c.to_dict() for c in self.changes],
        }


def _get_history_dir(config: ManifestConfig) -> str:
    base_dir = os.path.dirname(os.path.abspath(config.config_path))
    return os.path.join(base_dir, HISTORY_DIRNAME)


def _sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-\.]", "_", name)
    return name.strip("._")


def _get_history_filename(timestamp: Optional[str] = None) -> str:
    if timestamp is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"scan_{timestamp}.json"


def save_history(
    config: ManifestConfig,
    compare_result: CompareResult,
) -> str:
    history_dir = _get_history_dir(config)
    os.makedirs(history_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = _get_history_filename(timestamp)
    file_path = os.path.join(history_dir, filename)

    data = {
        "version": "1.0",
        "timestamp": timestamp,
        "manifest_name": config.name,
        "source_dir": config.source_dir,
        "backup_dir": config.backup_dir,
        "hash_algorithm": config.hash_algorithm,
        "retention_days": config.retention_days,
        "targets": [t.path for t in config.targets],
        "compare_result": compare_result.to_dict(),
        "summary": compare_result.summary(),
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise HistoryError(f"Failed to save history: {e}")

    return file_path


def list_history(config: ManifestConfig) -> List[str]:
    history_dir = _get_history_dir(config)
    if not os.path.exists(history_dir):
        return []

    files = []
    for filename in os.listdir(history_dir):
        if filename.startswith("scan_") and filename.endswith(".json"):
            files.append(filename)

    return sorted(files)


def get_latest_history_path(config: ManifestConfig) -> Optional[str]:
    history_dir = _get_history_dir(config)
    files = list_history(config)
    if not files:
        return None
    return os.path.join(history_dir, files[-1])


def load_history(file_path: str) -> Dict:
    if not os.path.exists(file_path):
        raise HistoryError(f"History file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise HistoryError(f"Invalid history file format: {e}")
    except OSError as e:
        raise HistoryError(f"Failed to read history: {e}")


def get_previous_history(config: ManifestConfig, skip_latest: bool = False) -> Optional[Dict]:
    files = list_history(config)
    if len(files) < 2 and skip_latest:
        return None
    if len(files) < 1:
        return None

    history_dir = _get_history_dir(config)
    idx = -2 if skip_latest and len(files) >= 2 else -1
    file_path = os.path.join(history_dir, files[idx])
    return load_history(file_path)


def compare_with_history(
    current_result: CompareResult,
    previous_history: Dict,
) -> HistoryComparison:
    previous_diffs = previous_history.get("compare_result", {}).get("diffs", [])
    previous_map = {d["relative_path"]: d["status"] for d in previous_diffs}
    previous_summary = previous_history.get("summary", {})

    current_map = {d.relative_path: d.status for d in current_result.diffs}
    current_summary = current_result.summary()

    all_paths = set(previous_map.keys()) | set(current_map.keys())

    changes = []
    for path in sorted(all_paths):
        prev_status = previous_map.get(path, "unknown")
        curr_status = current_map.get(path, "unknown")

        if prev_status != curr_status:
            if prev_status == "unknown":
                change_type = "new"
            elif curr_status == "unknown":
                change_type = "removed"
            elif prev_status == FILE_STATUS_OK and curr_status != FILE_STATUS_OK:
                change_type = "regressed"
            elif prev_status != FILE_STATUS_OK and curr_status == FILE_STATUS_OK:
                change_type = "fixed"
            else:
                change_type = "changed"

            changes.append(HistoryDiff(
                path=path,
                previous_status=prev_status,
                current_status=curr_status,
                change_type=change_type,
            ))

    return HistoryComparison(
        previous_timestamp=previous_history.get("timestamp", "unknown"),
        current_timestamp=current_result.compared_at,
        previous_summary=previous_summary,
        current_summary=current_summary,
        changes=changes,
    )
