"""Tests for the config backend resolver and 'new backend' CLI handler."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import yaml

from wichy.config.backend_resolver import (
    resolve_config_backend,
    save_backend_to_yaml,
    _interpolate_env_vars,
    _validate_entry,
)
from wichy.cli.handlers import _validate_new_backend

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_args(
    name="my-llm",
    base_url="http://localhost:8080/v1",
    model="llama-3-70b",
    api_key=None,
    extra_body=None,
    scope="home",
    force=False,
):
    """Build a mock args object for _validate_new_backend."""
    return MagicMock(
        new_backend_name=name,
        new_backend_base_url=base_url,
        new_backend_model=model,
        new_backend_api_key=api_key,
        new_backend_extra_body=extra_body,
        new_backend_scope=scope,
        new_backend_force=force,
    )


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Point settings.wichy_home to a temp dir and create a .wichy dir for project scope."""
    home_wichy = tmp_path / "home" / ".wichy"
    home_wichy.mkdir(parents=True)
    project_wichy = tmp_path / "project" / ".wichy"
    project_wichy.mkdir(parents=True)

    monkeypatch.setattr("wichy.config.settings.wichy_home", home_wichy)
    monkeypatch.chdir(tmp_path / "project")
    return tmp_path


def _make_fresh_settings(tmp_settings_path):
    """Create a Settings() with wichy_home pointed at the temp home dir."""
    from wichy.config.settings import Settings

    home_wichy = tmp_settings_path / "home" / ".wichy"
    return Settings(wichy_home=home_wichy)


# ---------------------------------------------------------------------------
# _interpolate_env_vars
# ---------------------------------------------------------------------------


class TestInterpolateEnvVars:
    def test_simple_interpolation(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_KEY", "sk-secret")
        result = _interpolate_env_vars("${MY_TEST_KEY}")
        assert result == "sk-secret"

    def test_no_env_var_left_as_is(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_KEY", raising=False)
        result = _interpolate_env_vars("${NONEXISTENT_KEY}")
        assert result == "${NONEXISTENT_KEY}"

    def test_no_placeholder(self):
        result = _interpolate_env_vars("sk-plain-key")
        assert result == "sk-plain-key"

    def test_mixed_string(self, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret123")
        result = _interpolate_env_vars("prefix-${API_KEY}-suffix")
        assert result == "prefix-secret123-suffix"

    def test_multiple_vars(self, monkeypatch):
        monkeypatch.setenv("VAR_A", "aaa")
        monkeypatch.setenv("VAR_B", "bbb")
        result = _interpolate_env_vars("${VAR_A}-${VAR_B}")
        assert result == "aaa-bbb"

    def test_partial_missing(self, monkeypatch):
        monkeypatch.setenv("VAR_A", "aaa")
        monkeypatch.delenv("VAR_B", raising=False)
        result = _interpolate_env_vars("${VAR_A}-${VAR_B}")
        assert result == "aaa-${VAR_B}"


# ---------------------------------------------------------------------------
# _validate_entry
# ---------------------------------------------------------------------------


class TestValidateEntry:
    def test_valid_entry(self):
        _validate_entry(
            {"base_url": "http://localhost:8080/v1", "model": "llama-3"},
            "test",
        )

    def test_valid_with_all_fields(self):
        _validate_entry(
            {
                "base_url": "http://localhost:8080/v1",
                "model": "llama-3",
                "api_key": "sk-key",
                "extra_body": {"provider": {"allow_fallbacks": True}},
            },
            "test",
        )

    def test_missing_base_url(self):
        with pytest.raises(ValueError, match="missing required field 'base_url'"):
            _validate_entry({"model": "llama-3"}, "test")

    def test_missing_model(self):
        with pytest.raises(ValueError, match="missing required field 'model'"):
            _validate_entry({"base_url": "http://localhost:8080/v1"}, "test")

    def test_empty_base_url(self):
        with pytest.raises(ValueError, match="'base_url' must be a non-empty string"):
            _validate_entry({"base_url": "  ", "model": "llama-3"}, "test")

    def test_empty_model(self):
        with pytest.raises(ValueError, match="'model' must be a non-empty string"):
            _validate_entry(
                {"base_url": "http://localhost:8080/v1", "model": ""}, "test"
            )

    def test_api_key_not_string(self):
        with pytest.raises(ValueError, match="'api_key' must be a string or null"):
            _validate_entry(
                {"base_url": "http://x", "model": "m", "api_key": 123}, "test"
            )

    def test_extra_body_not_dict(self):
        with pytest.raises(ValueError, match="'extra_body' must be a mapping"):
            _validate_entry(
                {"base_url": "http://x", "model": "m", "extra_body": "not a dict"},
                "test",
            )


# ---------------------------------------------------------------------------
# resolve_config_backend — alias lookup
# ---------------------------------------------------------------------------


class TestResolveAlias:
    def test_alias_found(self, tmp_settings, monkeypatch):
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "my-llm": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3-70b",
                            "api_key": "sk-test",
                        }
                    }
                }
            )
        )
        # Re-load settings to pick up the yaml
        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        base_url, api_key, model, extra_body = resolve_config_backend("config/my-llm")
        assert base_url == "http://localhost:8080/v1"
        assert api_key == "sk-test"
        assert model == "llama-3-70b"
        assert extra_body == {}

    def test_alias_not_found(self, tmp_settings, monkeypatch):
        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        with pytest.raises(ValueError, match="not found"):
            resolve_config_backend("config/nonexistent")

    def test_alias_with_env_var(self, tmp_settings, monkeypatch):
        monkeypatch.setenv("MY_SECRET_KEY", "sk-from-env")
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "env-llm": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                            "api_key": "${MY_SECRET_KEY}",
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        _, api_key, _, _ = resolve_config_backend("config/env-llm")
        assert api_key == "sk-from-env"

    def test_alias_api_key_fallback_to_openai(self, tmp_settings, monkeypatch):
        """If api_key is absent, fall back to settings.openai_api_key."""
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "no-key-llm": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        new_settings.openai_api_key = "sk-openai-fallback"
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        _, api_key, _, _ = resolve_config_backend("config/no-key-llm")
        assert api_key == "sk-openai-fallback"

    def test_alias_api_key_fallback_to_generic(self, tmp_settings, monkeypatch):
        """If api_key and openai_api_key are both absent, fall back to sk-generic."""
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "no-key-at-all": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        new_settings.openai_api_key = None
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        _, api_key, _, _ = resolve_config_backend("config/no-key-at-all")
        assert api_key == "sk-generic"

    def test_alias_with_extra_body(self, tmp_settings, monkeypatch):
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "with-extra": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                            "api_key": "sk-key",
                            "extra_body": {"provider": {"allow_fallbacks": True}},
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        _, _, _, extra_body = resolve_config_backend("config/with-extra")
        assert extra_body == {"provider": {"allow_fallbacks": True}}


# ---------------------------------------------------------------------------
# resolve_config_backend — filepath lookup
# ---------------------------------------------------------------------------


class TestResolveFilepath:
    def test_json_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "my-backend.json"
        config_file.write_text(
            json.dumps(
                {
                    "base_url": "http://localhost:8080/v1",
                    "model": "llama-3-70b",
                    "api_key": "sk-json-key",
                }
            )
        )

        base_url, api_key, model, _ = resolve_config_backend(f"config/{config_file}")
        assert base_url == "http://localhost:8080/v1"
        assert api_key == "sk-json-key"
        assert model == "llama-3-70b"

    def test_yaml_file(self, tmp_path, monkeypatch):
        config_file = tmp_path / "my-backend.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "base_url": "http://localhost:8080/v1",
                    "model": "llama-3-70b",
                    "api_key": "sk-yaml-key",
                }
            )
        )

        base_url, api_key, model, _ = resolve_config_backend(f"config/{config_file}")
        assert base_url == "http://localhost:8080/v1"
        assert api_key == "sk-yaml-key"
        assert model == "llama-3-70b"

    def test_absolute_path(self, tmp_path, monkeypatch):
        config_file = tmp_path / "abs-config.json"
        config_file.write_text(
            json.dumps({"base_url": "http://remote.com/v1", "model": "gpt-4o"})
        )

        # config//abs/path — the part after first / is /abs/path
        base_url, _, model, _ = resolve_config_backend(
            f"config/{config_file.absolute()}"
        )
        assert base_url == "http://remote.com/v1"
        assert model == "gpt-4o"

    def test_file_missing_required_field(self, tmp_path):
        config_file = tmp_path / "bad.json"
        config_file.write_text(json.dumps({"base_url": "http://localhost:8080/v1"}))

        with pytest.raises(ValueError, match="missing required field 'model'"):
            resolve_config_backend(f"config/{config_file}")

    def test_file_not_json_nor_yaml(self, tmp_path):
        config_file = tmp_path / "bad.txt"
        config_file.write_text("this is not valid json or yaml: : :")

        with pytest.raises(ValueError, match="Could not parse"):
            resolve_config_backend(f"config/{config_file}")


# ---------------------------------------------------------------------------
# resolve_config_backend — error cases
# ---------------------------------------------------------------------------


class TestResolveErrors:
    def test_empty_reference(self):
        with pytest.raises(ValueError, match="Invalid config backend format"):
            resolve_config_backend("config/")

    def test_no_slash(self):
        with pytest.raises(ValueError, match="Invalid config backend format"):
            resolve_config_backend("config")

    def test_no_file_no_alias(self, tmp_settings, monkeypatch):
        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        with pytest.raises(ValueError, match="not found"):
            resolve_config_backend("config/totally-nonexistent")


# ---------------------------------------------------------------------------
# save_backend_to_yaml
# ---------------------------------------------------------------------------


class TestSaveBackendToYaml:
    def test_create_new_file_home(self, tmp_settings):
        path = save_backend_to_yaml(
            alias="my-llm",
            base_url="http://localhost:8080/v1",
            model="llama-3",
            api_key="sk-key",
            extra_body=None,
            scope="home",
        )
        assert path.exists()
        data = yaml.safe_load(path.read_text())
        assert "backends" in data
        assert "my-llm" in data["backends"]
        assert data["backends"]["my-llm"]["base_url"] == "http://localhost:8080/v1"
        assert data["backends"]["my-llm"]["api_key"] == "sk-key"

    def test_create_new_file_project(self, tmp_settings):
        path = save_backend_to_yaml(
            alias="proj-llm",
            base_url="http://localhost:9000/v1",
            model="gpt-4o",
            api_key=None,
            extra_body=None,
            scope="project",
        )
        assert path.exists()
        assert ".wichy/settings.yaml" in str(path)
        data = yaml.safe_load(path.read_text())
        assert data["backends"]["proj-llm"]["model"] == "gpt-4o"
        # api_key should be absent since it was None
        assert "api_key" not in data["backends"]["proj-llm"]

    def test_merge_into_existing_file(self, tmp_settings):
        # Create initial file with one backend
        save_backend_to_yaml(
            alias="first",
            base_url="http://first:8080/v1",
            model="m1",
            api_key="k1",
            extra_body=None,
            scope="home",
        )
        # Add a second backend
        save_backend_to_yaml(
            alias="second",
            base_url="http://second:8080/v1",
            model="m2",
            api_key="k2",
            extra_body=None,
            scope="home",
        )
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        data = yaml.safe_load(home_yaml.read_text())
        assert "first" in data["backends"]
        assert "second" in data["backends"]

    def test_preserves_other_namespaces(self, tmp_settings):
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "other_namespace": {
                        "some_key": "some_value",
                    }
                }
            )
        )
        save_backend_to_yaml(
            alias="my-llm",
            base_url="http://localhost:8080/v1",
            model="llama-3",
            api_key="sk-key",
            extra_body=None,
            scope="home",
        )
        data = yaml.safe_load(home_yaml.read_text())
        assert "other_namespace" in data
        assert data["other_namespace"]["some_key"] == "some_value"
        assert "backends" in data

    def test_duplicate_alias_raises(self, tmp_settings):
        save_backend_to_yaml(
            alias="dup",
            base_url="http://localhost:8080/v1",
            model="llama-3",
            api_key="sk-key",
            extra_body=None,
            scope="home",
        )
        with pytest.raises(ValueError, match="already exists"):
            save_backend_to_yaml(
                alias="dup",
                base_url="http://other:8080/v1",
                model="other-model",
                api_key="sk-other",
                extra_body=None,
                scope="home",
            )

    def test_with_extra_body(self, tmp_settings):
        path = save_backend_to_yaml(
            alias="with-extra",
            base_url="http://localhost:8080/v1",
            model="llama-3",
            api_key="sk-key",
            extra_body={"provider": {"allow_fallbacks": True}},
            scope="home",
        )
        data = yaml.safe_load(path.read_text())
        assert data["backends"]["with-extra"]["extra_body"] == {
            "provider": {"allow_fallbacks": True}
        }

    def test_invalid_scope(self, tmp_settings):
        with pytest.raises(ValueError, match="Invalid scope"):
            save_backend_to_yaml(
                alias="x",
                base_url="http://x",
                model="m",
                api_key=None,
                extra_body=None,
                scope="invalid",
            )


# ---------------------------------------------------------------------------
# _validate_new_backend (CLI handler validation)
# ---------------------------------------------------------------------------


class TestValidateNewBackend:
    def test_all_valid_fields(self):
        result = _validate_new_backend(_make_args())
        assert result is not None
        assert result["alias"] == "my-llm"
        assert result["base_url"] == "http://localhost:8080/v1"
        assert result["model"] == "llama-3-70b"

    def test_invalid_alias_not_kebab(self):
        result = _validate_new_backend(_make_args(name="My_LLM"))
        assert result is None  # validation failed

    def test_invalid_alias_uppercase(self):
        result = _validate_new_backend(_make_args(name="MyLLM"))
        assert result is None

    def test_invalid_alias_spaces(self):
        result = _validate_new_backend(_make_args(name="my llm"))
        assert result is None

    def test_valid_alias_with_numbers(self):
        result = _validate_new_backend(_make_args(name="llm-2"))
        assert result is not None
        assert result["alias"] == "llm-2"

    def test_valid_alias_single_word(self):
        result = _validate_new_backend(_make_args(name="ollama"))
        assert result is not None
        assert result["alias"] == "ollama"

    def test_invalid_url_no_scheme(self):
        result = _validate_new_backend(_make_args(base_url="localhost:8080/v1"))
        assert result is None

    def test_invalid_url_ftp(self):
        result = _validate_new_backend(_make_args(base_url="ftp://localhost"))
        assert result is None

    def test_valid_url_https(self):
        result = _validate_new_backend(_make_args(base_url="https://api.openai.com/v1"))
        assert result is not None
        assert result["base_url"] == "https://api.openai.com/v1"

    def test_empty_model(self):
        result = _validate_new_backend(_make_args(model="  "))
        assert result is None

    def test_valid_extra_body_json(self):
        result = _validate_new_backend(
            _make_args(extra_body='{"provider": {"allow_fallbacks": true}}')
        )
        assert result is not None
        assert result["extra_body"] == {"provider": {"allow_fallbacks": True}}

    def test_invalid_extra_body_not_json(self):
        result = _validate_new_backend(_make_args(extra_body="not json at all"))
        assert result is None

    def test_invalid_extra_body_not_dict(self):
        result = _validate_new_backend(_make_args(extra_body="[1, 2, 3]"))
        assert result is None

    def test_empty_extra_body_treated_as_none(self):
        result = _validate_new_backend(_make_args(extra_body="  "))
        assert result is not None
        assert result["extra_body"] is None

    def test_api_key_with_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-secret")
        result = _validate_new_backend(_make_args(api_key="${MY_KEY}"))
        assert result is not None
        assert result["api_key"] == "${MY_KEY}"

    def test_api_key_env_var_not_set_warns(self, monkeypatch):
        """When api_key references a missing env var, validation still passes
        but a warning is printed to user_console."""
        from unittest.mock import patch

        monkeypatch.delenv("MISSING_KEY", raising=False)
        with patch("wichy.cli.handlers.user_console.print") as mock_print:
            result = _validate_new_backend(_make_args(api_key="${MISSING_KEY}"))
            # Should still pass validation (warn, not error)
            assert result is not None
            assert result["api_key"] == "${MISSING_KEY}"
            # Verify the warning was actually emitted
            mock_print.assert_called_once()
            warning_text = str(mock_print.call_args)
            assert "MISSING_KEY" in warning_text
            assert "warn" in warning_text

    def test_empty_api_key_becomes_none(self):
        result = _validate_new_backend(_make_args(api_key="  "))
        assert result is not None
        assert result["api_key"] is None

    def test_multiple_errors(self):
        """All errors should be collected and reported."""
        result = _validate_new_backend(
            _make_args(name="BAD NAME", base_url="no-scheme", model="")
        )
        assert result is None

    def test_scope_choices(self):
        result = _validate_new_backend(_make_args(scope="project"))
        assert result is not None
        assert result["scope"] == "project"

    def test_force_flag(self):
        result = _validate_new_backend(_make_args(force=True))
        assert result is not None
        assert result["force"] is True


# ---------------------------------------------------------------------------
# CLI parser — 'new backend' subcommand
# ---------------------------------------------------------------------------


class TestCliParserNewBackend:
    def test_parse_new_backend_basic(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        config = parser.parse(
            [
                "new",
                "backend",
                "--name",
                "my-llm",
                "--base-url",
                "http://localhost:8080/v1",
                "--model",
                "llama-3",
            ]
        )
        assert config.command == "new"
        assert config.new_command == "backend"
        assert config.new_backend_name == "my-llm"
        assert config.new_backend_base_url == "http://localhost:8080/v1"
        assert config.new_backend_model == "llama-3"

    def test_parse_new_backend_all_flags(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        config = parser.parse(
            [
                "new",
                "backend",
                "--name",
                "my-llm",
                "--base-url",
                "https://api.example.com/v1",
                "--model",
                "gpt-4o",
                "--api-key",
                "sk-key",
                "--extra-body",
                '{"provider": true}',
                "--scope",
                "project",
                "--force",
            ]
        )
        assert config.new_backend_api_key == "sk-key"
        assert config.new_backend_extra_body == '{"provider": true}'
        assert config.new_backend_scope == "project"
        assert config.new_backend_force is True

    def test_parse_new_backend_defaults(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        config = parser.parse(
            [
                "new",
                "backend",
                "--name",
                "x",
                "--base-url",
                "http://x",
                "--model",
                "m",
            ]
        )
        assert config.new_backend_api_key is None
        assert config.new_backend_extra_body is None
        assert config.new_backend_scope == "home"
        assert config.new_backend_force is False

    def test_parse_new_backend_missing_required_name(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        with pytest.raises(SystemExit):
            parser.parse(["new", "backend", "--base-url", "http://x", "--model", "m"])

    def test_parse_new_backend_missing_required_base_url(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        with pytest.raises(SystemExit):
            parser.parse(["new", "backend", "--name", "x", "--model", "m"])

    def test_parse_new_backend_invalid_scope(self):
        from wichy.cli_parser import CliParser

        parser = CliParser()
        with pytest.raises(SystemExit):
            parser.parse(
                [
                    "new",
                    "backend",
                    "--name",
                    "x",
                    "--base-url",
                    "http://x",
                    "--model",
                    "m",
                    "--scope",
                    "invalid",
                ]
            )


# ---------------------------------------------------------------------------
# Project-scope override (project overrides home)
# ---------------------------------------------------------------------------


class TestProjectScopeOverride:
    def test_project_overrides_home(self, tmp_settings, monkeypatch):
        """Project settings.yaml should override home settings.yaml for
        backends with the same alias."""
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "shared": {
                            "base_url": "http://home:8080/v1",
                            "model": "home-model",
                            "api_key": "sk-home",
                        }
                    }
                }
            )
        )
        project_yaml = tmp_settings / "project" / ".wichy" / "settings.yaml"
        project_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "shared": {
                            "base_url": "http://project:9000/v1",
                            "model": "project-model",
                            "api_key": "sk-project",
                        }
                    }
                }
            )
        )

        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        base_url, api_key, model, _ = resolve_config_backend("config/shared")
        assert base_url == "http://project:9000/v1"
        assert model == "project-model"
        assert api_key == "sk-project"

    def test_home_only_alias_still_works(self, tmp_settings, monkeypatch):
        """An alias only in home should still resolve when project yaml exists."""
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "home-only": {
                            "base_url": "http://home:8080/v1",
                            "model": "home-model",
                            "api_key": "sk-home",
                        }
                    }
                }
            )
        )
        project_yaml = tmp_settings / "project" / ".wichy" / "settings.yaml"
        project_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "other": {
                            "base_url": "http://other:9000/v1",
                            "model": "other-model",
                            "api_key": "sk-other",
                        }
                    }
                }
            )
        )

        new_settings = _make_fresh_settings(tmp_settings)
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        base_url, _, model, _ = resolve_config_backend("config/home-only")
        assert base_url == "http://home:8080/v1"
        assert model == "home-model"


# ---------------------------------------------------------------------------
# handle_new_backend end-to-end (including --force)
# ---------------------------------------------------------------------------


class TestHandleNewBackend:
    def test_save_and_force_overwrite(self, tmp_settings, monkeypatch):
        """End-to-end: save a backend, then overwrite with --force."""
        from wichy.cli.handlers import handle_new_backend

        # First save
        args1 = _make_args(
            name="force-test",
            base_url="http://first:8080/v1",
            model="first-model",
            api_key="sk-first",
            scope="home",
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_new_backend(args1)
        assert exc_info.value.code == 0

        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        data = yaml.safe_load(home_yaml.read_text())
        assert data["backends"]["force-test"]["base_url"] == "http://first:8080/v1"

        # Second save without --force should fail
        args2 = _make_args(
            name="force-test",
            base_url="http://second:8080/v1",
            model="second-model",
            api_key="sk-second",
            scope="home",
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_new_backend(args2)
        assert exc_info.value.code == 1

        # Data should be unchanged
        data = yaml.safe_load(home_yaml.read_text())
        assert data["backends"]["force-test"]["base_url"] == "http://first:8080/v1"

        # Third save with --force should overwrite
        args3 = _make_args(
            name="force-test",
            base_url="http://third:8080/v1",
            model="third-model",
            api_key="sk-third",
            scope="home",
            force=True,
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_new_backend(args3)
        assert exc_info.value.code == 0

        data = yaml.safe_load(home_yaml.read_text())
        assert data["backends"]["force-test"]["base_url"] == "http://third:8080/v1"
        assert data["backends"]["force-test"]["model"] == "third-model"

    def test_force_on_nonexistent_alias(self, tmp_settings):
        """--force on a non-existent alias should work fine (no-op removal)."""
        from wichy.cli.handlers import handle_new_backend

        args = _make_args(
            name="new-alias",
            base_url="http://localhost:8080/v1",
            model="llama-3",
            api_key="sk-key",
            scope="home",
            force=True,
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_new_backend(args)
        assert exc_info.value.code == 0

        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        data = yaml.safe_load(home_yaml.read_text())
        assert "new-alias" in data["backends"]

    def test_validation_error_exits_1(self, tmp_settings):
        """Invalid input should exit(1) not exit(0)."""
        from wichy.cli.handlers import handle_new_backend

        args = _make_args(
            name="BAD NAME",  # not kebab-case
            base_url="http://localhost:8080/v1",
            model="llama-3",
        )
        with pytest.raises(SystemExit) as exc_info:
            handle_new_backend(args)
        assert exc_info.value.code == 1

    def test_force_preserves_other_backends(self, tmp_settings):
        """--force on one alias should not affect other aliases."""
        from wichy.cli.handlers import handle_new_backend

        # Save two backends
        for name in ["alpha", "beta"]:
            args = _make_args(
                name=name,
                base_url=f"http://{name}:8080/v1",
                model=f"{name}-model",
                api_key=f"sk-{name}",
                scope="home",
            )
            with pytest.raises(SystemExit):
                handle_new_backend(args)

        # Force-overwrite alpha
        args = _make_args(
            name="alpha",
            base_url="http://new-alpha:9000/v1",
            model="new-alpha-model",
            api_key="sk-new",
            scope="home",
            force=True,
        )
        with pytest.raises(SystemExit):
            handle_new_backend(args)

        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        data = yaml.safe_load(home_yaml.read_text())
        assert data["backends"]["alpha"]["base_url"] == "http://new-alpha:9000/v1"
        assert data["backends"]["beta"]["base_url"] == "http://beta:8080/v1"


# ---------------------------------------------------------------------------
# File loader error paths
# ---------------------------------------------------------------------------


class TestFileLoaderErrors:
    def test_yml_extension(self, tmp_path):
        """Files with .yml extension should be parsed as YAML."""
        config_file = tmp_path / "config.yml"
        config_file.write_text(
            yaml.dump(
                {
                    "base_url": "http://localhost:8080/v1",
                    "model": "llama-3",
                    "api_key": "sk-yml",
                }
            )
        )
        base_url, api_key, model, _ = resolve_config_backend(f"config/{config_file}")
        assert base_url == "http://localhost:8080/v1"
        assert api_key == "sk-yml"
        assert model == "llama-3"

    def test_no_extension_tries_json_then_yaml(self, tmp_path):
        """Files without .json/.yaml/.yml extension should try JSON then YAML."""
        config_file = tmp_path / "config"
        config_file.write_text(
            yaml.dump(
                {
                    "base_url": "http://localhost:8080/v1",
                    "model": "llama-3",
                    "api_key": "sk-noext",
                }
            )
        )
        base_url, _, model, _ = resolve_config_backend(f"config/{config_file}")
        assert base_url == "http://localhost:8080/v1"
        assert model == "llama-3"

    def test_non_dict_json_raises(self, tmp_path):
        """A JSON array should raise ValueError about mapping at top level."""
        config_file = tmp_path / "array.json"
        config_file.write_text("[1, 2, 3]")
        with pytest.raises(ValueError, match="must contain a mapping"):
            resolve_config_backend(f"config/{config_file}")

    def test_non_dict_yaml_raises(self, tmp_path):
        """A YAML scalar should raise ValueError about mapping at top level."""
        config_file = tmp_path / "scalar.yaml"
        config_file.write_text("just a string")
        with pytest.raises(ValueError, match="must contain a mapping"):
            resolve_config_backend(f"config/{config_file}")

    def test_empty_api_key_after_interpolation_falls_back(
        self, tmp_settings, monkeypatch
    ):
        """If ${VAR} resolves to empty string, should fall back to openai_api_key."""
        monkeypatch.setenv("EMPTY_KEY", "")
        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "empty-env": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                            "api_key": "${EMPTY_KEY}",
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        new_settings.openai_api_key = "sk-openai-fallback"
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        _, api_key, _, _ = resolve_config_backend("config/empty-env")
        assert api_key == "sk-openai-fallback"

    def test_lowercase_env_var_interpolation(self, monkeypatch):
        """Lowercase env var names should be interpolated."""
        monkeypatch.setenv("my_api_key", "sk-lowercase")
        result = _interpolate_env_vars("${my_api_key}")
        assert result == "sk-lowercase"

    def test_validate_only_suppresses_warnings(self, tmp_settings, monkeypatch):
        """validate_only=True should suppress API key warning messages."""
        from unittest.mock import patch

        home_yaml = tmp_settings / "home" / ".wichy" / "settings.yaml"
        home_yaml.write_text(
            yaml.dump(
                {
                    "backends": {
                        "no-key": {
                            "base_url": "http://localhost:8080/v1",
                            "model": "llama-3",
                        }
                    }
                }
            )
        )
        new_settings = _make_fresh_settings(tmp_settings)
        new_settings.openai_api_key = None
        monkeypatch.setattr("wichy.config.backend_resolver.settings", new_settings)

        with patch("wichy.config.backend_resolver.user_console.print") as mock_print:
            resolve_config_backend("config/no-key", validate_only=True)
            # No warnings should be printed during validation-only mode
            mock_print.assert_not_called()
