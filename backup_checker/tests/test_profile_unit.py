"""Unit tests for profile export/import functionality."""

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

from backup_checker.profile import (
    export_profile,
    import_profile,
    read_operation_logs,
    ProfileError,
    ProfileConflictError,
    ProfileInvalidJsonError,
    ProfilePermissionError,
    ProfileUnknownAlgorithmError,
    ProfileInvalidConfigError,
    _validate_import_data,
    _detect_conflicts,
    _format_conflict_message,
)
from backup_checker.constants import (
    EXIT_PROFILE_CONFLICT,
    EXIT_PROFILE_INVALID_JSON,
    EXIT_PROFILE_PERMISSION_DENIED,
    EXIT_PROFILE_UNKNOWN_ALGORITHM,
    EXIT_PROFILE_INVALID_CONFIG,
    EXIT_DUPLICATE_TARGET,
    PROFILE_LOG_DIRNAME,
    PROFILE_LOG_FILENAME,
)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp = Path(tempfile.mkdtemp(prefix="profile_test_"))
    yield temp
    shutil.rmtree(temp, ignore_errors=True)


@pytest.fixture
def sample_config(temp_dir):
    """Create a sample backup-manifest.yaml."""
    config_path = temp_dir / "backup-manifest.yaml"
    config_data = {
        "manifest": {
            "name": "test-backup",
            "source_dir": "source",
            "backup_dir": "backup",
            "targets": [
                {"path": "documents/", "description": "Important documents"},
                {"path": "photos/", "description": "Personal photos"},
            ],
            "retention_days": 30,
            "exclude_patterns": ["*.tmp", "*.log"],
            "hash_algorithm": "sha256",
        }
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, sort_keys=False)
    return config_path


@pytest.fixture
def sample_json_profile(temp_dir):
    """Create a sample JSON profile file."""
    json_path = temp_dir / "profile.json"
    profile_data = {
        "profile_version": "1.0",
        "exported_at": "2024-01-01T00:00:00",
        "source_config": "/path/to/config",
        "manifest": {
            "name": "imported-backup",
            "source_dir": "new_source",
            "backup_dir": "new_backup",
            "targets": [
                {"path": "data/", "description": "Data files"},
                {"path": "configs/", "description": "Configuration files"},
            ],
            "retention_days": 60,
            "exclude_patterns": ["*.bak", "*.temp"],
            "hash_algorithm": "sha512",
        },
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(profile_data, f, indent=2)
    return json_path


class TestExportProfile:
    def test_export_success(self, temp_dir, sample_config):
        output_path = temp_dir / "exported.json"
        result = export_profile(str(sample_config), str(output_path))

        assert os.path.exists(result)
        assert result == str(output_path)

        with open(result, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["profile_version"] == "1.0"
        assert "exported_at" in data
        assert data["source_config"] == str(sample_config.resolve())
        assert data["manifest"]["name"] == "test-backup"
        assert data["manifest"]["source_dir"] == "source"
        assert data["manifest"]["backup_dir"] == "backup"
        assert len(data["manifest"]["targets"]) == 2
        assert data["manifest"]["targets"][0]["path"] == "documents/"
        assert data["manifest"]["exclude_patterns"] == ["*.tmp", "*.log"]
        assert data["manifest"]["hash_algorithm"] == "sha256"

    def test_export_config_not_found(self, temp_dir):
        with pytest.raises(ProfileInvalidConfigError) as exc:
            export_profile(str(temp_dir / "nonexistent.yaml"), str(temp_dir / "out.json"))
        assert "Config file not found" in exc.value.message
        assert exc.value.exit_code == EXIT_PROFILE_INVALID_CONFIG

    def test_export_invalid_yaml(self, temp_dir):
        bad_yaml = temp_dir / "bad.yaml"
        bad_yaml.write_text("this is: not: valid: yaml:", encoding="utf-8")
        with pytest.raises(ProfileInvalidConfigError) as exc:
            export_profile(str(bad_yaml), str(temp_dir / "out.json"))
        assert "Invalid YAML format" in exc.value.message

    def test_export_missing_manifest(self, temp_dir):
        bad_yaml = temp_dir / "bad.yaml"
        bad_yaml.write_text("not_manifest: {}", encoding="utf-8")
        with pytest.raises(ProfileInvalidConfigError) as exc:
            export_profile(str(bad_yaml), str(temp_dir / "out.json"))
        assert "missing 'manifest' section" in exc.value.message

    def test_export_permission_denied(self, temp_dir, sample_config):
        output_path = temp_dir / "exported.json"
        original_open = open

        def mock_open(path, mode="r", *args, **kwargs):
            if path == str(output_path) and "w" in mode:
                raise PermissionError()
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            with pytest.raises(ProfilePermissionError) as exc:
                export_profile(str(sample_config), str(output_path))
            assert "Permission denied" in exc.value.message
            assert exc.value.exit_code == EXIT_PROFILE_PERMISSION_DENIED

    def test_export_creates_output_directory(self, temp_dir, sample_config):
        nested_output = temp_dir / "nested" / "dir" / "exported.json"
        result = export_profile(str(sample_config), str(nested_output))
        assert os.path.exists(result)


class TestImportProfile:
    def test_import_new_config(self, temp_dir, sample_json_profile):
        target_config = temp_dir / "backup-manifest.yaml"
        assert not target_config.exists()

        result_path, backup_path = import_profile(str(sample_json_profile), str(target_config))

        assert result_path == str(target_config)
        assert backup_path is None
        assert target_config.exists()

        with open(target_config, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["manifest"]["name"] == "imported-backup"
        assert data["manifest"]["source_dir"] == "new_source"
        assert data["manifest"]["backup_dir"] == "new_backup"
        assert len(data["manifest"]["targets"]) == 2
        assert data["manifest"]["targets"][0]["path"] == "data/"
        assert data["manifest"]["exclude_patterns"] == ["*.bak", "*.temp"]
        assert data["manifest"]["hash_algorithm"] == "sha512"
        assert data["manifest"]["retention_days"] == 60

    def test_import_with_conflict_rejected(self, temp_dir, sample_config, sample_json_profile):
        with pytest.raises(ProfileConflictError) as exc:
            import_profile(str(sample_json_profile), str(sample_config), force=False)

        assert "Configuration conflicts detected" in exc.value.message
        assert exc.value.exit_code == EXIT_PROFILE_CONFLICT
        assert len(exc.value.conflicts) > 0
        assert "source_dir" in exc.value.conflicts
        assert "backup_dir" in exc.value.conflicts
        assert "targets" in exc.value.conflicts
        assert "hash_algorithm" in exc.value.conflicts
        assert "exclude_patterns" in exc.value.conflicts

    def test_import_with_force_overwrite(self, temp_dir, sample_config, sample_json_profile):
        original_content = sample_config.read_text(encoding="utf-8")

        result_path, backup_path = import_profile(
            str(sample_json_profile), str(sample_config), force=True
        )

        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert Path(backup_path).read_text(encoding="utf-8") == original_content

        with open(sample_config, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["manifest"]["source_dir"] == "new_source"
        assert data["manifest"]["hash_algorithm"] == "sha512"

    def test_import_json_not_found(self, temp_dir):
        with pytest.raises(ProfileInvalidJsonError) as exc:
            import_profile(
                str(temp_dir / "nonexistent.json"),
                str(temp_dir / "backup-manifest.yaml"),
            )
        assert "JSON file not found" in exc.value.message
        assert exc.value.exit_code == EXIT_PROFILE_INVALID_JSON

    def test_import_invalid_json(self, temp_dir):
        bad_json = temp_dir / "bad.json"
        bad_json.write_text("{this is not valid json", encoding="utf-8")
        with pytest.raises(ProfileInvalidJsonError) as exc:
            import_profile(str(bad_json), str(temp_dir / "backup-manifest.yaml"))
        assert "Invalid JSON format" in exc.value.message

    def test_import_unknown_algorithm(self, temp_dir):
        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
                "hash_algorithm": "unknown_algo",
            }
        }), encoding="utf-8")
        with pytest.raises(ProfileUnknownAlgorithmError) as exc:
            import_profile(str(bad_profile), str(temp_dir / "backup-manifest.yaml"))
        assert "Unsupported hash algorithm" in exc.value.message
        assert "unknown_algo" in exc.value.message
        assert exc.value.exit_code == EXIT_PROFILE_UNKNOWN_ALGORITHM

    def test_import_duplicate_targets(self, temp_dir):
        dup_profile = temp_dir / "dup.json"
        dup_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [
                    {"path": "data/"},
                    {"path": "data/"},
                ],
            }
        }), encoding="utf-8")
        with pytest.raises(ProfileError) as exc:
            import_profile(str(dup_profile), str(temp_dir / "backup-manifest.yaml"))
        assert exc.value.exit_code == EXIT_DUPLICATE_TARGET
        assert "Duplicate target paths" in exc.value.message

    def test_import_missing_required_fields(self, temp_dir):
        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
            }
        }), encoding="utf-8")
        with pytest.raises(ProfileInvalidJsonError) as exc:
            import_profile(str(bad_profile), str(temp_dir / "backup-manifest.yaml"))
        assert "missing required field" in exc.value.message

    def test_import_targets_not_array(self, temp_dir):
        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": "not_an_array",
            }
        }), encoding="utf-8")
        with pytest.raises(ProfileInvalidJsonError) as exc:
            import_profile(str(bad_profile), str(temp_dir / "backup-manifest.yaml"))
        assert "manifest.targets must be an array" in exc.value.message

    def test_import_rollback_on_validation_error(self, temp_dir, sample_config):
        original_content = sample_config.read_text(encoding="utf-8")

        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
            }
        }), encoding="utf-8")

        with pytest.raises(ProfileInvalidConfigError):
            import_profile(str(bad_profile), str(sample_config), force=True)

        assert sample_config.read_text(encoding="utf-8") == original_content

    def test_import_preserves_key_config_fields(self, temp_dir, sample_json_profile):
        target_config = temp_dir / "backup-manifest.yaml"
        import_profile(str(sample_json_profile), str(target_config))

        with open(target_config, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        manifest = data["manifest"]
        assert "targets" in manifest
        assert "exclude_patterns" in manifest
        assert "hash_algorithm" in manifest
        assert "source_dir" in manifest
        assert "backup_dir" in manifest


class TestValidateImportData:
    def test_valid_data(self):
        data = {
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
            }
        }
        _validate_import_data(data)

    def test_not_dict(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data("not a dict")

    def test_missing_manifest(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({})

    def test_manifest_not_dict(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({"manifest": "not a dict"})

    def test_missing_source_dir(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({
                "manifest": {"backup_dir": "bak", "targets": []}
            })

    def test_missing_backup_dir(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({
                "manifest": {"source_dir": "src", "targets": []}
            })

    def test_missing_targets(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({
                "manifest": {"source_dir": "src", "backup_dir": "bak"}
            })

    def test_targets_not_list(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({
                "manifest": {"source_dir": "src", "backup_dir": "bak", "targets": "not list"}
            })

    def test_unknown_algorithm(self):
        with pytest.raises(ProfileUnknownAlgorithmError):
            _validate_import_data({
                "manifest": {
                    "source_dir": "src",
                    "backup_dir": "bak",
                    "targets": [],
                    "hash_algorithm": "bad_algo",
                }
            })

    def test_exclude_patterns_not_list(self):
        with pytest.raises(ProfileInvalidJsonError):
            _validate_import_data({
                "manifest": {
                    "source_dir": "src",
                    "backup_dir": "bak",
                    "targets": [],
                    "exclude_patterns": "not list",
                }
            })


class TestDetectConflicts:
    def test_no_conflicts(self):
        existing = {
            "source_dir": "src",
            "backup_dir": "bak",
            "hash_algorithm": "sha256",
            "targets": [{"path": "data/"}],
            "exclude_patterns": ["*.tmp"],
        }
        incoming = {
            "source_dir": "src",
            "backup_dir": "bak",
            "hash_algorithm": "sha256",
            "targets": [{"path": "data/"}],
            "exclude_patterns": ["*.tmp"],
        }
        conflicts = _detect_conflicts(existing, incoming)
        assert len(conflicts) == 0

    def test_source_dir_conflict(self):
        existing = {"source_dir": "old_src"}
        incoming = {"source_dir": "new_src"}
        conflicts = _detect_conflicts(existing, incoming)
        assert "source_dir" in conflicts

    def test_backup_dir_conflict(self):
        existing = {"backup_dir": "old_bak"}
        incoming = {"backup_dir": "new_bak"}
        conflicts = _detect_conflicts(existing, incoming)
        assert "backup_dir" in conflicts

    def test_hash_algorithm_conflict(self):
        existing = {"hash_algorithm": "sha256"}
        incoming = {"hash_algorithm": "md5"}
        conflicts = _detect_conflicts(existing, incoming)
        assert "hash_algorithm" in conflicts

    def test_targets_conflict(self):
        existing = {"targets": [{"path": "data/"}]}
        incoming = {"targets": [{"path": "docs/"}, {"path": "data/"}]}
        conflicts = _detect_conflicts(existing, incoming)
        assert "targets" in conflicts

    def test_exclude_patterns_conflict(self):
        existing = {"exclude_patterns": ["*.tmp"]}
        incoming = {"exclude_patterns": ["*.log"]}
        conflicts = _detect_conflicts(existing, incoming)
        assert "exclude_patterns" in conflicts

    def test_multiple_conflicts(self):
        existing = {
            "source_dir": "src1",
            "backup_dir": "bak1",
            "hash_algorithm": "sha256",
        }
        incoming = {
            "source_dir": "src2",
            "backup_dir": "bak2",
            "hash_algorithm": "md5",
        }
        conflicts = _detect_conflicts(existing, incoming)
        assert len(conflicts) == 3


class TestFormatConflictMessage:
    def test_format_single_conflict(self):
        conflicts = {
            "source_dir": ("old_src", "new_src"),
        }
        msg = _format_conflict_message(conflicts)
        assert "Configuration conflicts detected" in msg
        assert "source_dir" in msg
        assert "existing: old_src" in msg
        assert "incoming: new_src" in msg

    def test_format_multiple_conflicts(self):
        conflicts = {
            "source_dir": ("old_src", "new_src"),
            "hash_algorithm": ("sha256", "md5"),
        }
        msg = _format_conflict_message(conflicts)
        assert "source_dir" in msg
        assert "hash_algorithm" in msg
        assert "--force" in msg


class TestOperationLogs:
    def test_log_written_on_export(self, temp_dir, sample_config):
        output_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(output_path))

        logs = read_operation_logs(str(sample_config))
        assert len(logs) >= 1

        export_logs = [l for l in logs if l.operation == "export"]
        assert len(export_logs) >= 1
        assert export_logs[0].status == "success"
        assert "Exported profile" in export_logs[0].details

    def test_log_written_on_import(self, temp_dir, sample_json_profile):
        target_config = temp_dir / "backup-manifest.yaml"
        import_profile(str(sample_json_profile), str(target_config))

        logs = read_operation_logs(str(target_config))
        assert len(logs) >= 1

        import_logs = [l for l in logs if l.operation == "import"]
        assert len(import_logs) >= 1
        assert import_logs[0].status == "imported"

    def test_log_written_on_overwrite(self, temp_dir, sample_config, sample_json_profile):
        import_profile(str(sample_json_profile), str(sample_config), force=True)

        logs = read_operation_logs(str(sample_config))
        overwrite_logs = [l for l in logs if l.operation == "overwrite"]
        assert len(overwrite_logs) >= 1
        assert overwrite_logs[0].status == "overwritten"
        assert overwrite_logs[0].backup_path is not None

    def test_log_written_on_conflict(self, temp_dir, sample_config, sample_json_profile):
        try:
            import_profile(str(sample_json_profile), str(sample_config), force=False)
        except ProfileConflictError:
            pass

        logs = read_operation_logs(str(sample_config))
        conflict_logs = [l for l in logs if l.status == "conflict"]
        assert len(conflict_logs) >= 1

    def test_log_written_on_rollback(self, temp_dir, sample_config):
        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
            }
        }), encoding="utf-8")

        try:
            import_profile(str(bad_profile), str(sample_config), force=True)
        except ProfileInvalidConfigError:
            pass

        logs = read_operation_logs(str(sample_config))
        rollback_logs = [l for l in logs if l.status == "rolled_back"]
        assert len(rollback_logs) >= 1
        assert rollback_logs[0].backup_path is not None

    def test_read_empty_logs(self, temp_dir):
        config_path = temp_dir / "backup-manifest.yaml"
        logs = read_operation_logs(str(config_path))
        assert len(logs) == 0

    def test_log_file_location(self, temp_dir, sample_config):
        output_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(output_path))

        log_dir = temp_dir / PROFILE_LOG_DIRNAME
        log_file = log_dir / PROFILE_LOG_FILENAME
        assert log_file.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
