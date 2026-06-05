import os
import json
import csv
from typing import List, Dict, Optional
from datetime import datetime

from .comparator import CompareResult, FileDiff
from .history import HistoryComparison
from .config import ManifestConfig
from .constants import (
    FILE_STATUS_MISSING,
    FILE_STATUS_EXPIRED,
    FILE_STATUS_CORRUPT,
    FILE_STATUS_UNREGISTERED,
    FILE_STATUS_OK,
    EXIT_REPORT_ERROR,
)


STATUS_LABELS_UNICODE = {
    FILE_STATUS_OK: ("OK", "✓", "green"),
    FILE_STATUS_MISSING: ("MISSING", "✗", "red"),
    FILE_STATUS_EXPIRED: ("EXPIRED", "!", "yellow"),
    FILE_STATUS_CORRUPT: ("CORRUPT", "✗", "red"),
    FILE_STATUS_UNREGISTERED: ("UNREGISTERED", "?", "cyan"),
}

STATUS_LABELS_ASCII = {
    FILE_STATUS_OK: ("OK", "[OK]", "green"),
    FILE_STATUS_MISSING: ("MISSING", "[MISS]", "red"),
    FILE_STATUS_EXPIRED: ("EXPIRED", "[EXP]", "yellow"),
    FILE_STATUS_CORRUPT: ("CORRUPT", "[CORR]", "red"),
    FILE_STATUS_UNREGISTERED: ("UNREGISTERED", "[UNREG]", "cyan"),
}


def get_status_labels(use_unicode: bool = True):
    return STATUS_LABELS_UNICODE if use_unicode else STATUS_LABELS_ASCII

STATUS_ORDER = [
    FILE_STATUS_MISSING,
    FILE_STATUS_CORRUPT,
    FILE_STATUS_EXPIRED,
    FILE_STATUS_UNREGISTERED,
    FILE_STATUS_OK,
]


class ReportError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_REPORT_ERROR):
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.2f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def format_timestamp(iso_str: str) -> str:
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError):
        return iso_str


def format_mtime(mtime: float) -> str:
    try:
        dt = datetime.fromtimestamp(mtime)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return "unknown"


def _color(text: str, color: str) -> str:
    colors = {
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "cyan": "\033[36m",
        "reset": "\033[0m",
    }
    return f"{colors.get(color, '')}{text}{colors['reset']}"


def generate_text_report(
    compare_result: CompareResult,
    config: Optional[ManifestConfig] = None,
    show_details: bool = True,
    use_color: bool = True,
    use_unicode: bool = True,
) -> str:
    lines = []
    status_labels = get_status_labels(use_unicode)

    lines.append("=" * 70)
    lines.append("BACKUP VERIFICATION REPORT")
    lines.append("=" * 70)

    if config:
        lines.append(f"Manifest:       {config.name}")
        lines.append(f"Source Dir:     {config.source_dir}")
        lines.append(f"Backup Dir:     {config.backup_dir}")
        lines.append(f"Hash Algorithm: {config.hash_algorithm}")
        lines.append(f"Retention:      {config.retention_days} days")
        lines.append("")

    lines.append(f"Scanned At:     {format_timestamp(compare_result.compared_at)}")
    lines.append(f"Source Files:   {len(compare_result.source_scan.files)}")
    lines.append(f"Backup Files:   {len(compare_result.backup_scan.files)}")
    lines.append("")

    summary = compare_result.summary()
    lines.append("-" * 70)
    lines.append("SUMMARY")
    lines.append("-" * 70)

    total_issues = 0
    for status in STATUS_ORDER:
        count = summary.get(status, 0)
        label, symbol, color = status_labels.get(status, (status.upper(), "?", "white"))
        line = f"  {symbol} {label:<15} {count:>5} files"
        if use_color and status != FILE_STATUS_OK:
            line = _color(line, color)
        lines.append(line)
        if status != FILE_STATUS_OK:
            total_issues += count

    lines.append("")
    ok_symbol = status_labels[FILE_STATUS_OK][1]
    fail_symbol = status_labels[FILE_STATUS_MISSING][1]
    if total_issues == 0:
        msg = f"  {ok_symbol} All files verified successfully!"
        lines.append(_color(msg, "green") if use_color else msg)
    else:
        msg = f"  {fail_symbol} Found {total_issues} issue(s) requiring attention"
        lines.append(_color(msg, "red") if use_color else msg)
    lines.append("")

    if show_details:
        for status in STATUS_ORDER:
            if status == FILE_STATUS_OK:
                continue
            diffs = compare_result.get_by_status(status)
            if not diffs:
                continue

            label, symbol, color = status_labels.get(status, (status.upper(), "?", "white"))
            lines.append("-" * 70)
            header = f"{symbol} {label} ({len(diffs)} files)"
            lines.append(_color(header, color) if use_color else header)
            lines.append("-" * 70)

            for diff in diffs:
                path_line = f"  {diff.relative_path}"
                lines.append(_color(path_line, color) if use_color else path_line)

                if status == FILE_STATUS_CORRUPT:
                    lines.append(f"    Source: {diff.source_checksum[:16]}... "
                                 f"({format_size(diff.source_size)}, {format_mtime(diff.source_mtime)})")
                    lines.append(f"    Backup: {diff.backup_checksum[:16]}... "
                                 f"({format_size(diff.backup_size)}, {format_mtime(diff.backup_mtime)})")
                elif status == FILE_STATUS_MISSING:
                    lines.append(f"    Source: {diff.source_checksum[:16]}... "
                                 f"({format_size(diff.source_size)}, {format_mtime(diff.source_mtime)})")
                    lines.append(f"    Backup: NOT FOUND")
                elif status == FILE_STATUS_EXPIRED:
                    lines.append(f"    Backup: {diff.backup_checksum[:16]}... "
                                 f"({format_size(diff.backup_size)}, {format_mtime(diff.backup_mtime)})")
                    lines.append(f"    Note:   {diff.details}")
                elif status == FILE_STATUS_UNREGISTERED:
                    lines.append(f"    Backup: {diff.backup_checksum[:16]}... "
                                 f"({format_size(diff.backup_size)}, {format_mtime(diff.backup_mtime)})")
                    lines.append(f"    Note:   {diff.details}")

                lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


def generate_history_text_report(
    comparison: HistoryComparison,
    use_color: bool = True,
    use_unicode: bool = True,
) -> str:
    lines = []
    status_labels = get_status_labels(use_unicode)

    lines.append("=" * 70)
    lines.append("HISTORY COMPARISON REPORT")
    lines.append("=" * 70)
    lines.append(f"Previous Scan: {format_timestamp(comparison.previous_timestamp)}")
    lines.append(f"Current Scan:  {format_timestamp(comparison.current_timestamp)}")
    lines.append("")

    lines.append("-" * 70)
    lines.append("SUMMARY COMPARISON")
    lines.append("-" * 70)

    for status in STATUS_ORDER:
        prev = comparison.previous_summary.get(status, 0)
        curr = comparison.current_summary.get(status, 0)
        label, symbol, color = status_labels.get(status, (status.upper(), "?", "white"))
        diff = curr - prev
        diff_str = f"({diff:+d})" if diff != 0 else "(no change)"
        line = f"  {symbol} {label:<15} {prev:>5} -> {curr:>5} {diff_str}"
        if use_color and diff != 0:
            if status == FILE_STATUS_OK:
                line_color = "green" if diff > 0 else "red"
            else:
                line_color = "green" if diff < 0 else "red"
            line = _color(line, line_color)
        lines.append(line)

    if comparison.changes:
        lines.append("")
        lines.append("-" * 70)
        lines.append(f"CHANGES ({len(comparison.changes)})")
        lines.append("-" * 70)

        change_labels = {
            "new": ("NEW", "+"),
            "removed": ("REMOVED", "-"),
            "regressed": ("REGRESSED", "↓"),
            "fixed": ("FIXED", "↑"),
            "changed": ("CHANGED", "~"),
        }

        for change in comparison.changes:
            label, symbol = change_labels.get(change.change_type, ("CHANGED", "~"))
            color_map = {
                "new": "cyan",
                "removed": "yellow",
                "regressed": "red",
                "fixed": "green",
                "changed": "blue",
            }
            color = color_map.get(change.change_type, "white")
            line = f"  {symbol} {label:<12} {change.path}"
            sub_line = f"           {change.previous_status} -> {change.current_status}"
            if use_color:
                lines.append(_color(line, color))
                lines.append(_color(sub_line, color))
            else:
                lines.append(line)
                lines.append(sub_line)
    else:
        lines.append("")
        lines.append("  No changes between scans.")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def export_json_report(
    compare_result: CompareResult,
    output_path: str,
    config: Optional[ManifestConfig] = None,
) -> None:
    data = {
        "report_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "type": "verification",
    }

    if config:
        data["config"] = {
            "name": config.name,
            "source_dir": config.source_dir,
            "backup_dir": config.backup_dir,
            "hash_algorithm": config.hash_algorithm,
            "retention_days": config.retention_days,
            "targets": [t.path for t in config.targets],
        }

    data["result"] = compare_result.to_dict()
    data["summary"] = compare_result.summary()

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        raise ReportError(f"Failed to export JSON report: {e}")


def export_csv_report(
    compare_result: CompareResult,
    output_path: str,
) -> None:
    try:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Path",
                "Status",
                "Source Checksum",
                "Backup Checksum",
                "Source Size",
                "Backup Size",
                "Source Modified",
                "Backup Modified",
                "Details",
            ])

            for diff in compare_result.diffs:
                writer.writerow([
                    diff.relative_path,
                    diff.status,
                    diff.source_checksum,
                    diff.backup_checksum,
                    diff.source_size,
                    diff.backup_size,
                    format_mtime(diff.source_mtime) if diff.source_mtime else "",
                    format_mtime(diff.backup_mtime) if diff.backup_mtime else "",
                    diff.details,
                ])
    except OSError as e:
        raise ReportError(f"Failed to export CSV report: {e}")


def print_console_report(
    compare_result: CompareResult,
    config: Optional[ManifestConfig] = None,
    show_details: bool = True,
) -> None:
    import sys
    use_color = sys.stdout.isatty()
    use_unicode = sys.stdout.isatty()
    report = generate_text_report(compare_result, config, show_details, use_color, use_unicode)
    print(report)


def print_history_comparison(comparison: HistoryComparison) -> None:
    import sys
    use_color = sys.stdout.isatty()
    use_unicode = sys.stdout.isatty()
    report = generate_history_text_report(comparison, use_color, use_unicode)
    print(report)
