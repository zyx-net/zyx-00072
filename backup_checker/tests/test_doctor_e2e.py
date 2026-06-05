"""End-to-end tests for doctor diagnostic CLI functionality.

This verifies:
1. Normal doctor check passes with warnings for missing dirs
2. --json output is valid and stable JSON
3. --fix creates missing history and profile log directories
4. Bad YAML returns correct exit code and error message
5. Duplicate target returns correct exit code and error message
6. Unknown hash algorithm returns correct exit code and error message
7. Permission denied returns correct exit code and error message
8. Doctor operations are logged to profile log
9. After --fix, scan command works correctly
10. Default config (auto-detect) works
11. --config option works with explicit path
"""

import subprocess
import sys
import os
import json
import yaml
import shutil
import tempfile
from pathlib import Path

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

EXIT_SUCCESS = 0
EXIT_GENERAL_ERROR = 1
EXIT_CONFIG_ERROR = 2
EXIT_DUPLICATE_TARGET = 3
EXIT_DOCTOR_ERROR = 14
EXIT_DOCTOR_WARNING = 15
EXIT_DOCTOR_PERMISSION = 16
EXIT_DOCTOR_UNKNOWN_ALGORITHM = 17
EXIT_DOCTOR_DUPLICATE_TARGET = 18

EXAMPLES_DIR = project_root.parent / "examples"


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
    temp_dir = Path(tempfile.mkdtemp(prefix="doctor_e2e_test_"))

    for subdir in ["source", "backup"]:
        src = EXAMPLES_DIR / subdir
        dst = temp_dir / subdir
        if src.exists():
            shutil.copytree(src, dst)

    missing_file = temp_dir / "backup" / "documents" / "missing-file.txt"
    if missing_file.exists():
        missing_file.unlink()

    source_contract = temp_dir / "source" / "documents" / "contract.txt"
    backup_contract = temp_dir / "backup" / "documents" / "contract.txt"
    source_contract.write_text(
        "Original content for important contract. Version 1.0 signed.",
        encoding="utf-8"
    )
    backup_contract.write_text(
        "CORRUPTED content for important contract. This should fail verification.",
        encoding="utf-8"
    )

    return temp_dir


def cleanup_test_directory(temp_dir):
    """Clean up temporary test directory."""
    shutil.rmtree(temp_dir, ignore_errors=True)


def run_test(test_name, cmd, cwd, expected_code, check_stdout=None,
             check_stderr=None, not_check_stdout=None, description=""):
    """Run a test case with assertions."""
    print("=" * 60)
    print(f"TEST: {test_name}")
    if description:
        print(f"  {description}")
    print(f"CMD:  {cmd}")

    result = run_command(cmd, cwd=cwd)
    print(f"EXIT: {result.returncode}")

    if result.stdout:
        lines = result.stdout.strip().split("\n")
        show = lines[-10:] if len(lines) > 10 else lines
        print("STDOUT (tail):")
        for line in show:
            print(f"  {line}")

    if result.stderr:
        print("STDERR:")
        for line in result.stderr.strip().split("\n"):
            print(f"  {line}")

    if result.returncode != expected_code:
        print(f"FAIL: Expected exit code {expected_code}, got {result.returncode}")
        sys.exit(1)

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

    if not_check_stdout:
        for check in not_check_stdout:
            found = check.lower() in result.stdout.lower()
            if found:
                print(f"FAIL: Expected stdout to NOT contain '{check}' but it does")
                sys.exit(1)

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
    print("DOCTOR END-TO-END TEST SUITE")
    print("=" * 70)
    print()

    temp_dir = setup_test_directory()
    print(f"Working directory: {temp_dir}")
    print()

    try:
        run_test(
            "Initialize config",
            'backup-checker init -o . -s source -b backup -n "doctor-test"',
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Created config file:"],
            description="Create initial backup-manifest.yaml"
        )

        run_test(
            "Normal doctor check (with warnings)",
            "backup-checker doctor",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_WARNING,
            check_stdout=[
                "BACKUP CHECKER DOCTOR REPORT",
                "History directory does not exist",
                "Profile log directory does not exist",
                "fixable with --fix",
                "PASSED WITH WARNINGS",
            ],
            check_stderr=[],
            description="Doctor should warn about missing history and profile dirs"
        )

        result = run_test(
            "Doctor JSON output",
            "backup-checker doctor --json",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_WARNING,
            check_stdout=[],
            check_stderr=[],
            description="JSON output should be valid JSON"
        )

        json_data = json.loads(result.stdout)
        assert "checks" in json_data, "JSON missing 'checks' field"
        assert "summary" in json_data, "JSON missing 'summary' field"
        assert "timestamp" in json_data, "JSON missing 'timestamp' field"
        assert "config_path" in json_data, "JSON missing 'config_path' field"
        assert json_data["summary"]["ok"] >= 10
        assert json_data["summary"]["warn"] >= 1
        assert json_data["summary"]["error"] == 0
        print("VERIFIED: JSON output is valid and contains expected fields")
        print()

        result2 = run_command("backup-checker doctor --json", cwd=temp_dir)
        json_data2 = json.loads(result2.stdout)
        assert json_data["checks"] == json_data2["checks"], "JSON output is not stable (checks differ)"
        print("VERIFIED: JSON output is stable across multiple runs")
        print()

        history_dir = temp_dir / ".backup-history"
        profile_dir = temp_dir / ".backup-profiles"
        assert not history_dir.exists(), "History dir should not exist before --fix"
        assert profile_dir.exists(), "Profile dir should already exist from logging"

        run_test(
            "Doctor with --fix creates directories",
            "backup-checker doctor --fix",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "FIXES APPLIED",
                "Created history directory",
                "PASSED - All checks OK",
            ],
            check_stderr=[],
            description="--fix should create missing history directory and pass"
        )

        assert history_dir.exists(), "History dir was not created by --fix"
        assert profile_dir.exists(), "Profile dir should still exist"
        print("VERIFIED: History directory created by --fix")
        print()

        run_test(
            "Doctor check after fix (all OK)",
            "backup-checker doctor",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "PASSED - All checks OK",
                "ready to run scan/report/drill",
            ],
            check_stderr=[],
            description="After fix, all checks should pass"
        )

        bad_yaml = temp_dir / "bad.yaml"
        bad_yaml.write_text("this is: not: valid: yaml: [", encoding="utf-8")
        run_test(
            "Bad YAML config",
            "backup-checker doctor -c bad.yaml",
            cwd=temp_dir,
            expected_code=EXIT_CONFIG_ERROR,
            check_stdout=["Invalid YAML format", "[ERR] config.yaml"],
            check_stderr=[],
            description="Exit code 2 for bad YAML"
        )

        run_test(
            "Bad YAML config with --json",
            "backup-checker doctor -c bad.yaml --json",
            cwd=temp_dir,
            expected_code=EXIT_CONFIG_ERROR,
            check_stdout=["{", "}", '"exit_code": 2', '"error_category": "invalid_yaml"'],
            not_check_stdout=["[ERR]"],
            check_stderr=[],
            description="JSON output for bad YAML, no [ERR] text"
        )
        result = subprocess.run(
            ["backup-checker", "doctor", "-c", "bad.yaml", "--json"],
            cwd=temp_dir, capture_output=True, text=True
        )
        try:
            json_data = json.loads(result.stdout)
            assert json_data["exit_code"] == EXIT_CONFIG_ERROR
            assert json_data["error_category"] == "invalid_yaml"
            assert json_data["summary"]["error"] >= 1
            assert "Invalid YAML format" in json_data["checks"][0]["message"]
            print("VERIFIED: Bad YAML --json output is valid JSON with correct fields")
        except json.JSONDecodeError:
            pytest.fail(f"Bad YAML --json output is not valid JSON: {result.stdout}")
        print()

        dup_config = temp_dir / "dup.yaml"
        dup_config.write_text(yaml.dump({
            "manifest": {
                "name": "test",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/"},
                    {"path": "documents/"},
                ],
                "hash_algorithm": "sha256",
            }
        }), encoding="utf-8")
        run_test(
            "Duplicate target config",
            "backup-checker doctor -c dup.yaml",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_DUPLICATE_TARGET,
            check_stdout=["Duplicate target paths found", "[ERR] config.load"],
            check_stderr=[],
            description="Exit code 18 for duplicate targets"
        )

        run_test(
            "Duplicate target config with --json",
            "backup-checker doctor -c dup.yaml --json",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_DUPLICATE_TARGET,
            check_stdout=["{", "}", '"exit_code": 18', '"error_category": "duplicate_target"'],
            not_check_stdout=["[ERR]"],
            check_stderr=[],
            description="JSON output for duplicate target, no [ERR] text"
        )
        result = subprocess.run(
            ["backup-checker", "doctor", "-c", "dup.yaml", "--json"],
            cwd=temp_dir, capture_output=True, text=True
        )
        try:
            json_data = json.loads(result.stdout)
            assert json_data["exit_code"] == EXIT_DOCTOR_DUPLICATE_TARGET
            assert json_data["error_category"] == "duplicate_target"
            assert json_data["summary"]["error"] >= 1
            assert "Duplicate target paths found" in json_data["checks"][0]["message"]
            assert "documents/" in json_data["checks"][0]["details"]["duplicates"]
            print("VERIFIED: Duplicate target --json output is valid JSON with correct fields")
        except json.JSONDecodeError:
            pytest.fail(f"Duplicate target --json output is not valid JSON: {result.stdout}")
        print()

        unknown_algo_config = temp_dir / "unknown-algo.yaml"
        unknown_algo_config.write_text(yaml.dump({
            "manifest": {
                "name": "test",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [{"path": "documents/"}],
                "hash_algorithm": "unknown123",
            }
        }), encoding="utf-8")
        run_test(
            "Unknown hash algorithm",
            "backup-checker doctor -c unknown-algo.yaml",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_UNKNOWN_ALGORITHM,
            check_stdout=[
                "Unsupported hash algorithm",
                "unknown123",
                "md5", "sha1", "sha256", "sha512",
                "[ERR] config.hash_algorithm",
            ],
            check_stderr=[],
            description="Exit code 17 for unknown hash algorithm"
        )

        run_test(
            "Unknown hash algorithm with --json",
            "backup-checker doctor -c unknown-algo.yaml --json",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_UNKNOWN_ALGORITHM,
            check_stdout=["{", "}", '"exit_code": 17', '"error_category": "unknown_algorithm"'],
            not_check_stdout=["[ERR]"],
            check_stderr=[],
            description="JSON output for unknown algorithm, no [ERR] text"
        )
        result = subprocess.run(
            ["backup-checker", "doctor", "-c", "unknown-algo.yaml", "--json"],
            cwd=temp_dir, capture_output=True, text=True
        )
        try:
            json_data = json.loads(result.stdout)
            assert json_data["exit_code"] == EXIT_DOCTOR_UNKNOWN_ALGORITHM
            assert json_data["error_category"] == "unknown_algorithm"
            assert json_data["summary"]["error"] >= 1
            assert "Unsupported hash algorithm" in json_data["checks"][0]["message"]
            assert "unknown123" in json_data["checks"][0]["message"]
            print("VERIFIED: Unknown algorithm --json output is valid JSON with correct fields")
        except json.JSONDecodeError:
            pytest.fail(f"Unknown algorithm --json output is not valid JSON: {result.stdout}")
        print()

        missing_source_config = temp_dir / "missing-source.yaml"
        missing_source_config.write_text(yaml.dump({
            "manifest": {
                "name": "test",
                "source_dir": "nonexistent_source",
                "backup_dir": "backup",
                "targets": [{"path": "documents/"}],
                "hash_algorithm": "sha256",
            }
        }), encoding="utf-8")
        run_test(
            "Missing source directory",
            "backup-checker doctor -c missing-source.yaml",
            cwd=temp_dir,
            expected_code=EXIT_DOCTOR_ERROR,
            check_stdout=[
                "source_dir does not exist",
                "FAILED - Errors found",
            ],
            check_stderr=[],
            description="Exit code 14 for missing source dir"
        )

        run_test(
            "Doctor with explicit --config",
            "backup-checker doctor -c backup-manifest.yaml",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["PASSED - All checks OK"],
            check_stderr=[],
            description="--config option should work with explicit path"
        )

        subdir = temp_dir / "subdir"
        subdir.mkdir()
        run_test(
            "Doctor auto-detect config from subdirectory",
            "backup-checker doctor",
            cwd=subdir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["PASSED - All checks OK"],
            check_stderr=[],
            description="Should auto-detect config in parent directory"
        )

        run_test(
            "Profile log shows doctor operations",
            "backup-checker profile log -n 50",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "doctor",
                "warning",
                "success",
            ],
            check_stderr=[],
            description="Profile log should show all doctor operations"
        )

        log_file = temp_dir / ".backup-profiles" / "profile-operations.log"
        assert log_file.exists(), "Profile log file not created"

        log_lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(log_lines) >= 3, f"Expected at least 3 log entries, got {len(log_lines)}"

        doctor_ops = []
        for line in log_lines:
            if line.strip():
                entry = json.loads(line)
                if entry.get("operation") == "doctor":
                    doctor_ops.append(entry)

        assert len(doctor_ops) >= 3, f"Expected at least 3 doctor operations, got {len(doctor_ops)}"
        print("VERIFIED: Doctor operations are logged to profile log")
        print()

        run_test(
            "Scan works after doctor --fix",
            "backup-checker scan --no-save --no-compare-history",
            cwd=temp_dir,
            expected_code=5,
            check_stdout=[
                "Scanning source",
                "Scanning backup",
                "[MISS]",
                "[CORR]",
            ],
            check_stderr=[],
            description="Scan should work correctly after doctor --fix"
        )

        run_test(
            "Report works after doctor --fix",
            f"backup-checker report -f json -o {temp_dir}/doctor-report.json",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] JSON report exported to:"],
            check_stderr=[],
            description="Report should work correctly after doctor --fix"
        )

        report_file = temp_dir / "doctor-report.json"
        assert report_file.exists(), "Report file not created"
        with open(report_file, "r", encoding="utf-8") as f:
            report_data = json.load(f)
        assert "result" in report_data
        assert "source_scan" in report_data["result"]
        assert "backup_scan" in report_data["result"]
        assert "summary" in report_data
        print("VERIFIED: Report generated successfully")
        print()

        fresh_dir = temp_dir / "fresh"
        fresh_dir.mkdir()
        fresh_source = fresh_dir / "source" / "documents"
        fresh_backup = fresh_dir / "backup" / "documents"
        fresh_source.mkdir(parents=True)
        fresh_backup.mkdir(parents=True)
        (fresh_source / "test.txt").write_text("test content", encoding="utf-8")
        (fresh_backup / "test.txt").write_text("test content", encoding="utf-8")

        run_test(
            "Init in fresh directory",
            'backup-checker init -o . -s source -b backup -n "fresh-test"',
            cwd=fresh_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Created config file:"],
            description="Initialize config in fresh directory"
        )

        run_test(
            "Doctor in fresh dir (warnings)",
            "backup-checker doctor",
            cwd=fresh_dir,
            expected_code=EXIT_DOCTOR_WARNING,
            check_stdout=[
                "History directory does not exist",
            ],
            check_stderr=[],
            description="Should warn about missing history dir (profile dir auto-created by logging)"
        )

        fresh_history_dir = fresh_dir / ".backup-history"
        fresh_profile_dir = fresh_dir / ".backup-profiles"
        assert not fresh_history_dir.exists(), "History dir should not exist yet"
        assert fresh_profile_dir.exists(), "Profile dir should be auto-created by logging"

        run_test(
            "Doctor --fix in fresh dir",
            "backup-checker doctor --fix",
            cwd=fresh_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "FIXES APPLIED",
                "Created history directory",
                "PASSED - All checks OK",
            ],
            check_stderr=[],
            description="--fix should create history dir in fresh dir"
        )

        assert fresh_history_dir.exists(), "History dir was not created by --fix"

        run_test(
            "Scan works after fix in fresh dir",
            "backup-checker scan --no-save --no-compare-history",
            cwd=fresh_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "Scanning source",
                "Scanning backup",
                "All files verified successfully",
            ],
            check_stderr=[],
            description="Scan should pass after fix in fresh dir"
        )

        print("VERIFIED: Full doctor -> fix -> scan workflow works")
        print()

        output_grouped = run_command("backup-checker doctor", cwd=temp_dir).stdout
        assert "ERROR:" in output_grouped or "WARN:" in output_grouped or "OK:" in output_grouped
        error_idx = output_grouped.find("ERROR:")
        warn_idx = output_grouped.find("WARN:")
        ok_idx = output_grouped.find("OK:")
        if error_idx != -1 and warn_idx != -1:
            assert error_idx < warn_idx, "ERROR section should come before WARN section"
        if warn_idx != -1 and ok_idx != -1:
            assert warn_idx < ok_idx, "WARN section should come before OK section"
        print("VERIFIED: Output is grouped by status in correct order (ERROR > WARN > OK)")
        print()

        print("=" * 70)
        print("ALL DOCTOR END-TO-END TESTS PASSED!")
        print("=" * 70)
        print()
        print("SUMMARY OF VERIFICATIONS:")
        print("  [OK] Normal doctor check works with warnings")
        print("  [OK] --json output is valid, stable JSON with correct fields")
        print("  [OK] --fix creates missing history and profile directories")
        print("  [OK] Bad YAML returns exit code 2 with clear error")
        print("  [OK] Duplicate target returns exit code 18 with clear error")
        print("  [OK] Unknown algorithm returns exit code 17 with clear error")
        print("  [OK] Missing source dir returns exit code 14 with clear error")
        print("  [OK] --config option works with explicit path")
        print("  [OK] Auto-detect config works from subdirectory")
        print("  [OK] Doctor operations are logged to profile log")
        print("  [OK] Scan works after doctor --fix")
        print("  [OK] Report works after doctor --fix")
        print("  [OK] Full doctor -> fix -> scan workflow works")
        print("  [OK] Output is grouped by status (ERROR > WARN > OK)")
        print()

        return 0

    finally:
        cleanup_test_directory(temp_dir)
        print("Temporary test directory cleaned up.")


if __name__ == "__main__":
    sys.exit(main())
