"""Regression tests for scan history saving functionality.

This test verifies:
1. The scan command outputs the correct "[OK] Saved history to:" message (not truncated)
2. The history file is actually written to disk
3. The history file contains valid JSON with expected structure
4. The fix does not break existing functionality (exit codes, file status detection)

Bug reference: scan output was showing "✓ved history to:" instead of
"[OK] Saved history to:" due to a typo in cli.py line 142.
"""

import subprocess
import sys
import os
import json
import shutil
import tempfile
from pathlib import Path

# Add parent directory to path so we can import the module
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

# Expected message in stdout
EXPECTED_HISTORY_MESSAGE = "[OK] Saved history to:"


def run_command(cmd, cwd=None):
    """Run a command and return completed process."""
    return subprocess.run(
        cmd,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
    )


def setup_test_directory():
    """Create a temporary test directory with example structure."""
    # Use the existing examples directory as template
    examples_dir = project_root.parent / "examples"

    # Create temp dir
    temp_dir = Path(tempfile.mkdtemp(prefix="backup_checker_test_"))

    # Copy source and backup from examples
    for subdir in ["source", "backup"]:
        src = examples_dir / subdir
        dst = temp_dir / subdir
        if src.exists():
            shutil.copytree(src, dst)

    # Ensure missing file is actually missing to test exit code 5
    missing_file = temp_dir / "backup" / "documents" / "missing-file.txt"
    if missing_file.exists():
        missing_file.unlink()

    # Ensure contract.txt is corrupted to test exit code 4 path
    source_contract = temp_dir / "source" / "documents" / "contract.txt"
    backup_contract = temp_dir / "backup" / "documents" / "contract.txt"
    source_contract.write_text("Original content for important contract. Version 1.0 signed.", encoding="utf-8")
    backup_contract.write_text("CORRUPTED content for important contract. This should fail verification.", encoding="utf-8")

    return temp_dir


def cleanup_test_directory(temp_dir):
    """Clean up temporary test directory."""
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_scan_outputs_correct_history_message():
    """Test that scan outputs the correct, non-truncated history message."""
    temp_dir = setup_test_directory()
    try:
        # 1. Initialize config
        result = run_command(
            f'backup-checker init -o . -s source -b backup -n "test-backup"',
            cwd=temp_dir,
        )
        assert result.returncode == 0, f"init failed: {result.stderr}"

        # 2. Run scan
        result = run_command(
            "backup-checker scan --no-compare-history",
            cwd=temp_dir,
        )

        # 3. Verify exit code (should be 5 due to missing file)
        assert result.returncode == 5, (
            f"Expected exit code 5 (missing file), got {result.returncode}. "
            f"stdout: {result.stdout[-500:]}"
        )

        # 4. Verify the history message is correct (the main regression test)
        assert EXPECTED_HISTORY_MESSAGE.lower() in result.stdout.lower(), (
            f"Expected message '{EXPECTED_HISTORY_MESSAGE}' not found in output.\n"
            f"Last 3 lines of stdout:\n"
            + "\n".join(repr(line) for line in result.stdout.strip().split("\n")[-3:])
        )

        # 5. Verify no truncated message exists
        assert "✓ved" not in result.stdout, "Truncated message '✓ved' found in output"

        print("PASS: test_scan_outputs_correct_history_message")
        return True

    finally:
        cleanup_test_directory(temp_dir)


def test_history_file_is_written_and_valid():
    """Test that the history file is actually written and contains valid data."""
    temp_dir = setup_test_directory()
    try:
        # Initialize and scan
        run_command(
            f'backup-checker init -o . -s source -b backup -n "test-backup"',
            cwd=temp_dir,
        )
        result = run_command(
            "backup-checker scan --no-compare-history",
            cwd=temp_dir,
        )

        # Extract history file path from output
        history_path = None
        for line in result.stdout.strip().split("\n"):
            if EXPECTED_HISTORY_MESSAGE in line:
                history_path = line.split(EXPECTED_HISTORY_MESSAGE)[1].strip()
                break

        assert history_path is not None, "Could not find history file path in output"

        # Verify file exists
        assert os.path.exists(history_path), f"History file does not exist: {history_path}"

        # Verify file is valid JSON with expected structure
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        required_top_level_keys = {"version", "timestamp", "compare_result", "summary"}
        assert required_top_level_keys.issubset(data.keys()), (
            f"History file missing required keys: {required_top_level_keys - data.keys()}"
        )

        # Verify compare_result structure
        compare_result = data["compare_result"]
        required_compare_keys = {"source_scan", "backup_scan", "diffs", "compared_at"}
        assert required_compare_keys.issubset(compare_result.keys()), (
            f"compare_result missing required keys: {required_compare_keys - compare_result.keys()}"
        )

        # Verify summary structure
        summary = data["summary"]
        required_summary_keys = {"ok", "missing", "corrupt", "expired", "unregistered"}
        assert required_summary_keys.issubset(summary.keys()), (
            f"summary missing required keys: {required_summary_keys - summary.keys()}"
        )

        # Verify summary values match expected (1 missing, 1 corrupt in test data)
        assert summary["missing"] == 1, f"Expected 1 missing file, got {summary['missing']}"
        assert summary["corrupt"] == 1, f"Expected 1 corrupt file, got {summary['corrupt']}"

        print("PASS: test_history_file_is_written_and_valid")
        return True

    finally:
        cleanup_test_directory(temp_dir)


def test_scan_with_no_save_does_not_write_history():
    """Test that --no-save flag prevents writing history file."""
    temp_dir = setup_test_directory()
    try:
        # Initialize and scan with --no-save
        run_command(
            f'backup-checker init -o . -s source -b backup -n "test-backup"',
            cwd=temp_dir,
        )
        result = run_command(
            "backup-checker scan --no-save --no-compare-history",
            cwd=temp_dir,
        )

        # Verify exit code
        assert result.returncode == 5, f"Expected exit code 5, got {result.returncode}"

        # Verify no history message in output
        assert EXPECTED_HISTORY_MESSAGE not in result.stdout, (
            "History message should not appear with --no-save"
        )

        # Verify no history directory was created
        history_dir = temp_dir / ".backup-history"
        if history_dir.exists():
            files = list(history_dir.glob("scan_*.json"))
            assert len(files) == 0, f"History file was created with --no-save: {files}"

        print("PASS: test_scan_with_no_save_does_not_write_history")
        return True

    finally:
        cleanup_test_directory(temp_dir)


def test_exit_codes_still_work():
    """Test that exit codes for missing/corrupt files still work correctly."""
    temp_dir = setup_test_directory()
    try:
        # Initialize
        run_command(
            f'backup-checker init -o . -s source -b backup -n "test-backup"',
            cwd=temp_dir,
        )

        # Test exit code 5 (missing file)
        result = run_command(
            "backup-checker scan --no-save --no-compare-history",
            cwd=temp_dir,
        )
        assert result.returncode == 5, (
            f"Expected exit code 5 for missing file, got {result.returncode}"
        )
        print("  Verified exit code 5 (missing file) works")

        # Fix missing file
        src = temp_dir / "source" / "documents" / "missing-file.txt"
        dst = temp_dir / "backup" / "documents" / "missing-file.txt"
        shutil.copy(src, dst)

        # Test exit code 4 (corrupt file)
        result = run_command(
            "backup-checker scan --no-save --no-compare-history",
            cwd=temp_dir,
        )
        assert result.returncode == 4, (
            f"Expected exit code 4 for corrupt file, got {result.returncode}"
        )
        print("  Verified exit code 4 (corrupt file) works")

        # Fix corrupt file
        src = temp_dir / "source" / "documents" / "contract.txt"
        dst = temp_dir / "backup" / "documents" / "contract.txt"
        shutil.copy(src, dst)

        # Test exit code 0 (all OK)
        result = run_command(
            "backup-checker scan --no-save --no-compare-history",
            cwd=temp_dir,
        )
        assert result.returncode == 0, (
            f"Expected exit code 0 for all OK, got {result.returncode}"
        )
        print("  Verified exit code 0 (all OK) works")

        print("PASS: test_exit_codes_still_work")
        return True

    finally:
        cleanup_test_directory(temp_dir)


def main():
    """Run all regression tests."""
    print("=" * 70)
    print("Running Scan History Regression Tests")
    print("=" * 70)
    print()

    tests = [
        test_scan_outputs_correct_history_message,
        test_history_file_is_written_and_valid,
        test_scan_with_no_save_does_not_write_history,
        test_exit_codes_still_work,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {test.__name__}")
            print(f"  {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {test.__name__}")
            print(f"  {type(e).__name__}: {e}")
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
