"""Full end-to-end test for the entire backup-checker workflow.

This verifies:
1. Complete init -> scan -> report -> drill workflow
2. All exit codes work correctly (0, 3, 4, 5)
3. History saving message is correct (regression test for the "✓ved" bug)
4. History comparison works on second scan
5. All existing functionality is not broken
"""

import subprocess
import sys
import os
import json
import shutil
from pathlib import Path

# Add parent directory to path
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

EXPECTED_HISTORY_MESSAGE = "[OK] Saved history to:"
BUGGY_MESSAGE = "✓ved"


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


def setup_examples_directory():
    """Reset the examples directory to clean state."""
    examples_dir = project_root.parent / "examples"

    # Clean up
    for item in ["backup-manifest.yaml", "report.json", "report.csv", "report.txt"]:
        f = examples_dir / item
        if f.exists():
            f.unlink()

    history_dir = examples_dir / ".backup-history"
    if history_dir.exists():
        shutil.rmtree(history_dir)

    # Reset missing file
    missing_file = examples_dir / "backup" / "documents" / "missing-file.txt"
    if missing_file.exists():
        missing_file.unlink()

    # Reset contract.txt to be corrupted
    source_contract = examples_dir / "source" / "documents" / "contract.txt"
    backup_contract = examples_dir / "backup" / "documents" / "contract.txt"
    source_contract.write_text(
        "Original content for important contract. Version 1.0 signed.",
        encoding="utf-8"
    )
    backup_contract.write_text(
        "CORRUPTED content for important contract. This should fail verification.",
        encoding="utf-8"
    )

    # Reset old-project date
    old_file = examples_dir / "backup" / "documents" / "old-project-2020.zip"
    old_date = os.path.getmtime(old_file) - (100 * 24 * 3600)
    os.utime(old_file, (old_date, old_date))

    return examples_dir


def run_test(test_name, cmd, cwd, expected_code, check_stdout=None,
             check_stderr=None, description=""):
    """Run a test case with assertions."""
    print("=" * 60)
    print(f"TEST: {test_name}")
    if description:
        print(f"  {description}")
    print(f"CMD:  {cmd}")

    result = run_command(cmd, cwd=cwd)
    print(f"EXIT: {result.returncode}")

    # Show last 5 lines of stdout
    if result.stdout:
        lines = result.stdout.strip().split("\n")
        show = lines[-5:] if len(lines) > 5 else lines
        print("STDOUT (tail):")
        for line in show:
            print(f"  {line}")

    # Show stderr if any
    if result.stderr:
        print("STDERR:")
        for line in result.stderr.strip().split("\n"):
            print(f"  {line}")

    # Check exit code
    if result.returncode != expected_code:
        print(f"FAIL: Expected exit code {expected_code}, got {result.returncode}")
        sys.exit(1)

    # Check stdout
    if check_stdout:
        for check in check_stdout:
            if isinstance(check, tuple):
                text, should_contain = check
            else:
                text, should_contain = check, True

            found = text.lower() in result.stdout.lower()
            if found != should_contain:
                action = "contain" if should_contain else "NOT contain"
                print(f"FAIL: Expected stdout to {action} '{text}'")
                sys.exit(1)

    # Check stderr
    if check_stderr:
        for check in check_stderr:
            if isinstance(check, tuple):
                text, should_contain = check
            else:
                text, should_contain = check, True

            found = text.lower() in result.stderr.lower()
            if found != should_contain:
                action = "contain" if should_contain else "NOT contain"
                print(f"FAIL: Expected stderr to {action} '{text}'")
                sys.exit(1)

    print(f"PASS: {test_name}")
    print()
    return result


def main():
    print("=" * 70)
    print("FULL END-TO-END TEST SUITE")
    print("=" * 70)
    print()

    examples_dir = setup_examples_directory()
    print(f"Working directory: {examples_dir}")
    print()

    # TEST 1: Initialize config
    run_test(
        "Initialize config",
        'backup-checker init -o . -s source -b backup -n "example-backup"',
        cwd=examples_dir,
        expected_code=0,
        check_stdout=["[OK] Created config file:"],
        description="Create backup-manifest.yaml"
    )

    # TEST 2: First scan - verify correct history message (main regression test)
    result = run_test(
        "First scan (with issues)",
        "backup-checker scan --no-compare-history",
        cwd=examples_dir,
        expected_code=5,
        check_stdout=[
            EXPECTED_HISTORY_MESSAGE,  # Must have correct message
            (BUGGY_MESSAGE, False),    # Must NOT have truncated message
            "[MISS] MISSING",
            "[CORR] CORRUPT",
        ],
        description="Main regression test: verify history message is NOT truncated"
    )

    # Verify history file was actually created
    history_dir = examples_dir / ".backup-history"
    history_files = list(history_dir.glob("scan_*.json"))
    assert len(history_files) == 1, f"Expected 1 history file, got {len(history_files)}"
    print(f"VERIFIED: History file created: {history_files[0].name}")

    # Verify history file content
    with open(history_files[0], "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["summary"]["missing"] == 1, "Expected 1 missing file in summary"
    assert data["summary"]["corrupt"] == 1, "Expected 1 corrupt file in summary"
    print("VERIFIED: History file contains valid summary data")
    print()

    # TEST 3: Report exports
    report_formats = [
        ("json", "JSON"),
        ("csv", "CSV"),
        ("text", "Text"),
    ]
    for fmt, display in report_formats:
        run_test(
            f"Export {fmt} report",
            f"backup-checker report -f {fmt} -o report.{fmt}",
            cwd=examples_dir,
            expected_code=0,
            check_stdout=[f"[OK] {display} report exported to:"],
        )
        report_file = examples_dir / f"report.{fmt}"
        assert report_file.exists(), f"report.{fmt} not created"
        print(f"VERIFIED: report.{fmt} exists")
    print()

    # TEST 4: Drill with missing file - exit code 5
    run_test(
        "Drill with missing file",
        "backup-checker drill",
        cwd=examples_dir,
        expected_code=5,
        check_stderr=["missing"],
        description="Exit code 5 when files are missing"
    )

    # TEST 5: Fix missing, drill with corrupt - exit code 4
    src = examples_dir / "source" / "documents" / "missing-file.txt"
    dst = examples_dir / "backup" / "documents" / "missing-file.txt"
    shutil.copy(src, dst)

    run_test(
        "Drill with corrupt file",
        "backup-checker drill",
        cwd=examples_dir,
        expected_code=4,
        check_stderr=["checksum", "mismatch"],
        description="Exit code 4 when checksums don't match"
    )

    # TEST 6: Duplicate target - exit code 3
    dup_file = examples_dir / "dup.yaml"
    dup_file.write_text("""manifest:
  name: test
  source_dir: source
  backup_dir: backup
  targets:
  - path: documents/
  - path: documents/
  retention_days: 30
  hash_algorithm: sha256
""", encoding="utf-8")

    run_test(
        "Duplicate target path",
        "backup-checker scan -c dup.yaml --no-save",
        cwd=examples_dir,
        expected_code=3,
        check_stderr=["duplicate"],
        description="Exit code 3 for duplicate target paths"
    )
    dup_file.unlink()

    # TEST 7: Fix corrupt, second scan - exit code 0, compare history
    src = examples_dir / "source" / "documents" / "contract.txt"
    dst = examples_dir / "backup" / "documents" / "contract.txt"
    shutil.copy(src, dst)

    result = run_test(
        "Second scan (with history comparison)",
        "backup-checker scan",
        cwd=examples_dir,
        expected_code=0,
        check_stdout=[
            EXPECTED_HISTORY_MESSAGE,
            "HISTORY COMPARISON REPORT",
            "FIXED",
        ],
        description="History comparison should show 2 files fixed"
    )

    # Verify 2 history files now
    history_files = list(history_dir.glob("scan_*.json"))
    assert len(history_files) == 2, f"Expected 2 history files, got {len(history_files)}"
    print(f"VERIFIED: 2 history files exist after second scan")
    print()

    # TEST 8: Drill success - exit code 0
    run_test(
        "Drill success",
        "backup-checker drill",
        cwd=examples_dir,
        expected_code=0,
        check_stdout=["All files restored and verified successfully"],
        description="Exit code 0 when all files are correct"
    )

    # TEST 9: History list
    run_test(
        "History list",
        "backup-checker history",
        cwd=examples_dir,
        expected_code=0,
        check_stdout=["Found 2 history files"],
    )

    # TEST 10: History compare
    run_test(
        "History compare",
        "backup-checker history --compare 0 -1",
        cwd=examples_dir,
        expected_code=0,
        check_stdout=["SUMMARY COMPARISON", "FIXED"],
    )

    # TEST 11: --no-save flag works
    history_count_before = len(list(history_dir.glob("scan_*.json")))
    run_test(
        "Scan with --no-save",
        "backup-checker scan --no-save --no-compare-history",
        cwd=examples_dir,
        expected_code=0,
        check_stdout=[
            (EXPECTED_HISTORY_MESSAGE, False),  # Should NOT show save message
        ],
        description="--no-save should prevent writing history"
    )
    history_count_after = len(list(history_dir.glob("scan_*.json")))
    assert history_count_before == history_count_after, \
        f"History count changed with --no-save: {history_count_before} -> {history_count_after}"
    print("VERIFIED: --no-save does not create new history file")
    print()

    print("=" * 70)
    print("ALL END-TO-END TESTS PASSED!")
    print("=" * 70)
    print()
    print("SUMMARY OF VERIFICATIONS:")
    print(f"  ✓ {EXPECTED_HISTORY_MESSAGE} message is complete (not truncated)")
    print(f"  ✓ No '{BUGGY_MESSAGE}' truncated message in output")
    print("  ✓ History files are written and contain valid data")
    print("  ✓ Exit codes: 0 (success), 3 (duplicate), 4 (checksum), 5 (missing)")
    print("  ✓ History comparison works between scans")
    print("  ✓ All report formats export correctly")
    print("  ✓ Drill works for all scenarios (fail/success)")
    print("  ✓ --no-save flag prevents history creation")
    print()

    # Clean up examples directory
    setup_examples_directory()
    print("Examples directory reset to clean state.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
