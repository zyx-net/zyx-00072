"""Unit tests for doctor diagnostic functionality."""

import sys
import os
import json
import yaml
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

import pytest

from backup_checker.doctor import (
    run_doctor,
    DoctorError,
    CheckItem,
    DoctorResult,
    format_console_report,
    format_json_report,
    _check_path_exists,
    _check_path_readable,
    _check_path_writable,
    _check_hash_algorithm,
    _check_targets,
    _check_exclude_patterns,
    _check_history_dir,
    _check_profile_log_dir,
    _check_source_backup_overlap,
    CHECK_OK,
    CHECK_WARN,
    CHECK_ERROR,
)
from backup_checker.config import ManifestConfig, TargetConfig
from backup_checker.constants import (
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


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp = Path(tempfile.mkdtemp(prefix="doctor_test_"))
    yield temp
    shutil.rmtree(temp, ignore_errors=True)


@pytest.fixture
def sample_dirs(temp_dir):
    """Create sample source and backup directories."""
    source_dir = temp_dir / "source"
    backup_dir = temp_dir / "backup"
    source_dir.mkdir()
    backup_dir.mkdir()

    (source_dir / "documents").mkdir()
    (source_dir / "database").mkdir()
    (backup_dir / "documents").mkdir()
    (backup_dir / "database").mkdir()

    (source_dir / "documents" / "test.txt").write_text("test", encoding="utf-8")
    (backup_dir / "documents" / "test.txt").write_text("test", encoding="utf-8")

    return {"source": source_dir, "backup": backup_dir}


@pytest.fixture
def sample_config(temp_dir, sample_dirs):
    """Create a sample backup-manifest.yaml."""
    config_path = temp_dir / "backup-manifest.yaml"
    config_data = {
        "manifest": {
            "name": "test-backup",
            "source_dir": str(sample_dirs["source"].name),
            "backup_dir": str(sample_dirs["backup"].name),
            "targets": [
                {"path": "documents/", "description": "Important documents"},
                {"path": "database/", "description": "Database backups"},
            ],
            "retention_days": 30,
            "exclude_patterns": ["*.tmp", "*.log", "*.swp"],
            "hash_algorithm": "sha256",
        }
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
    return config_path


@pytest.fixture
def manifest_config(sample_config, sample_dirs):
    """Create a ManifestConfig instance."""
    return ManifestConfig(
        name="test-backup",
        source_dir=str(sample_dirs["source"]),
        backup_dir=str(sample_dirs["backup"]),
        targets=[
            TargetConfig(path="documents/", description="Important documents"),
            TargetConfig(path="database/", description="Database backups"),
        ],
        retention_days=30,
        exclude_patterns=["*.tmp", "*.log"],
        hash_algorithm="sha256",
        config_path=str(sample_config),
    )


class TestCheckItem:
    def test_check_item_to_dict(self):
        item = CheckItem(
            name="test.check",
            status=CHECK_OK,
            message="Test passed",
            details={"key": "value"},
            fixable=True,
        )
        d = item.to_dict()
        assert d["name"] == "test.check"
        assert d["status"] == CHECK_OK
        assert d["message"] == "Test passed"
        assert d["details"] == {"key": "value"}
        assert d["fixable"] is True


class TestDoctorResult:
    def test_summary(self):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path="/path/to/config.yaml",
            checks=[
                CheckItem(name="a", status=CHECK_OK, message="ok"),
                CheckItem(name="b", status=CHECK_OK, message="ok"),
                CheckItem(name="c", status=CHECK_WARN, message="warn"),
                CheckItem(name="d", status=CHECK_ERROR, message="err"),
            ],
        )
        summary = result.summary()
        assert summary[CHECK_OK] == 2
        assert summary[CHECK_WARN] == 1
        assert summary[CHECK_ERROR] == 1

    def test_exit_code_success(self):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path="/path/to/config.yaml",
            checks=[CheckItem(name="a", status=CHECK_OK, message="ok")],
        )
        assert result.exit_code() == EXIT_SUCCESS

    def test_exit_code_warning(self):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path="/path/to/config.yaml",
            checks=[
                CheckItem(name="a", status=CHECK_OK, message="ok"),
                CheckItem(name="b", status=CHECK_WARN, message="warn"),
            ],
        )
        assert result.exit_code() == EXIT_DOCTOR_WARNING

    def test_exit_code_error(self):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path="/path/to/config.yaml",
            checks=[
                CheckItem(name="a", status=CHECK_OK, message="ok"),
                CheckItem(name="b", status=CHECK_ERROR, message="err"),
            ],
        )
        assert result.exit_code() == EXIT_DOCTOR_ERROR

    def test_by_status(self):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path="/path/to/config.yaml",
            checks=[
                CheckItem(name="a", status=CHECK_OK, message="ok"),
                CheckItem(name="b", status=CHECK_WARN, message="warn"),
                CheckItem(name="c", status=CHECK_ERROR, message="err"),
            ],
        )
        grouped = result.by_status()
        assert len(grouped[CHECK_OK]) == 1
        assert len(grouped[CHECK_WARN]) == 1
        assert len(grouped[CHECK_ERROR]) == 1

    def test_to_dict(self, temp_dir):
        config_path = temp_dir / "config.yaml"
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(config_path),
            checks=[CheckItem(name="a", status=CHECK_OK, message="ok", details={"x": 1})],
            fixes_applied=["Fixed something"],
        )
        d = result.to_dict()
        assert d["timestamp"] == "2024-01-01T00:00:00"
        assert d["config_path"] == str(config_path.resolve())
        assert d["fixes_applied"] == ["Fixed something"]
        assert len(d["checks"]) == 1


class TestCheckPathExists:
    def test_path_exists(self, temp_dir):
        check = _check_path_exists(str(temp_dir), "test_dir")
        assert check.status == CHECK_OK
        assert "exists" in check.message

    def test_path_not_exists(self, temp_dir):
        missing = temp_dir / "missing"
        check = _check_path_exists(str(missing), "missing_dir")
        assert check.status == CHECK_ERROR
        assert "does not exist" in check.message

    def test_empty_path(self):
        check = _check_path_exists("", "empty_path")
        assert check.status == CHECK_ERROR
        assert "empty" in check.message

    def test_fixable_flag(self):
        check = _check_path_exists("/nonexistent", "test", fixable=True)
        assert check.fixable is True


class TestCheckPathReadable:
    def test_readable(self, temp_dir):
        check = _check_path_readable(str(temp_dir), "test_dir")
        assert check.status == CHECK_OK

    def test_not_exists(self, temp_dir):
        missing = temp_dir / "missing"
        check = _check_path_readable(str(missing), "missing_dir")
        assert check.status == CHECK_ERROR

    def test_permission_denied(self, temp_dir):
        test_file = temp_dir / "test.txt"
        test_file.write_text("test", encoding="utf-8")

        original_open = open

        def mock_open(path, mode="r", *args, **kwargs):
            if path == str(test_file) and "r" in mode:
                raise PermissionError("Permission denied")
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            check = _check_path_readable(str(test_file), "test_file")
            assert check.status == CHECK_ERROR
            assert "Permission denied" in check.message


class TestCheckPathWritable:
    def test_writable_dir(self, temp_dir):
        check = _check_path_writable(str(temp_dir), "test_dir")
        assert check.status == CHECK_OK

    def test_not_exists(self, temp_dir):
        missing = temp_dir / "missing"
        check = _check_path_writable(str(missing), "missing_dir")
        assert check.status == CHECK_ERROR

    def test_permission_denied(self, temp_dir):
        original_makedirs = os.makedirs
        original_open = open

        def mock_open(path, mode="r", *args, **kwargs):
            if "w" in mode and str(temp_dir) in str(path):
                raise PermissionError("Permission denied")
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            check = _check_path_writable(str(temp_dir), "test_dir")
            assert check.status == CHECK_ERROR
            assert "Permission denied" in check.message


class TestCheckHashAlgorithm:
    def test_supported_algorithms(self):
        for algo in SUPPORTED_HASH_ALGORITHMS:
            check = _check_hash_algorithm(algo)
            assert check.status == CHECK_OK
            assert algo in check.message

    def test_unsupported_algorithm(self):
        check = _check_hash_algorithm("unknown123")
        assert check.status == CHECK_ERROR
        assert "Unsupported" in check.message
        assert "unknown123" in check.message


class TestCheckTargets:
    def test_valid_targets(self, sample_dirs):
        targets = [
            TargetConfig(path="documents/", description="Docs"),
            TargetConfig(path="database/", description="DB"),
        ]
        checks = _check_targets(targets, str(sample_dirs["source"]), str(sample_dirs["backup"]))
        assert any(c.status == CHECK_OK and "All" in c.message for c in checks)

    def test_duplicate_targets(self, sample_dirs):
        targets = [
            TargetConfig(path="documents/", description="Docs"),
            TargetConfig(path="documents/", description="Docs again"),
        ]
        checks = _check_targets(targets, str(sample_dirs["source"]), str(sample_dirs["backup"]))
        assert any(c.status == CHECK_ERROR and "duplicate" in c.message.lower() for c in checks)

    def test_empty_target_path(self, sample_dirs):
        targets = [TargetConfig(path="", description="Empty")]
        checks = _check_targets(targets, str(sample_dirs["source"]), str(sample_dirs["backup"]))
        assert any(c.status == CHECK_ERROR and "empty" in c.message.lower() for c in checks)

    def test_path_traversal(self, sample_dirs):
        targets = [TargetConfig(path="../outside/", description="Traversal")]
        checks = _check_targets(targets, str(sample_dirs["source"]), str(sample_dirs["backup"]))
        assert any(c.status == CHECK_ERROR and "path traversal" in c.message.lower() for c in checks)

    def test_overlap_source_dir(self, sample_dirs):
        targets = [TargetConfig(path="./", description="Root")]
        checks = _check_targets(targets, str(sample_dirs["source"]), str(sample_dirs["backup"]))
        assert any(c.status == CHECK_WARN and "matches entire source_dir" in c.message for c in checks)


class TestCheckExcludePatterns:
    def test_no_patterns(self):
        check = _check_exclude_patterns([])
        assert check.status == CHECK_OK
        assert "No exclude patterns" in check.message

    def test_valid_patterns(self):
        check = _check_exclude_patterns(["*.tmp", "*.log", "*.swp"])
        assert check.status == CHECK_OK
        assert "All" in check.message
        assert "3" in check.message

    def test_empty_patterns_none(self):
        check = _check_exclude_patterns([])
        assert check.details["count"] == 0


class TestCheckHistoryDir:
    def test_exists(self, temp_dir, manifest_config):
        history_dir = temp_dir / HISTORY_DIRNAME
        history_dir.mkdir()
        check = _check_history_dir(manifest_config)
        assert check.status == CHECK_OK
        assert check.fixable is False

    def test_not_exists(self, temp_dir, manifest_config):
        check = _check_history_dir(manifest_config)
        assert check.status == CHECK_WARN
        assert check.fixable is True
        assert "does not exist" in check.message


class TestCheckProfileLogDir:
    def test_exists(self, temp_dir, manifest_config):
        profile_dir = temp_dir / PROFILE_LOG_DIRNAME
        profile_dir.mkdir()
        check = _check_profile_log_dir(manifest_config)
        assert check.status == CHECK_OK

    def test_not_exists(self, temp_dir, manifest_config):
        check = _check_profile_log_dir(manifest_config)
        assert check.status == CHECK_WARN
        assert check.fixable is True


class TestCheckSourceBackupOverlap:
    def test_properly_separated(self, sample_dirs):
        check = _check_source_backup_overlap(
            str(sample_dirs["source"]),
            str(sample_dirs["backup"]),
        )
        assert check.status == CHECK_OK
        assert "properly separated" in check.message

    def test_same_directory(self, temp_dir):
        check = _check_source_backup_overlap(str(temp_dir), str(temp_dir))
        assert check.status == CHECK_ERROR
        assert "same" in check.message

    def test_backup_inside_source(self, temp_dir):
        source = temp_dir / "source"
        backup = temp_dir / "source" / "backup"
        source.mkdir()
        backup.mkdir()
        check = _check_source_backup_overlap(str(source), str(backup))
        assert check.status == CHECK_ERROR
        assert "inside source_dir" in check.message

    def test_source_inside_backup(self, temp_dir):
        backup = temp_dir / "backup"
        source = temp_dir / "backup" / "source"
        backup.mkdir()
        source.mkdir()
        check = _check_source_backup_overlap(str(source), str(backup))
        assert check.status == CHECK_ERROR
        assert "inside backup_dir" in check.message


class TestRunDoctor:
    def test_normal_check(self, temp_dir, sample_config):
        result = run_doctor(str(sample_config), apply_fixes=False)
        assert isinstance(result, DoctorResult)
        summary = result.summary()
        assert summary[CHECK_ERROR] == 0
        assert summary[CHECK_WARN] >= 2

    def test_with_fix_creates_dirs(self, temp_dir, sample_config):
        history_dir = temp_dir / HISTORY_DIRNAME
        profile_dir = temp_dir / PROFILE_LOG_DIRNAME

        assert not history_dir.exists()
        assert not profile_dir.exists()

        result = run_doctor(str(sample_config), apply_fixes=True)

        assert history_dir.exists()
        assert profile_dir.exists()
        assert len(result.fixes_applied) == 2

    def test_bad_yaml(self, temp_dir):
        bad_yaml = temp_dir / "bad.yaml"
        bad_yaml.write_text("this is: not: valid: yaml: [", encoding="utf-8")

        with pytest.raises(DoctorError) as exc:
            run_doctor(str(bad_yaml))
        assert exc.value.exit_code == EXIT_CONFIG_ERROR
        assert "YAML" in exc.value.message

    def test_duplicate_targets(self, temp_dir, sample_dirs):
        config_path = temp_dir / "dup.yaml"
        config_data = {
            "manifest": {
                "name": "test",
                "source_dir": str(sample_dirs["source"].name),
                "backup_dir": str(sample_dirs["backup"].name),
                "targets": [
                    {"path": "documents/"},
                    {"path": "documents/"},
                ],
                "hash_algorithm": "sha256",
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        with pytest.raises(DoctorError) as exc:
            run_doctor(str(config_path))
        assert exc.value.exit_code == EXIT_DOCTOR_DUPLICATE_TARGET
        assert "Duplicate" in exc.value.message

    def test_unknown_algorithm(self, temp_dir, sample_dirs):
        config_path = temp_dir / "unknown-algo.yaml"
        config_data = {
            "manifest": {
                "name": "test",
                "source_dir": str(sample_dirs["source"].name),
                "backup_dir": str(sample_dirs["backup"].name),
                "targets": [{"path": "documents/"}],
                "hash_algorithm": "unknown123",
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        with pytest.raises(DoctorError) as exc:
            run_doctor(str(config_path))
        assert exc.value.exit_code == EXIT_DOCTOR_UNKNOWN_ALGORITHM
        assert "Unsupported hash algorithm" in exc.value.message

    def test_config_not_found(self, temp_dir):
        with pytest.raises(DoctorError) as exc:
            run_doctor(str(temp_dir / "nonexistent.yaml"))
        assert exc.value.exit_code == EXIT_CONFIG_ERROR

    def test_missing_source_dir(self, temp_dir):
        config_path = temp_dir / "missing-source.yaml"
        config_data = {
            "manifest": {
                "name": "test",
                "source_dir": "nonexistent_source",
                "backup_dir": "backup",
                "targets": [{"path": "documents/"}],
                "hash_algorithm": "sha256",
            }
        }
        (temp_dir / "backup").mkdir()
        (temp_dir / "backup" / "documents").mkdir()
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        result = run_doctor(str(config_path))
        summary = result.summary()
        assert summary[CHECK_ERROR] >= 1
        source_checks = [c for c in result.checks if "source_dir" in c.name and c.status == CHECK_ERROR]
        assert len(source_checks) >= 1

    def test_fix_permission_denied(self, temp_dir, sample_config):
        original_makedirs = os.makedirs

        def mock_makedirs(path, exist_ok=False):
            if HISTORY_DIRNAME in str(path) or PROFILE_LOG_DIRNAME in str(path):
                raise PermissionError("Permission denied")
            return original_makedirs(path, exist_ok=exist_ok)

        with patch("os.makedirs", side_effect=mock_makedirs):
            with pytest.raises(DoctorError) as exc:
                run_doctor(str(sample_config), apply_fixes=True)
            assert exc.value.exit_code == EXIT_DOCTOR_PERMISSION
            assert "Permission denied" in exc.value.message

    def test_log_written_to_profile(self, temp_dir, sample_config):
        from backup_checker.profile import read_operation_logs

        result = run_doctor(str(sample_config), apply_fixes=False)

        logs = read_operation_logs(str(sample_config))
        doctor_logs = [l for l in logs if l.operation == "doctor"]
        assert len(doctor_logs) >= 1
        assert doctor_logs[0].status in ["success", "warning", "error"]
        assert "OK=" in doctor_logs[0].details


class TestFormatConsoleReport:
    def test_format_success(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[
                CheckItem(name="config.load", status=CHECK_OK, message="Config loaded"),
                CheckItem(name="source_dir", status=CHECK_OK, message="Source exists"),
            ],
        )
        report = format_console_report(result)
        assert "BACKUP CHECKER DOCTOR REPORT" in report
        assert "PASSED - All checks OK" in report

    def test_format_with_warnings(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[
                CheckItem(name="config.load", status=CHECK_OK, message="Config loaded"),
                CheckItem(name="history_dir", status=CHECK_WARN, message="Missing", fixable=True),
            ],
        )
        report = format_console_report(result)
        assert "WARN" in report
        assert "fixable with --fix" in report
        assert "PASSED WITH WARNINGS" in report

    def test_format_with_errors(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[
                CheckItem(name="config.load", status=CHECK_OK, message="Config loaded"),
                CheckItem(name="source_dir", status=CHECK_ERROR, message="Does not exist"),
            ],
        )
        report = format_console_report(result)
        assert "ERR" in report
        assert "FAILED" in report

    def test_format_with_fixes(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[CheckItem(name="config.load", status=CHECK_OK, message="Config loaded")],
            fixes_applied=["Created history dir", "Created profile dir"],
        )
        report = format_console_report(result)
        assert "FIXES APPLIED" in report
        assert "Created history dir" in report


class TestFormatJsonReport:
    def test_json_is_stable(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[
                CheckItem(
                    name="z_check",
                    status=CHECK_OK,
                    message="Z",
                    details={"b": 2, "a": 1},
                ),
                CheckItem(
                    name="a_check",
                    status=CHECK_ERROR,
                    message="A",
                    details={"c": 3},
                ),
            ],
            fixes_applied=["fix1", "fix2"],
        )

        json1 = format_json_report(result)
        json2 = format_json_report(result)

        assert json1 == json2

        data = json.loads(json1)
        assert "checks" in data
        assert "summary" in data
        assert "config_path" in data
        assert "timestamp" in data

    def test_json_sort_keys(self, temp_dir):
        result = DoctorResult(
            timestamp="2024-01-01T00:00:00",
            config_path=str(temp_dir / "config.yaml"),
            checks=[],
        )
        json_str = format_json_report(result)
        keys = list(json.loads(json_str).keys())
        assert keys == sorted(keys)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
