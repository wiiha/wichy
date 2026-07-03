"""Tests for server subcommand CLI parsing."""

from wichy.cli_parser import CliParser


def test_server_mode_defaults():
    parser = CliParser()
    args = parser.parse(["server"])
    assert args.server_mode is True
    assert args.no_chat is False
    assert args.server_port is None


def test_server_mode_no_chat():
    parser = CliParser()
    args = parser.parse(["server", "--no-chat"])
    assert args.server_mode is True
    assert args.no_chat is True
    assert args.server_port is None


def test_server_mode_custom_port():
    parser = CliParser()
    args = parser.parse(["server", "--port", "9000"])
    assert args.server_mode is True
    assert args.server_port == 9000


def test_server_mode_no_chat_and_port():
    parser = CliParser()
    args = parser.parse(["server", "--no-chat", "--port", "9001"])
    assert args.server_mode is True
    assert args.no_chat is True
    assert args.server_port == 9001
