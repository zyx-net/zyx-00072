import os
import json
import fnmatch
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .config import (
    ManifestConfig,
    ConfigError,
    DuplicateTargetError,
    _validate_config,
    load_config,
)
from .constants import (
    EXIT_DOCTOR_ERROR,
    EXIT_DOCTOR_WARNING,
    EXIT_DOCTOR_PERMISSION,
    EXIT_DOCTOR_UNKNOWN_ALGORITHM,
    EXIT_DOCTOR_DUPLICATE_TARGET,
    EXIT_CONFIG_ERROR,
    EXIT_SUCCESS,
    SUPPORTED_HASH_ALGORITHMS,
    HISTORY_DIRNAME,
    PROFILE_LOG_DIRNAME,
)
from .history import _get_history_dir
from .profile import _log_operation


CHECK_OK = "ok"
CHECK_WARN = "warn"
CHECK_ERROR = "error"


class DoctorError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_DOCTOR_ERROR):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


@dataclass
class CheckItem:
    name: str
    status: str
    message: str
    details: Optional[Dict] = None
    fixable: bool = False

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details or {},
            "fixable": self.fixable,
        }


@dataclass
class DoctorResult:
    timestamp: str
    config_path: str
    checks: List[CheckItem] = field(default_factory=list)
    fixes_applied: List[str] = field(default_factory=list)

    def by_status(self) -> Dict[str, List[CheckItem]]:
        grouped = {CHECK_OK: [], CHECK_WARN: [], CHECK_ERROR: []}
        for check in self.checks:
            grouped[check.status].append(check)
        return grouped

    def summary(self) -> Dict[str, int]:
        grouped = self.by_status()
        return {
            CHECK_OK: len(grouped[CHECK_OK]),
            CHECK_WARN: len(grouped[CHECK_WARN]),
            CHECK_ERROR: len(grouped[CHECK_ERROR]),
        }

    def exit_code(self) -> int:
        counts = self.summary()
        if counts[CHECK_ERROR] > 0:
            return EXIT_DOCTOR_ERROR
        if counts[CHECK_WARN] > 0:
            return EXIT_DOCTOR_WARNING
        return EXIT_SUCCESS

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "config_path": os.path.abspath(self.config_path),
            "summary": self.summary(),
            "checks": [c.to_dict() for c in self.checks],
            "fixes_applied": self.fixes_applied,
        }


def _get_profile_log_dir(config: ManifestConfig) -> str:
    base_dir = os.path.dirname(os.path.abspath(config.config_path))
    return os.path.join(base_dir, PROFILE_LOG_DIRNAME)


def _check_path_exists(path: str, name: str, fixable: bool = False) -> CheckItem:
    if not path:
        return CheckItem(
            name=name,
            status=CHECK_ERROR,
            message=f"{name} path is empty",
            fixable=False,
        )
    abs_path = os.path.abspath(path)
    if os.path.exists(abs_path):
        return CheckItem(
            name=name,
            status=CHECK_OK,
            message=f"{name} exists: {abs_path}",
            details={"path": abs_path},
            fixable=False,
        )
    return CheckItem(
        name=name,
        status=CHECK_ERROR,
        message=f"{name} does not exist: {abs_path}",
        details={"path": abs_path},
        fixable=fixable,
    )


def _check_path_readable(path: str, name: str) -> CheckItem:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return CheckItem(
            name=f"{name}.readable",
            status=CHECK_ERROR,
            message=f"Cannot check readability: {name} does not exist",
            details={"path": abs_path},
            fixable=False,
        )
    try:
        with open(abs_path, "rb") if os.path.isfile(abs_path) else os.scandir(abs_path) as _:
            pass
        return CheckItem(
            name=f"{name}.readable",
            status=CHECK_OK,
            message=f"{name} is readable",
            details={"path": abs_path},
            fixable=False,
        )
    except PermissionError:
        return CheckItem(
            name=f"{name}.readable",
            status=CHECK_ERROR,
            message=f"Permission denied: cannot read {name} at {abs_path}",
            details={"path": abs_path},
            fixable=False,
        )
    except OSError as e:
        return CheckItem(
            name=f"{name}.readable",
            status=CHECK_ERROR,
            message=f"Cannot read {name}: {e}",
            details={"path": abs_path, "error": str(e)},
            fixable=False,
        )


def _check_path_writable(path: str, name: str) -> CheckItem:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return CheckItem(
            name=f"{name}.writable",
            status=CHECK_ERROR,
            message=f"Cannot check writability: {name} does not exist",
            details={"path": abs_path},
            fixable=False,
        )
    try:
        if os.path.isdir(abs_path):
            test_file = os.path.join(abs_path, f".doctor_write_test_{os.getpid()}")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        else:
            test_dir = os.path.dirname(abs_path)
            test_file = os.path.join(test_dir, f".doctor_write_test_{os.getpid()}")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
        return CheckItem(
            name=f"{name}.writable",
            status=CHECK_OK,
            message=f"{name} is writable",
            details={"path": abs_path},
            fixable=False,
        )
    except PermissionError:
        return CheckItem(
            name=f"{name}.writable",
            status=CHECK_ERROR,
            message=f"Permission denied: cannot write to {name} at {abs_path}",
            details={"path": abs_path},
            fixable=False,
        )
    except OSError as e:
        return CheckItem(
            name=f"{name}.writable",
            status=CHECK_ERROR,
            message=f"Cannot write to {name}: {e}",
            details={"path": abs_path, "error": str(e)},
            fixable=False,
        )


def _check_hash_algorithm(algo: str) -> CheckItem:
    if algo in SUPPORTED_HASH_ALGORITHMS:
        return CheckItem(
            name="hash_algorithm",
            status=CHECK_OK,
            message=f"Hash algorithm '{algo}' is supported",
            details={"algorithm": algo, "supported": sorted(SUPPORTED_HASH_ALGORITHMS)},
            fixable=False,
        )
    return CheckItem(
        name="hash_algorithm",
        status=CHECK_ERROR,
        message=f"Unsupported hash algorithm: '{algo}'. Use one of: {', '.join(sorted(SUPPORTED_HASH_ALGORITHMS))}",
        details={"algorithm": algo, "supported": sorted(SUPPORTED_HASH_ALGORITHMS)},
        fixable=False,
    )


def _check_targets(targets: List, source_dir: str, backup_dir: str) -> List[CheckItem]:
    checks = []
    base_dir = os.path.commonpath([
        os.path.abspath(source_dir),
        os.path.abspath(backup_dir),
    ])

    seen_paths = set()
    duplicates = []

    for i, target in enumerate(targets):
        target_path = target.path if hasattr(target, "path") else target.get("path", "")

        if not target_path:
            checks.append(CheckItem(
                name=f"targets[{i}]",
                status=CHECK_ERROR,
                message=f"Target {i} has empty path",
                details={"index": i},
                fixable=False,
            ))
            continue

        if target_path in seen_paths:
            duplicates.append(target_path)
            checks.append(CheckItem(
                name=f"targets[{i}]",
                status=CHECK_ERROR,
                message=f"Duplicate target path: '{target_path}'",
                details={"index": i, "path": target_path, "duplicate": True},
                fixable=False,
            ))
        else:
            seen_paths.add(target_path)

        full_source_path = os.path.normpath(os.path.join(source_dir, target_path))
        full_backup_path = os.path.normpath(os.path.join(backup_dir, target_path))

        abs_source = os.path.abspath(full_source_path)
        abs_backup = os.path.abspath(full_backup_path)

        if ".." in os.path.normpath(target_path).split(os.sep):
            checks.append(CheckItem(
                name=f"targets[{i}].path_traversal",
                status=CHECK_ERROR,
                message=f"Target path '{target_path}' contains path traversal (..), which is not allowed",
                details={"index": i, "path": target_path},
                fixable=False,
            ))

        try:
            rel_to_source_base = os.path.relpath(abs_source, os.path.abspath(source_dir))
            if rel_to_source_base.startswith(".."):
                checks.append(CheckItem(
                    name=f"targets[{i}].out_of_bounds",
                    status=CHECK_ERROR,
                    message=f"Target '{target_path}' resolves outside source_dir: {abs_source}",
                    details={"index": i, "path": target_path, "resolved": abs_source, "boundary": source_dir},
                    fixable=False,
                ))
        except ValueError:
            pass

        try:
            rel_to_backup_base = os.path.relpath(abs_backup, os.path.abspath(backup_dir))
            if rel_to_backup_base.startswith(".."):
                checks.append(CheckItem(
                    name=f"targets[{i}].out_of_bounds",
                    status=CHECK_ERROR,
                    message=f"Target '{target_path}' resolves outside backup_dir: {abs_backup}",
                    details={"index": i, "path": target_path, "resolved": abs_backup, "boundary": backup_dir},
                    fixable=False,
                ))
        except ValueError:
            pass

        if os.path.abspath(full_source_path) == os.path.abspath(source_dir):
            checks.append(CheckItem(
                name=f"targets[{i}].overlap_source",
                status=CHECK_WARN,
                message=f"Target '{target_path}' matches entire source_dir, this will scan everything",
                details={"index": i, "path": target_path},
                fixable=False,
            ))

        if os.path.abspath(full_backup_path) == os.path.abspath(backup_dir):
            checks.append(CheckItem(
                name=f"targets[{i}].overlap_backup",
                status=CHECK_WARN,
                message=f"Target '{target_path}' matches entire backup_dir, this will scan everything",
                details={"index": i, "path": target_path},
                fixable=False,
            ))

    if duplicates:
        checks.append(CheckItem(
            name="targets.duplicates",
            status=CHECK_ERROR,
            message=f"Found {len(duplicates)} duplicate target path(s): {', '.join(duplicates)}",
            details={"duplicates": duplicates},
            fixable=False,
        ))

    if not checks:
        checks.append(CheckItem(
            name="targets",
            status=CHECK_OK,
            message=f"All {len(targets)} target(s) are valid",
            details={"count": len(targets), "paths": [t.path for t in targets]},
            fixable=False,
        ))

    return checks


def _check_exclude_patterns(patterns: List[str]) -> CheckItem:
    if not patterns:
        return CheckItem(
            name="exclude_patterns",
            status=CHECK_OK,
            message="No exclude patterns configured",
            details={"count": 0},
            fixable=False,
        )

    invalid = []
    for i, pattern in enumerate(patterns):
        try:
            fnmatch.fnmatch("test", pattern)
        except Exception:
            invalid.append({"index": i, "pattern": pattern})

    if invalid:
        return CheckItem(
            name="exclude_patterns",
            status=CHECK_WARN,
            message=f"Found {len(invalid)} potentially invalid exclude pattern(s)",
            details={"count": len(patterns), "invalid": invalid},
            fixable=False,
        )

    return CheckItem(
        name="exclude_patterns",
        status=CHECK_OK,
        message=f"All {len(patterns)} exclude pattern(s) are valid",
        details={"count": len(patterns), "patterns": patterns},
        fixable=False,
    )


def _check_history_dir(config: ManifestConfig) -> CheckItem:
    history_dir = _get_history_dir(config)
    if os.path.exists(history_dir):
        return CheckItem(
            name="history_dir",
            status=CHECK_OK,
            message=f"History directory exists: {history_dir}",
            details={"path": history_dir},
            fixable=False,
        )
    return CheckItem(
        name="history_dir",
        status=CHECK_WARN,
        message=f"History directory does not exist: {history_dir}",
        details={"path": history_dir},
        fixable=True,
    )


def _check_profile_log_dir(config: ManifestConfig) -> CheckItem:
    profile_dir = _get_profile_log_dir(config)
    if os.path.exists(profile_dir):
        return CheckItem(
            name="profile_log_dir",
            status=CHECK_OK,
            message=f"Profile log directory exists: {profile_dir}",
            details={"path": profile_dir},
            fixable=False,
        )
    return CheckItem(
        name="profile_log_dir",
        status=CHECK_WARN,
        message=f"Profile log directory does not exist: {profile_dir}",
        details={"path": profile_dir},
        fixable=True,
    )


def _check_source_backup_overlap(source_dir: str, backup_dir: str) -> CheckItem:
    abs_source = os.path.abspath(source_dir)
    abs_backup = os.path.abspath(backup_dir)

    if abs_source == abs_backup:
        return CheckItem(
            name="source_backup_overlap",
            status=CHECK_ERROR,
            message=f"source_dir and backup_dir are the same: {abs_source}",
            details={"source_dir": abs_source, "backup_dir": abs_backup},
            fixable=False,
        )

    try:
        rel = os.path.relpath(abs_backup, abs_source)
        if not rel.startswith(".."):
            return CheckItem(
                name="source_backup_overlap",
                status=CHECK_ERROR,
                message=f"backup_dir is inside source_dir: {abs_backup} is within {abs_source}",
                details={"source_dir": abs_source, "backup_dir": abs_backup},
                fixable=False,
            )
    except ValueError:
        pass

    try:
        rel = os.path.relpath(abs_source, abs_backup)
        if not rel.startswith(".."):
            return CheckItem(
                name="source_backup_overlap",
                status=CHECK_ERROR,
                message=f"source_dir is inside backup_dir: {abs_source} is within {abs_backup}",
                details={"source_dir": abs_source, "backup_dir": abs_backup},
                fixable=False,
            )
    except ValueError:
        pass

    return CheckItem(
        name="source_backup_overlap",
        status=CHECK_OK,
        message="source_dir and backup_dir are properly separated",
        details={"source_dir": abs_source, "backup_dir": abs_backup},
        fixable=False,
    )


def run_doctor(
    config_path: str,
    apply_fixes: bool = False,
) -> DoctorResult:
    timestamp = datetime.now().isoformat()
    result = DoctorResult(
        timestamp=timestamp,
        config_path=config_path,
    )

    try:
        config = load_config(config_path)
    except DuplicateTargetError as e:
        result.checks.append(CheckItem(
            name="config.load",
            status=CHECK_ERROR,
            message=f"Duplicate target paths found: {', '.join(e.duplicates)}",
            details={"duplicates": e.duplicates},
            fixable=False,
        ))
        _log_doctor_operation(config_path, result, "error", f"Failed: duplicate targets")
        raise DoctorError(
            f"Duplicate target paths found: {', '.join(e.duplicates)}",
            EXIT_DOCTOR_DUPLICATE_TARGET,
        ) from e
    except ConfigError as e:
        if "hash algorithm" in str(e).lower() or "Unsupported" in str(e):
            result.checks.append(CheckItem(
                name="config.hash_algorithm",
                status=CHECK_ERROR,
                message=str(e.message),
                fixable=False,
            ))
            _log_doctor_operation(config_path, result, "error", f"Failed: unknown hash algorithm")
            raise DoctorError(str(e.message), EXIT_DOCTOR_UNKNOWN_ALGORITHM) from e
        if "YAML" in str(e) or "Invalid YAML" in str(e):
            result.checks.append(CheckItem(
                name="config.yaml",
                status=CHECK_ERROR,
                message=str(e.message),
                fixable=False,
            ))
            _log_doctor_operation(config_path, result, "error", f"Failed: bad YAML format")
            raise DoctorError(str(e.message), EXIT_CONFIG_ERROR) from e
        result.checks.append(CheckItem(
            name="config.load",
            status=CHECK_ERROR,
            message=str(e.message),
            fixable=False,
        ))
        _log_doctor_operation(config_path, result, "error", f"Failed: config error")
        raise DoctorError(str(e.message), EXIT_CONFIG_ERROR) from e

    base_dir = os.path.dirname(os.path.abspath(config_path))

    result.checks.append(CheckItem(
        name="config.load",
        status=CHECK_OK,
        message=f"Config loaded successfully from {config_path}",
        details={"config_path": os.path.abspath(config_path), "manifest_name": config.name},
        fixable=False,
    ))

    result.checks.append(_check_path_exists(config.source_dir, "source_dir"))
    if os.path.exists(config.source_dir):
        result.checks.append(_check_path_readable(config.source_dir, "source_dir"))

    result.checks.append(_check_path_exists(config.backup_dir, "backup_dir"))
    if os.path.exists(config.backup_dir):
        result.checks.append(_check_path_readable(config.backup_dir, "backup_dir"))
        result.checks.append(_check_path_writable(config.backup_dir, "backup_dir"))

    result.checks.append(_check_source_backup_overlap(config.source_dir, config.backup_dir))

    result.checks.append(_check_hash_algorithm(config.hash_algorithm))

    target_checks = _check_targets(config.targets, config.source_dir, config.backup_dir)
    result.checks.extend(target_checks)

    result.checks.append(_check_exclude_patterns(config.exclude_patterns))

    history_check = _check_history_dir(config)
    result.checks.append(history_check)
    if history_check.status == CHECK_WARN and history_check.fixable and apply_fixes:
        try:
            os.makedirs(_get_history_dir(config), exist_ok=True)
            result.fixes_applied.append(f"Created history directory: {_get_history_dir(config)}")
            history_check.status = CHECK_OK
            history_check.message = f"History directory created: {_get_history_dir(config)}"
        except PermissionError as e:
            history_check.status = CHECK_ERROR
            history_check.message = f"Permission denied: cannot create history directory: {e}"
            result.checks.append(CheckItem(
                name="history_dir.create",
                status=CHECK_ERROR,
                message=f"Permission denied: cannot create history directory",
                details={"error": str(e)},
                fixable=False,
            ))
            _log_doctor_operation(config_path, result, "error", f"Failed: permission denied creating history dir")
            raise DoctorError(
                f"Permission denied: cannot create history directory: {e}",
                EXIT_DOCTOR_PERMISSION,
            ) from e

    profile_check = _check_profile_log_dir(config)
    result.checks.append(profile_check)
    if profile_check.status == CHECK_WARN and profile_check.fixable and apply_fixes:
        try:
            os.makedirs(_get_profile_log_dir(config), exist_ok=True)
            result.fixes_applied.append(f"Created profile log directory: {_get_profile_log_dir(config)}")
            profile_check.status = CHECK_OK
            profile_check.message = f"Profile log directory created: {_get_profile_log_dir(config)}"
        except PermissionError as e:
            profile_check.status = CHECK_ERROR
            profile_check.message = f"Permission denied: cannot create profile log directory: {e}"
            result.checks.append(CheckItem(
                name="profile_log_dir.create",
                status=CHECK_ERROR,
                message=f"Permission denied: cannot create profile log directory",
                details={"error": str(e)},
                fixable=False,
            ))
            _log_doctor_operation(config_path, result, "error", f"Failed: permission denied creating profile dir")
            raise DoctorError(
                f"Permission denied: cannot create profile log directory: {e}",
                EXIT_DOCTOR_PERMISSION,
            ) from e

    if history_check.status == CHECK_OK and os.path.exists(_get_history_dir(config)):
        result.checks.append(_check_path_readable(_get_history_dir(config), "history_dir"))
        result.checks.append(_check_path_writable(_get_history_dir(config), "history_dir"))

    if profile_check.status == CHECK_OK and os.path.exists(_get_profile_log_dir(config)):
        result.checks.append(_check_path_readable(_get_profile_log_dir(config), "profile_log_dir"))
        result.checks.append(_check_path_writable(_get_profile_log_dir(config), "profile_log_dir"))

    status = "success"
    summary = result.summary()
    if summary[CHECK_ERROR] > 0:
        status = "error"
    elif summary[CHECK_WARN] > 0:
        status = "warning"

    details = f"Doctor check complete: {summary[CHECK_OK]} OK, {summary[CHECK_WARN]} warnings, {summary[CHECK_ERROR]} errors"
    if result.fixes_applied:
        details += f". Fixes applied: {len(result.fixes_applied)}"

    _log_doctor_operation(config_path, result, status, details)

    return result


def _log_doctor_operation(
    config_path: str,
    result: DoctorResult,
    status: str,
    details: str,
) -> None:
    summary = result.summary()
    full_details = (
        f"{details}. "
        f"OK={summary[CHECK_OK]}, WARN={summary[CHECK_WARN]}, ERR={summary[CHECK_ERROR]}. "
        f"Fixes applied: {len(result.fixes_applied)}"
    )
    _log_operation(
        config_path,
        "doctor",
        config_path,
        None,
        status,
        full_details,
    )


def format_console_report(result: DoctorResult, use_color: bool = True) -> str:
    lines = []
    grouped = result.by_status()
    summary = result.summary()

    lines.append("=" * 70)
    lines.append("BACKUP CHECKER DOCTOR REPORT")
    lines.append("=" * 70)
    lines.append(f"Timestamp:   {result.timestamp}")
    lines.append(f"Config:      {os.path.abspath(result.config_path)}")
    lines.append(f"Summary:     {summary[CHECK_OK]} OK, {summary[CHECK_WARN]} warnings, {summary[CHECK_ERROR]} errors")
    lines.append("")

    status_symbols = {
        CHECK_OK: "[OK]" if use_color else "[OK]",
        CHECK_WARN: "[WARN]" if use_color else "[WARN]",
        CHECK_ERROR: "[ERR]" if use_color else "[ERR]",
    }

    for status in [CHECK_ERROR, CHECK_WARN, CHECK_OK]:
        checks = grouped[status]
        if not checks:
            continue

        symbol = status_symbols[status]
        lines.append(f"{symbol} {status.upper()}: {len(checks)} item(s)")
        lines.append("-" * 70)

        for check in checks:
            lines.append(f"  {symbol} {check.name}")
            lines.append(f"       {check.message}")
            if check.fixable:
                lines.append(f"       (fixable with --fix)")
        lines.append("")

    if result.fixes_applied:
        lines.append("FIXES APPLIED:")
        lines.append("-" * 70)
        for fix in result.fixes_applied:
            lines.append(f"  [FIX] {fix}")
        lines.append("")

    if summary[CHECK_ERROR] > 0:
        lines.append("RESULT: FAILED - Errors found, please fix before running scan/report/drill")
    elif summary[CHECK_WARN] > 0:
        lines.append("RESULT: PASSED WITH WARNINGS - Consider reviewing the warnings")
    else:
        lines.append("RESULT: PASSED - All checks OK, ready to run scan/report/drill")

    lines.append("=" * 70)
    return "\n".join(lines)


def format_json_report(result: DoctorResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
