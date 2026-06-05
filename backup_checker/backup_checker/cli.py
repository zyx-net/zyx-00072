import os
import sys
import json
from typing import Optional, List

import click

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from . import __version__
from .constants import (
    EXIT_SUCCESS,
    EXIT_GENERAL_ERROR,
    EXIT_CHECKSUM_MISMATCH,
    EXIT_MISSING_FILE,
    FILE_STATUS_CORRUPT,
    FILE_STATUS_MISSING,
    CONFIG_FILENAME,
)
from .config import (
    init_config,
    load_config,
    find_config,
    ConfigError,
    DuplicateTargetError,
)
from .scanner import scan_source, scan_backup
from .comparator import compare_scans
from .history import (
    save_history,
    list_history,
    load_history,
    get_previous_history,
    get_latest_history_path,
    compare_with_history,
    HistoryError,
)
from .reporter import (
    print_console_report,
    print_history_comparison,
    export_json_report,
    export_csv_report,
    ReportError,
    format_timestamp,
)
from .drill import run_drill, run_drill_from_history, DrillError
from .profile import (
    export_profile,
    import_profile,
    read_operation_logs,
    ProfileError,
    ProfileConflictError,
    ProfileInvalidJsonError,
    ProfilePermissionError,
    ProfileUnknownAlgorithmError,
    ProfileInvalidConfigError,
)


@click.group()
@click.version_option(__version__, prog_name="backup-checker")
def cli():
    """Local backup inventory verification and recovery drill CLI tool."""
    pass


@cli.command()
@click.option(
    "--output-dir", "-o",
    default=".",
    help="Directory to create the manifest config file (default: current directory)",
)
@click.option(
    "--source-dir", "-s",
    required=True,
    help="Source directory path (relative to output-dir)",
)
@click.option(
    "--backup-dir", "-b",
    required=True,
    help="Backup directory path (relative to output-dir)",
)
@click.option(
    "--name", "-n",
    default="backup-check",
    help="Name for this backup manifest (default: backup-check)",
)
def init(output_dir, source_dir, backup_dir, name):
    """Initialize a new backup manifest configuration."""
    try:
        config_path = init_config(output_dir, source_dir, backup_dir, name)
        click.echo(f"[OK] Created config file: {config_path}")
        click.echo("")
        click.echo("Edit the config file to customize targets and settings,")
        click.echo("then run 'backup-checker scan' to start verification.")
        sys.exit(EXIT_SUCCESS)
    except ConfigError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@cli.command()
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--no-save",
    is_flag=True,
    help="Do not save scan results to history",
)
@click.option(
    "--compare-history/--no-compare-history",
    default=True,
    help="Compare with previous scan (default: True)",
)
@click.option(
    "--brief",
    is_flag=True,
    help="Show only summary, no details",
)
def scan(config, no_save, compare_history, brief):
    """Scan source and backup directories, compare and save results."""
    try:
        config_path = config or find_config()
        manifest_config = load_config(config_path)

        click.echo(f"Scanning source: {manifest_config.source_dir}")
        source_result = scan_source(manifest_config)
        click.echo(f"  Found {len(source_result.files)} files in source")

        click.echo(f"Scanning backup: {manifest_config.backup_dir}")
        backup_result = scan_backup(manifest_config)
        click.echo(f"  Found {len(backup_result.files)} files in backup")
        click.echo("")

        compare_result = compare_scans(source_result, backup_result, manifest_config)

        print_console_report(compare_result, manifest_config, show_details=not brief)

        if compare_history:
            prev_history = get_previous_history(manifest_config, skip_latest=False)
            if prev_history:
                comparison = compare_with_history(compare_result, prev_history)
                print_history_comparison(comparison)

        if not no_save:
            history_path = save_history(manifest_config, compare_result)
            click.echo(f"[OK] Saved history to: {history_path}")

        summary = compare_result.summary()
        if summary.get(FILE_STATUS_MISSING, 0) > 0:
            sys.exit(EXIT_MISSING_FILE)
        if summary.get(FILE_STATUS_CORRUPT, 0) > 0:
            sys.exit(EXIT_CHECKSUM_MISMATCH)

        sys.exit(EXIT_SUCCESS)

    except DuplicateTargetError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        click.echo(f"  Duplicate paths: {', '.join(e.duplicates)}", err=True)
        sys.exit(e.exit_code)
    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except HistoryError as e:
        click.echo(f"[ERR] History error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@cli.command("report")
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--history",
    help="Use a specific history file instead of scanning now",
)
@click.option(
    "--format", "-f",
    type=click.Choice(["console", "json", "csv", "text"]),
    default="console",
    help="Output format (default: console)",
)
@click.option(
    "--output", "-o",
    help="Output file path (required for json/csv/text formats)",
)
@click.option(
    "--brief",
    is_flag=True,
    help="Show only summary, no details (console/text only)",
)
def report_cmd(config, history, format, output, brief):
    """Generate verification report in various formats."""
    try:
        config_path = config or find_config()
        manifest_config = load_config(config_path)

        if history:
            history_data = load_history(history)
            from .scanner import ScanResult, FileInfo
            from .comparator import CompareResult, FileDiff

            source_data = history_data["compare_result"]["source_scan"]
            source_files = [FileInfo(**f) for f in source_data["files"]]
            source_scan = ScanResult(
                directory=source_data["directory"],
                algorithm=source_data["algorithm"],
                files=source_files,
                scanned_at=source_data["scanned_at"],
            )

            backup_data = history_data["compare_result"]["backup_scan"]
            backup_files = [FileInfo(**f) for f in backup_data["files"]]
            backup_scan = ScanResult(
                directory=backup_data["directory"],
                algorithm=backup_data["algorithm"],
                files=backup_files,
                scanned_at=backup_data["scanned_at"],
            )

            diffs_data = history_data["compare_result"]["diffs"]
            diffs = [FileDiff(**d) for d in diffs_data]
            compare_result = CompareResult(
                source_scan=source_scan,
                backup_scan=backup_scan,
                diffs=diffs,
                compared_at=history_data["compare_result"]["compared_at"],
            )
        else:
            source_result = scan_source(manifest_config)
            backup_result = scan_backup(manifest_config)
            compare_result = compare_scans(source_result, backup_result, manifest_config)

        if format == "console":
            print_console_report(compare_result, manifest_config, show_details=not brief)
        elif format == "json":
            if not output:
                raise ReportError("--output is required for JSON format")
            export_json_report(compare_result, output, manifest_config)
            click.echo(f"[OK] JSON report exported to: {output}")
        elif format == "csv":
            if not output:
                raise ReportError("--output is required for CSV format")
            export_csv_report(compare_result, output)
            click.echo(f"[OK] CSV report exported to: {output}")
        elif format == "text":
            if not output:
                raise ReportError("--output is required for text format")
            from .reporter import generate_text_report
            report = generate_text_report(compare_result, manifest_config, show_details=not brief, use_color=False, use_unicode=False)
            with open(output, "w", encoding="utf-8") as f:
                f.write(report)
            click.echo(f"[OK] Text report exported to: {output}")

        sys.exit(EXIT_SUCCESS)

    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except ReportError as e:
        click.echo(f"[ERR] Report error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except HistoryError as e:
        click.echo(f"[ERR] History error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@cli.command()
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--history",
    help="Use a specific history file for the drill",
)
@click.option(
    "--restore-dir", "-r",
    help="Directory to restore files to (default: temp dir)",
)
@click.option(
    "--keep-restore",
    is_flag=True,
    help="Keep the restored files after drill completes",
)
@click.option(
    "--file", "-f",
    multiple=True,
    help="Drill only specific file(s) (relative path, can be repeated)",
)
def drill(config, history, restore_dir, keep_restore, file):
    """Perform a recovery drill by restoring from backup and verifying."""
    try:
        config_path = config or find_config()
        manifest_config = load_config(config_path)

        if history:
            click.echo(f"Using history file: {history}")
            history_data = load_history(history)
            drill_result = run_drill_from_history(
                manifest_config, history_data, restore_dir, keep_restore
            )
        else:
            source_result = scan_source(manifest_config)
            backup_result = scan_backup(manifest_config)
            compare_result = compare_scans(source_result, backup_result, manifest_config)

            verify_only = list(file) if file else None
            drill_result = run_drill(
                manifest_config, source_result, compare_result,
                restore_dir, keep_restore, verify_only
            )

        summary = drill_result.summary()
        click.echo("=" * 70)
        click.echo("RECOVERY DRILL RESULTS")
        click.echo("=" * 70)
        click.echo(f"Started:    {format_timestamp(drill_result.started_at)}")
        click.echo(f"Completed:  {format_timestamp(drill_result.completed_at)}")
        click.echo(f"Restore to: {drill_result.restore_dir}")
        click.echo("")
        click.echo(f"  [OK] Success: {summary.get('success', 0)} files")
        click.echo(f"  [ERR] Failed:  {summary.get('failed', 0)} files")
        click.echo("")

        if drill_result.all_successful():
            click.echo("[OK] All files restored and verified successfully!")
        else:
            click.echo("Some files failed verification:")
            for f in drill_result.files:
                if f.status == "failed":
                    click.echo(f"  [ERR] {f.relative_path}")
                    if f.error:
                        click.echo(f"    {f.error}")

        click.echo("=" * 70)

        if not keep_restore and restore_dir is None:
            click.echo("(Temporary restore directory has been cleaned up)")

        sys.exit(EXIT_SUCCESS)

    except DrillError as e:
        click.echo(f"[ERR] Drill failed: {e.message}", err=True)
        sys.exit(e.exit_code)
    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except HistoryError as e:
        click.echo(f"[ERR] History error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@cli.command("history")
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--show",
    help="Show details of a specific history file (filename or index)",
)
@click.option(
    "--compare",
    nargs=2,
    metavar="FIRST SECOND",
    help="Compare two history files (filenames or indices, -1 for latest)",
)
def history_cmd(config, show, compare):
    """Manage and view scan history."""
    try:
        config_path = config or find_config()
        manifest_config = load_config(config_path)

        history_files = list_history(manifest_config)

        if not history_files:
            click.echo("No history found. Run 'backup-checker scan' first.")
            sys.exit(EXIT_SUCCESS)

        if show:
            if show.lstrip("-").isdigit():
                idx = int(show)
                if idx < 0:
                    idx = len(history_files) + idx
                if 0 <= idx < len(history_files):
                    show_file = history_files[idx]
                else:
                    raise HistoryError(f"History index out of range: {show}")
            else:
                show_file = show

            history_dir = os.path.join(
                os.path.dirname(manifest_config.config_path), ".backup-history"
            )
            file_path = os.path.join(history_dir, show_file)
            data = load_history(file_path)
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))
            sys.exit(EXIT_SUCCESS)

        if compare:
            first, second = compare
            history_dir = os.path.join(
                os.path.dirname(manifest_config.config_path), ".backup-history"
            )

            def resolve_history(ref):
                if ref.lstrip("-").isdigit():
                    idx = int(ref)
                    if idx < 0:
                        idx = len(history_files) + idx
                    if 0 <= idx < len(history_files):
                        return history_files[idx]
                    raise HistoryError(f"History index out of range: {ref}")
                return ref

            first_file = resolve_history(first)
            second_file = resolve_history(second)

            first_data = load_history(os.path.join(history_dir, first_file))
            second_data = load_history(os.path.join(history_dir, second_file))

            from .scanner import ScanResult, FileInfo
            from .comparator import CompareResult, FileDiff

            source_data = second_data["compare_result"]["source_scan"]
            source_files = [FileInfo(**f) for f in source_data["files"]]
            source_scan = ScanResult(
                directory=source_data["directory"],
                algorithm=source_data["algorithm"],
                files=source_files,
                scanned_at=source_data["scanned_at"],
            )

            backup_data = second_data["compare_result"]["backup_scan"]
            backup_files = [FileInfo(**f) for f in backup_data["files"]]
            backup_scan = ScanResult(
                directory=backup_data["directory"],
                algorithm=backup_data["algorithm"],
                files=backup_files,
                scanned_at=backup_data["scanned_at"],
            )

            diffs_data = second_data["compare_result"]["diffs"]
            diffs = [FileDiff(**d) for d in diffs_data]
            second_result = CompareResult(
                source_scan=source_scan,
                backup_scan=backup_scan,
                diffs=diffs,
                compared_at=second_data["compare_result"]["compared_at"],
            )

            comparison = compare_with_history(second_result, first_data)
            print_history_comparison(comparison)
            sys.exit(EXIT_SUCCESS)

        click.echo(f"Found {len(history_files)} history files:")
        for i, filename in enumerate(history_files):
            click.echo(f"  [{i}] {filename}")

        latest_path = get_latest_history_path(manifest_config)
        if latest_path:
            click.echo("")
            click.echo(f"Latest: {latest_path}")

        sys.exit(EXIT_SUCCESS)

    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except HistoryError as e:
        click.echo(f"[ERR] History error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@cli.group()
def profile():
    """Manage backup configuration profiles (export/import)."""
    pass


@profile.command("export")
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--output", "-o",
    default="backup-profile.json",
    help="Output JSON file path (default: backup-profile.json)",
)
def profile_export(config, output):
    """Export current configuration to a JSON profile file."""
    try:
        config_path = config or find_config()
        result_path = export_profile(config_path, output)
        click.echo(f"[OK] Profile exported to: {result_path}")
        sys.exit(EXIT_SUCCESS)

    except ProfileError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        sys.exit(e.exit_code)
    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@profile.command("import")
@click.argument("json_file")
@click.option(
    "--config", "-c",
    help=f"Target config file path (default: {CONFIG_FILENAME} in current dir)",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    help="Force overwrite if conflicts exist",
)
def profile_import(json_file, config, force):
    """Import configuration from a JSON profile file.

    JSON_FILE: Path to the JSON profile file to import.
    """
    try:
        target_config_path = config or os.path.join(os.getcwd(), CONFIG_FILENAME)
        result_path, backup_path = import_profile(json_file, target_config_path, force)

        if backup_path:
            click.echo(f"[OK] Rollback backup created at: {backup_path}")
            click.echo(f"[OK] Profile imported and config overwritten: {result_path}")
        else:
            click.echo(f"[OK] Profile imported to: {result_path}")

        sys.exit(EXIT_SUCCESS)

    except ProfileConflictError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        click.echo("", err=True)
        click.echo("To proceed with overwrite, use --force flag:", err=True)
        click.echo(f"  backup-checker profile import {json_file} --force", err=True)
        sys.exit(e.exit_code)
    except ProfileError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


@profile.command("log")
@click.option(
    "--config", "-c",
    help="Path to the manifest config file (default: auto-detect)",
)
@click.option(
    "--limit", "-n",
    type=int,
    default=20,
    help="Number of recent entries to show (default: 20)",
)
def profile_log(config, limit):
    """Show profile operation history."""
    try:
        config_path = config or find_config()
        logs = read_operation_logs(config_path)

        if not logs:
            click.echo("No profile operation history found.")
            sys.exit(EXIT_SUCCESS)

        recent_logs = logs[-limit:] if limit > 0 else logs

        click.echo(f"Showing {len(recent_logs)} most recent profile operations:")
        click.echo("")
        click.echo(f"{'Timestamp':<25} {'Operation':<10} {'Status':<12} {'Details'}")
        click.echo("-" * 90)

        for log in recent_logs:
            op = log.operation
            status = log.status
            click.echo(f"{log.timestamp:<25} {op:<10} {status:<12} {log.details}")
            if log.backup_path:
                click.echo(f"{'':<25} {'':<10} {'':<12}   backup: {log.backup_path}")

        sys.exit(EXIT_SUCCESS)

    except ProfileError as e:
        click.echo(f"[ERR] {e.message}", err=True)
        sys.exit(e.exit_code)
    except ConfigError as e:
        click.echo(f"[ERR] Config error: {e.message}", err=True)
        sys.exit(e.exit_code)
    except Exception as e:
        click.echo(f"[ERR] Unexpected error: {e}", err=True)
        sys.exit(EXIT_GENERAL_ERROR)


if __name__ == "__main__":
    cli()
