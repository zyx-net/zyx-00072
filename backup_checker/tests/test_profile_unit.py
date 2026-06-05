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
    diff_profiles,
    format_diff_console,
    format_diff_json,
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
    FieldDiff,
    ProfileDiffResult,
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

        result_path, backup_path, _ = import_profile(str(sample_json_profile), str(target_config))

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

        result_path, backup_path, _ = import_profile(
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


class TestDiffProfiles:
    def test_diff_no_changes(self, temp_dir, sample_config):
        export_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(export_path))

        diff = diff_profiles(str(sample_config), str(export_path))
        assert not diff.has_changes
        assert not diff.has_conflicts
        assert len(diff.field_diffs) == 0
        assert diff.summary["total"] == 0

    def test_diff_all_fields_modified(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "exported_at": "2024-01-01T00:00:00",
            "source_config": "/path/to/config",
            "manifest": {
                "name": "modified-name",
                "source_dir": "new_source",
                "backup_dir": "new_backup",
                "targets": [
                    {"path": "new_target/", "description": "New target"},
                ],
                "retention_days": 60,
                "exclude_patterns": ["*.new"],
                "hash_algorithm": "md5",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        assert diff.has_changes
        assert diff.has_conflicts
        assert len(diff.field_diffs) == 7

        field_names = {fd.field for fd in diff.field_diffs}
        assert "name" in field_names
        assert "source_dir" in field_names
        assert "backup_dir" in field_names
        assert "targets" in field_names
        assert "retention_days" in field_names
        assert "exclude_patterns" in field_names
        assert "hash_algorithm" in field_names

        name_diff = next(fd for fd in diff.field_diffs if fd.field == "name")
        assert name_diff.change_type == "modify"
        assert name_diff.old_value == "test-backup"
        assert name_diff.new_value == "modified-name"

        targets_diff = next(fd for fd in diff.field_diffs if fd.field == "targets")
        assert len(targets_diff.added) == 1
        assert len(targets_diff.removed) == 2

    def test_diff_only_exclude_patterns(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "test-backup",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/", "description": "Important documents"},
                    {"path": "photos/", "description": "Personal photos"},
                ],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp", "*.bak"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        assert diff.has_changes
        assert len(diff.field_diffs) == 1
        assert diff.field_diffs[0].field == "exclude_patterns"
        assert diff.field_diffs[0].added == ["*.bak"]
        assert diff.field_diffs[0].removed == ["*.log"]

    def test_diff_only_targets(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "test-backup",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/", "description": "Important documents"},
                    {"path": "videos/", "description": "Video files"},
                ],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp", "*.log"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        assert diff.has_changes
        assert len(diff.field_diffs) == 1
        targets_diff = diff.field_diffs[0]
        assert targets_diff.field == "targets"
        assert len(targets_diff.added) == 1
        assert targets_diff.added[0]["path"] == "videos/"
        assert len(targets_diff.removed) == 1
        assert targets_diff.removed[0]["path"] == "photos/"

    def test_diff_config_not_found(self, temp_dir, sample_json_profile):
        with pytest.raises(ProfileInvalidConfigError):
            diff_profiles(str(temp_dir / "nonexistent.yaml"), str(sample_json_profile))

    def test_diff_json_not_found(self, temp_dir, sample_config):
        with pytest.raises(ProfileInvalidJsonError):
            diff_profiles(str(sample_config), str(temp_dir / "nonexistent.json"))

    def test_diff_invalid_json(self, temp_dir, sample_config):
        bad_json = temp_dir / "bad.json"
        bad_json.write_text("{this is not valid json", encoding="utf-8")
        with pytest.raises(ProfileInvalidJsonError):
            diff_profiles(str(sample_config), str(bad_json))

    def test_diff_unknown_algorithm(self, temp_dir, sample_config):
        bad_json = temp_dir / "bad.json"
        bad_json.write_text(json.dumps({
            "manifest": {
                "source_dir": "src",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
                "hash_algorithm": "unknown_algo",
            }
        }), encoding="utf-8")
        with pytest.raises(ProfileUnknownAlgorithmError):
            diff_profiles(str(sample_config), str(bad_json))


class TestDiffFormatters:
    def test_format_console_no_changes(self, temp_dir, sample_config):
        export_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(export_path))
        diff = diff_profiles(str(sample_config), str(export_path))
        output = format_diff_console(diff)
        assert "PROFILE DIFF COMPARISON" in output
        assert "No differences found" in output
        assert "[OK]" in output

    def test_format_console_with_changes(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "new-name",
                "source_dir": "new_src",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/", "description": "Important documents"},
                    {"path": "new_target/", "description": "New"},
                ],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp", "*.log", "*.new"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        output = format_diff_console(diff)
        assert "SUMMARY:" in output
        assert "[~] MOD" in output
        assert "[+] ADD" in output
        assert "清单名称" in output
        assert "源目录" in output
        assert "目标列表" in output
        assert "排除规则" in output

    def test_format_json_stable_parsable(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "new-name",
                "source_dir": "new_src",
                "backup_dir": "backup",
                "targets": [{"path": "data/"}],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        output = format_diff_json(diff)

        parsed = json.loads(output)
        assert "current_config" in parsed
        assert "incoming_file" in parsed
        assert "has_changes" in parsed
        assert "has_conflicts" in parsed
        assert "field_diffs" in parsed
        assert "summary" in parsed
        assert isinstance(parsed["field_diffs"], list)
        assert isinstance(parsed["summary"], dict)

        second_output = format_diff_json(diff)
        assert output == second_output

    def test_format_json_no_changes(self, temp_dir, sample_config):
        export_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(export_path))
        diff = diff_profiles(str(sample_config), str(export_path))
        output = format_diff_json(diff)

        parsed = json.loads(output)
        assert parsed["has_changes"] is False
        assert parsed["has_conflicts"] is False
        assert len(parsed["field_diffs"]) == 0


class TestImportDryRun:
    def test_dry_run_no_conflicts(self, temp_dir, sample_json_profile):
        target_config = temp_dir / "backup-manifest.yaml"
        result_path, backup_path, diff = import_profile(
            str(sample_json_profile), str(target_config), force=False, dry_run=True
        )

        assert result_path is None
        assert backup_path is None
        assert diff is not None
        assert not target_config.exists()

    def test_dry_run_with_conflicts(self, temp_dir, sample_config, sample_json_profile):
        original_content = sample_config.read_text(encoding="utf-8")

        with pytest.raises(ProfileConflictError):
            import_profile(
                str(sample_json_profile), str(sample_config), force=False, dry_run=True
            )

        assert sample_config.read_text(encoding="utf-8") == original_content

    def test_dry_run_force_with_conflicts(self, temp_dir, sample_config, sample_json_profile):
        original_content = sample_config.read_text(encoding="utf-8")

        result_path, backup_path, diff = import_profile(
            str(sample_json_profile), str(sample_config), force=True, dry_run=True
        )

        assert result_path is None
        assert backup_path is None
        assert diff is not None
        assert diff.has_conflicts
        assert sample_config.read_text(encoding="utf-8") == original_content

    def test_dry_run_permission_check(self, temp_dir, sample_json_profile):
        import stat
        read_only_dir = temp_dir / "readonly"
        read_only_dir.mkdir()
        read_only_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

        target_config = read_only_dir / "backup-manifest.yaml"

        original_access = os.access
        try:
            def mock_access(path, mode):
                if path == str(read_only_dir) and mode == os.W_OK:
                    return False
                return original_access(path, mode)

            with patch("os.access", side_effect=mock_access):
                with pytest.raises(ProfilePermissionError):
                    import_profile(
                        str(sample_json_profile), str(target_config), force=False, dry_run=True
                    )
        finally:
            os.chmod(str(read_only_dir), stat.S_IRWXU)

    def test_dry_run_logs_operation(self, temp_dir, sample_config, sample_json_profile):
        try:
            import_profile(
                str(sample_json_profile), str(sample_config), force=False, dry_run=True
            )
        except ProfileConflictError:
            pass

        logs = read_operation_logs(str(sample_config))
        dry_run_logs = [l for l in logs if l.operation == "dry_run"]
        assert len(dry_run_logs) >= 1

    def test_dry_run_validates_config(self, temp_dir, sample_config):
        bad_profile = temp_dir / "bad.json"
        bad_profile.write_text(json.dumps({
            "manifest": {
                "source_dir": "",
                "backup_dir": "bak",
                "targets": [{"path": "data/"}],
            }
        }), encoding="utf-8")

        with pytest.raises(ProfileInvalidConfigError):
            import_profile(str(bad_profile), str(sample_config), force=True, dry_run=True)

    def test_dry_run_detects_duplicate_targets(self, temp_dir, sample_config):
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
            import_profile(str(dup_profile), str(sample_config), force=True, dry_run=True)
        assert exc.value.exit_code == EXIT_DUPLICATE_TARGET


class TestImportConsistency:
    def test_import_reload_consistent(self, temp_dir, sample_json_profile):
        target_config = temp_dir / "backup-manifest.yaml"
        result_path, backup_path, _ = import_profile(
            str(sample_json_profile), str(target_config)
        )

        from backup_checker.config import load_config
        loaded_config = load_config(result_path)

        with open(str(sample_json_profile), "r", encoding="utf-8") as f:
            imported_data = json.load(f)
        manifest = imported_data["manifest"]

        assert loaded_config.name == manifest.get("name")
        assert loaded_config.hash_algorithm == manifest.get("hash_algorithm")
        assert loaded_config.retention_days == manifest.get("retention_days")
        assert loaded_config.exclude_patterns == manifest.get("exclude_patterns", [])

        loaded_target_paths = [t.path for t in loaded_config.targets]
        imported_target_paths = [t["path"] for t in manifest.get("targets", [])]
        assert loaded_target_paths == imported_target_paths

    def test_import_force_reload_consistent(self, temp_dir, sample_config, sample_json_profile):
        result_path, backup_path, _ = import_profile(
            str(sample_json_profile), str(sample_config), force=True
        )

        from backup_checker.config import load_config
        loaded_config = load_config(result_path)

        with open(str(sample_json_profile), "r", encoding="utf-8") as f:
            imported_data = json.load(f)
        manifest = imported_data["manifest"]

        assert loaded_config.source_dir.endswith(manifest.get("source_dir")) or \
               loaded_config.source_dir == manifest.get("source_dir") or \
               manifest.get("source_dir") in loaded_config.source_dir

        assert loaded_config.hash_algorithm == manifest.get("hash_algorithm")

    def test_rollback_backup_contains_original(self, temp_dir, sample_config, sample_json_profile):
        original_content = sample_config.read_text(encoding="utf-8")

        result_path, backup_path, _ = import_profile(
            str(sample_json_profile), str(sample_config), force=True
        )

        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert Path(backup_path).read_text(encoding="utf-8") == original_content

        from backup_checker.config import load_config
        backup_config = load_config(backup_path)
        assert backup_config.name == "test-backup"
        assert backup_config.hash_algorithm == "sha256"


class TestDiffJsonOutput:
    def test_json_output_has_expected_keys(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "new-name",
                "source_dir": "new_src",
                "backup_dir": "new_backup",
                "targets": [{"path": "data/"}],
                "retention_days": 60,
                "exclude_patterns": ["*.new"],
                "hash_algorithm": "md5",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        output = format_diff_json(diff)
        parsed = json.loads(output)

        assert "current_config" in parsed
        assert "incoming_file" in parsed
        assert "has_changes" in parsed
        assert "has_conflicts" in parsed
        assert "field_diffs" in parsed
        assert "summary" in parsed

        for fd in parsed["field_diffs"]:
            assert "field" in fd
            assert "display_name" in fd
            assert "change_type" in fd
            assert "old_value" in fd
            assert "new_value" in fd
            assert "added" in fd
            assert "removed" in fd

        for key in ["added", "removed", "modified", "total"]:
            assert key in parsed["summary"]

    def test_json_output_targets_details(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "test-backup",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/", "description": "Important documents"},
                    {"path": "videos/", "description": "Videos"},
                ],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp", "*.log"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        output = format_diff_json(diff)
        parsed = json.loads(output)

        targets_diff = next(fd for fd in parsed["field_diffs"] if fd["field"] == "targets")
        assert len(targets_diff["added"]) == 1
        assert targets_diff["added"][0]["path"] == "videos/"
        assert len(targets_diff["removed"]) == 1
        assert targets_diff["removed"][0]["path"] == "photos/"

    def test_json_output_field_diff_has_correct_change_type(self, temp_dir, sample_config):
        modified_json = temp_dir / "modified.json"
        modified_data = {
            "profile_version": "1.0",
            "manifest": {
                "name": "new-name",
                "source_dir": "source",
                "backup_dir": "backup",
                "targets": [
                    {"path": "documents/", "description": "Important documents"},
                    {"path": "photos/", "description": "Personal photos"},
                ],
                "retention_days": 30,
                "exclude_patterns": ["*.tmp", "*.log"],
                "hash_algorithm": "sha256",
            },
        }
        with open(modified_json, "w", encoding="utf-8") as f:
            json.dump(modified_data, f, indent=2)

        diff = diff_profiles(str(sample_config), str(modified_json))
        output = format_diff_json(diff)
        parsed = json.loads(output)

        name_diff = next(fd for fd in parsed["field_diffs"] if fd["field"] == "name")
        assert name_diff["change_type"] == "modify"
        assert name_diff["old_value"] == "test-backup"
        assert name_diff["new_value"] == "new-name"


class TestOperationLogsDistinguish:
    def test_diff_logged_separately(self, temp_dir, sample_config):
        export_path = temp_dir / "exported.json"
        export_profile(str(sample_config), str(export_path))

        diff_profiles(str(sample_config), str(export_path))

        logs = read_operation_logs(str(sample_config))
        diff_logs = [l for l in logs if l.operation == "diff"]
        assert len(diff_logs) >= 1
        assert diff_logs[0].status in ["no_changes", "changes_found", "conflict"]

    def test_dry_run_logged_separately(self, temp_dir, sample_config, sample_json_profile):
        try:
            import_profile(str(sample_json_profile), str(sample_config), force=False, dry_run=True)
        except ProfileConflictError:
            pass

        logs = read_operation_logs(str(sample_config))
        dry_run_logs = [l for l in logs if l.operation == "dry_run"]
        assert len(dry_run_logs) >= 1

    def test_import_logged_separately(self, temp_dir, sample_config, sample_json_profile):
        import_profile(str(sample_json_profile), str(sample_config), force=True)

        logs = read_operation_logs(str(sample_config))
        overwrite_logs = [l for l in logs if l.operation == "overwrite"]
        assert len(overwrite_logs) >= 1
        assert overwrite_logs[0].status == "overwritten"
        assert overwrite_logs[0].backup_path is not None

    def test_rollback_logged_separately(self, temp_dir, sample_config):
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
        rollback_logs = [l for l in logs if l.operation == "rollback"]
        assert len(rollback_logs) >= 1
        assert rollback_logs[0].status == "rolled_back"
        assert rollback_logs[0].backup_path is not None


class TestImportPermissionDenied:
    def test_import_permission_denied_no_backup_leak(self, temp_dir, sample_config, sample_json_profile):
        import stat

        original_content = sample_config.read_text(encoding="utf-8")

        original_open = open

        def mock_open(path, mode="r", *args, **kwargs):
            if path == str(sample_config) and "w" in mode:
                raise PermissionError("Permission denied")
            return original_open(path, mode, *args, **kwargs)

        with patch("builtins.open", side_effect=mock_open):
            with pytest.raises(ProfilePermissionError):
                import_profile(str(sample_json_profile), str(sample_config), force=True)

        assert sample_config.read_text(encoding="utf-8") == original_content

        log_dir = temp_dir / PROFILE_LOG_DIRNAME
        if log_dir.exists():
            log_file = log_dir / PROFILE_LOG_FILENAME
            if log_file.exists():
                logs = read_operation_logs(str(sample_config))
                rollback_logs = [l for l in logs if l.operation == "rollback"]
                assert len(rollback_logs) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
