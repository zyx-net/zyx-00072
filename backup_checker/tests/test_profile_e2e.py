"""End-to-end tests for profile export/import CLI functionality.

This verifies:
1. Successful export of config to JSON
2. Successful import of JSON to new config
3. Conflict detection and rejection without --force
4. Force overwrite with --force and rollback backup creation
5. Error handling for bad JSON, unknown algorithm, duplicate targets
6. Operation logging works across CLI calls
7. Imported config works with existing scan/report/drill commands
8. Log file persists and shows correct operation history
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
EXIT_PROFILE_CONFLICT = 9
EXIT_PROFILE_INVALID_JSON = 10
EXIT_PROFILE_PERMISSION_DENIED = 11
EXIT_PROFILE_UNKNOWN_ALGORITHM = 12
EXIT_PROFILE_INVALID_CONFIG = 13

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
    temp_dir = Path(tempfile.mkdtemp(prefix="profile_e2e_test_"))

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
             check_stderr=None, description=""):
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
        show = lines[-5:] if len(lines) > 5 else lines
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
    print("PROFILE END-TO-END TEST SUITE")
    print("=" * 70)
    print()

    temp_dir = setup_test_directory()
    print(f"Working directory: {temp_dir}")
    print()

    try:
        run_test(
            "Initialize config",
            'backup-checker init -o . -s source -b backup -n "e2e-test"',
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Created config file:"],
            description="Create initial backup-manifest.yaml"
        )

        run_test(
            "Export profile to JSON",
            "backup-checker profile export -o my-profile.json",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Profile exported to:"],
            description="Export current config to JSON profile"
        )

        export_file = temp_dir / "my-profile.json"
        assert export_file.exists(), "Exported JSON file not created"
        with open(export_file, "r", encoding="utf-8") as f:
            export_data = json.load(f)
        assert export_data["manifest"]["name"] == "e2e-test"
        assert export_data["manifest"]["source_dir"] == "source"
        assert len(export_data["manifest"]["targets"]) >= 1
        print("VERIFIED: Exported JSON contains correct data")
        print()

        import_target = temp_dir / "new-config" / "backup-manifest.yaml"
        run_test(
            "Import profile to new location",
            f"backup-checker profile import my-profile.json -c {import_target}",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Profile imported to:"],
            description="Import JSON to a new config file location"
        )

        assert import_target.exists(), "Imported config file not created"
        with open(import_target, "r", encoding="utf-8") as f:
            import_data = yaml.safe_load(f)
        assert import_data["manifest"]["name"] == "e2e-test"
        assert import_data["manifest"]["source_dir"] == "source"
        print("VERIFIED: Imported config contains correct data")
        print()

        existing_config = temp_dir / "backup-manifest.yaml"
        original_content = existing_config.read_text(encoding="utf-8")

        modified_json = temp_dir / "modified-profile.json"
        modified_data = dict(export_data)
        modified_data["manifest"]["source_dir"] = "different_source"
        modified_data["manifest"]["hash_algorithm"] = "md5"
        modified_data["manifest"]["targets"] = [
            {"path": "new_target/", "description": "New target"}
        ]
        modified_data["manifest"]["exclude_patterns"] = ["*.new"]
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        run_test(
            "Import with conflict - rejected",
            f"backup-checker profile import {modified_json.name}",
            cwd=temp_dir,
            expected_code=EXIT_PROFILE_CONFLICT,
            check_stderr=[
                "Configuration conflicts detected",
                "source_dir",
                "hash_algorithm",
                "targets",
                "exclude_patterns",
                "--force",
            ],
            description="Import should be rejected when conflicts exist"
        )

        assert existing_config.read_text(encoding="utf-8") == original_content, \
            "Config was modified despite conflict rejection"
        print("VERIFIED: Config unchanged after conflict rejection")
        print()

        result = run_test(
            "Import with --force - overwritten",
            f"backup-checker profile import {modified_json.name} --force",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "[OK] Rollback backup created at:",
                "[OK] Profile imported and config overwritten:",
            ],
            description="Import with --force should overwrite and create backup"
        )

        backup_path = None
        for line in result.stdout.strip().split("\n"):
            if "[OK] Rollback backup created at:" in line:
                backup_path = line.split("[OK] Rollback backup created at:")[1].strip()
                break

        assert backup_path is not None, "Backup path not found in output"
        assert os.path.exists(backup_path), f"Backup file not created at: {backup_path}"
        assert Path(backup_path).read_text(encoding="utf-8") == original_content, \
            "Backup file does not contain original content"
        print(f"VERIFIED: Rollback backup exists at: {backup_path}")

        with open(existing_config, "r", encoding="utf-8") as f:
            overwritten_data = yaml.safe_load(f)
        assert overwritten_data["manifest"]["source_dir"] == "different_source"
        assert overwritten_data["manifest"]["hash_algorithm"] == "md5"
        assert overwritten_data["manifest"]["targets"][0]["path"] == "new_target/"
        print("VERIFIED: Config was overwritten with new values")
        print()

        bad_json = temp_dir / "bad.json"
        bad_json.write_text("{this is not valid json", encoding="utf-8")
        run_test(
            "Import invalid JSON",
            f"backup-checker profile import {bad_json.name}",
            cwd=temp_dir,
            expected_code=EXIT_PROFILE_INVALID_JSON,
            check_stderr=["Invalid JSON format"],
            description="Exit code 10 for invalid JSON"
        )

        unknown_algo_json = temp_dir / "unknown-algo.json"
        unknown_algo_json.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
                "hash_algorithm": "unknown123",
            }
        }), encoding="utf-8")
        run_test(
            "Import unknown hash algorithm",
            f"backup-checker profile import {unknown_algo_json.name}",
            cwd=temp_dir,
            expected_code=EXIT_PROFILE_UNKNOWN_ALGORITHM,
            check_stderr=[
                "Unsupported hash algorithm",
                "unknown123",
                "md5", "sha1", "sha256", "sha512",
            ],
            description="Exit code 12 for unknown hash algorithm"
        )

        dup_targets_json = temp_dir / "dup-targets.json"
        dup_targets_json.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [
                    {"path": "data/"},
                    {"path": "data/"},
                ],
            }
        }), encoding="utf-8")
        dup_target_config = temp_dir / "dup-test" / "backup-manifest.yaml"
        run_test(
            "Import duplicate targets",
            f"backup-checker profile import {dup_targets_json.name} -c {dup_target_config}",
            cwd=temp_dir,
            expected_code=EXIT_DUPLICATE_TARGET,
            check_stderr=["Duplicate target paths"],
            description="Exit code 3 for duplicate target paths"
        )

        missing_fields_json = temp_dir / "missing-fields.json"
        missing_fields_json.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
            }
        }), encoding="utf-8")
        run_test(
            "Import missing required fields",
            f"backup-checker profile import {missing_fields_json.name}",
            cwd=temp_dir,
            expected_code=EXIT_PROFILE_INVALID_JSON,
            check_stderr=["missing required field"],
            description="Exit code 10 for missing required fields"
        )

        run_test(
            "Import non-existent JSON file",
            "backup-checker profile import does-not-exist.json",
            cwd=temp_dir,
            expected_code=EXIT_PROFILE_INVALID_JSON,
            check_stderr=["JSON file not found"],
            description="Exit code 10 for missing JSON file"
        )

        run_test(
            "Show profile operation log",
            "backup-checker profile log -n 50",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=[
                "profile operations",
                "export",
                "import",
                "overwrite",
                "conflict",
            ],
            description="Profile log should show all operations"
        )

        log_dir = temp_dir / ".backup-profiles"
        log_file = log_dir / "profile-operations.log"
        assert log_file.exists(), "Profile log file not created"

        log_lines = log_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(log_lines) >= 3, f"Expected at least 3 log entries, got {len(log_lines)}"

        operations_found = set()
        status_found = set()
        for line in log_lines:
            if line.strip():
                entry = json.loads(line)
                operations_found.add(entry["operation"])
                status_found.add(entry["status"])

        assert "export" in operations_found
        assert "overwrite" in operations_found
        assert "conflict" in status_found
        assert "overwritten" in status_found
        assert "success" in status_found
        print("VERIFIED: Log file contains all operation types")
        print()

        fresh_dir = temp_dir / "fresh_import"
        fresh_dir.mkdir()
        fresh_config = fresh_dir / "backup-manifest.yaml"

        good_profile = temp_dir / "good-profile.json"
        good_profile.write_text(json.dumps({
            "profile_version": "1.0",
            "exported_at": "2024-01-01T00:00:00",
            "manifest": {
                "name": "scan-test",
                "source_dir": "../source",
                "backup_dir": "../backup",
                "targets": [
                    {"path": "documents/", "description": "Documents"},
                ],
                "exclude_patterns": ["*.tmp"],
                "hash_algorithm": "sha256",
                "retention_days": 30,
            }
        }), encoding="utf-8")

        run_test(
            "Import profile for scan test",
            f"backup-checker profile import {good_profile} -c {fresh_config}",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] Profile imported to:"],
            description="Import a valid profile for subsequent scan test"
        )

        run_test(
            "Scan works with imported config",
            f"backup-checker scan -c {fresh_config} --no-save --no-compare-history",
            cwd=temp_dir,
            expected_code=5,
            check_stdout=["Scanning source", "Scanning backup", "[MISS]", "[CORR]"],
            description="Imported config should work with scan command"
        )

        run_test(
            "Report works with imported config",
            f"backup-checker report -c {fresh_config} -f json -o {temp_dir}/imported-report.json --history none 2>/dev/null || backup-checker report -c {fresh_config} -f json -o {temp_dir}/imported-report.json",
            cwd=temp_dir,
            expected_code=EXIT_SUCCESS,
            check_stdout=["[OK] JSON report exported to:"],
            description="Imported config should work with report command"
        )

        run_test(
            "Drill works with imported config",
            f"backup-checker drill -c {fresh_config} --keep-restore",
            cwd=temp_dir,
            expected_code=5,
            check_stderr=["missing"],
            description="Imported config should work with drill command"
        )

        print("VERIFIED: All existing commands (scan/report/drill) work with imported config")
        print()

        restore_target = temp_dir / "restore-test.yaml"
        shutil.copy2(backup_path, restore_target)
        with open(restore_target, "r", encoding="utf-8") as f:
            restored_data = yaml.safe_load(f)
        assert restored_data["manifest"]["source_dir"] == "source"
        assert restored_data["manifest"]["hash_algorithm"] == "sha256"
        print("VERIFIED: Rollback backup can be used to restore original config")
        print()

        print("=" * 70)
        print("ALL PROFILE END-TO-END TESTS PASSED!")
        print("=" * 70)
        print()
        print("SUMMARY OF VERIFICATIONS:")
        print("  ✓ Export config to JSON works correctly")
        print("  ✓ Import JSON to new config works correctly")
        print("  ✓ Conflict detection rejects import without --force")
        print("  ✓ --force overwrites and creates rollback backup")
        print("  ✓ Invalid JSON returns exit code 10")
        print("  ✓ Unknown algorithm returns exit code 12")
        print("  ✓ Duplicate targets returns exit code 3")
        print("  ✓ Missing required fields returns exit code 10")
        print("  ✓ Missing JSON file returns exit code 10")
        print("  ✓ Operation logs are written to .backup-profiles/")
        print("  ✓ Log shows export, import, overwrite, conflict operations")
        print("  ✓ Imported config works with scan/report/drill")
        print("  ✓ Rollback backup can restore original config")
        print()

        return 0

    finally:
        cleanup_test_directory(temp_dir)
        print("Temporary test directory cleaned up.")


if __name__ == "__main__":
    sys.exit(main())
