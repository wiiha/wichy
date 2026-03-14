"""Tests for the CliParser class."""

import sys
from unittest.mock import patch

import pytest

from wichy.cli_parser import CliConfig, CliParser


class TestCliParser:
    """Test suite for CliParser."""
    
    def test_parse_global_flags(self):
        """Test parsing global flags."""
        parser = CliParser()
        args = parser.parse(["--show-log", "--list-tools"])
        assert args.show_log is True
        assert args.list_tools is True
        assert args.command is None
    
    def test_parse_model_str(self):
        """Test parsing model string."""
        parser = CliParser()
        args = parser.parse(["-m", "ollama/llama2"])
        assert args.model_str == "ollama/llama2"
    
    def test_parse_tools_filtering(self):
        """Test parsing tools and not-tools flags."""
        parser = CliParser()
        args = parser.parse(["--tools", "bash, file", "--not-tools", "curl"])
        assert args.tools == "bash, file"
        assert args.not_tools == "curl"
    
    def test_parse_root_agent_description(self):
        """Test parsing root agent description."""
        parser = CliParser()
        args = parser.parse(["-r", "my-custom-agent"])
        assert args.root_agent_description == "my-custom-agent"
        assert args.root_agent_description != "root-agent-code-advanced"
    
    def test_parse_load_ctx(self):
        """Test parsing load context flag."""
        parser = CliParser()
        args = parser.parse(["--load-ctx", "/path/to/context.json"])
        assert args.load_ctx == "/path/to/context.json"
    
    def test_parse_no_server(self):
        """Test parsing no-server flag."""
        parser = CliParser()
        args = parser.parse(["--no-server"])
        assert args.no_server is True
    
    def test_parse_ls_subcommand(self):
        """Test parsing ls subcommand."""
        parser = CliParser()
        args = parser.parse(["ls", "tools"])
        assert args.command == "ls"
        assert args.ls_command == "tools"
    
    def test_parse_ls_ra(self):
        """Test parsing ls ra subcommand."""
        parser = CliParser()
        args = parser.parse(["ls", "ra"])
        assert args.command == "ls"
        assert args.ls_command == "ra"
    
    def test_parse_ls_ctx(self):
        """Test parsing ls ctx subcommand."""
        parser = CliParser()
        args = parser.parse(["ls", "ctx"])
        assert args.command == "ls"
        assert args.ls_command == "ctx"
    
    def test_parse_ls_skills(self):
        """Test parsing ls skills subcommand."""
        parser = CliParser()
        args = parser.parse(["ls", "skills"])
        assert args.command == "ls"
        assert args.ls_command == "skills"
    
    def test_parse_new_skill(self):
        """Test parsing new skill subcommand."""
        parser = CliParser()
        args = parser.parse(["new", "skill", "-n", "my-skill"])
        assert args.command == "new"
        assert args.new_command == "skill"
        assert args.new_skill_name == "my-skill"
        assert args.new_skill_with_script is False
    
    def test_parse_new_skill_with_script(self):
        """Test parsing new skill with --with-script flag."""
        parser = CliParser()
        args = parser.parse(["new", "skill", "-n", "my-skill", "--with-script"])
        assert args.command == "new"
        assert args.new_command == "skill"
        assert args.new_skill_name == "my-skill"
        assert args.new_skill_with_script is True
    
    def test_parse_ra_template(self):
        """Test parsing ra template subcommand."""
        parser = CliParser()
        args = parser.parse(["ra", "-t"])
        assert args.command == "ra"
        assert args.ra_template is True
    
    def test_parse_ls_without_subcommand(self):
        """Test parsing 'ls' without subcommand returns ls_command=None."""
        parser = CliParser()
        args = parser.parse(["ls"])
        assert args.command == "ls"
        assert args.ls_command is None
    
    def test_parse_defaults(self):
        """Test default values."""
        parser = CliParser()
        args = parser.parse([])
        assert args.show_log is False
        assert args.list_tools is False
        assert args.model_str == ""
        assert args.root_agent_description == "root-agent-code-advanced"
        assert args.no_server is False
        assert args.command is None
    
    def test_parse_log_tools_requires_show_log_not_enforced_by_parser(self):
        """Test that log-tools flag can be set independently (enforcement is elsewhere)."""
        parser = CliParser()
        args = parser.parse(["--log-tools"])
        assert args.log_tools is True
    
    def test_parse_log_agents_requires_show_log_not_enforced_by_parser(self):
        """Test that log-agents flag can be set independently (enforcement is elsewhere)."""
        parser = CliParser()
        args = parser.parse(["--log-agents"])
        assert args.log_agents is True
    
    def test_print_usage(self, capsys):
        """Test print_usage method."""
        parser = CliParser()
        parser.print_usage()
        captured = capsys.readouterr()
        assert "usage:" in captured.out or "usage:" in captured.err
