"""Tests for the ServerController class."""

from unittest.mock import MagicMock, patch

import pytest

from wichy.server_controller import ServerController


class TestServerController:
    """Test suite for ServerController."""

    @pytest.fixture
    def mock_start_server(self):
        """Mock the start_server_in_background function."""
        with patch("wichy.server_controller._start_server_in_background") as mock:
            mock.return_value = 7891
            yield mock

    def test_server_controller_defaults(self):
        """Test ServerController default initialization."""
        from wichy.config import settings

        controller = ServerController()
        assert controller.port == settings.server_port
        assert controller.is_running is False
        assert controller.actual_port is None

    def test_server_controller_custom_port(self):
        """Test ServerController with custom port."""
        controller = ServerController(port=9999)
        assert controller.port == 9999

    def test_server_controller_start(self, mock_start_server):
        """Test starting the server."""
        controller = ServerController(port=7891)
        actual_port = controller.start()

        assert actual_port == 7891
        assert controller.is_running is True
        assert controller.actual_port == 7891
        mock_start_server.assert_called_once_with(port=7891)

    def test_server_controller_start_already_running(self, mock_start_server):
        """Test that starting an already running server returns existing port."""
        controller = ServerController(port=7891)
        controller.start()

        # Second call should not start again
        actual_port = controller.start()
        assert actual_port == 7891
        assert mock_start_server.call_count == 1

    def test_server_controller_stop(self, mock_start_server):
        """Test stopping the server."""
        controller = ServerController(port=7891)
        controller.start()
        assert controller.is_running is True

        controller.stop()
        assert controller.is_running is False
        assert controller.actual_port is None

    def test_server_controller_get_startup_info_not_running(self):
        """Test get_startup_info when server not running."""
        controller = ServerController()
        info = controller.get_startup_info()
        assert info == {}

    def test_server_controller_get_startup_info_running(self, mock_start_server):
        """Test get_startup_info when server running."""
        controller = ServerController(port=7891)
        controller.start()

        info = controller.get_startup_info()
        assert info["port"] == 7891
        assert info["url"] == "http://127.0.0.1:7891"
        assert info["graph_url"] == "http://127.0.0.1:7891/tools/graph/"

    def test_server_controller_enable_on_start_false(self, mock_start_server):
        """Test ServerController with enable_on_start=False does not auto-start."""
        controller = ServerController(enable_on_start=False)
        assert controller.is_running is False
        assert controller.actual_port is None
        mock_start_server.assert_not_called()

    def test_server_controller_manual_start_with_disabled_autostart(
        self, mock_start_server
    ):
        """Test manually starting server when auto-start disabled."""
        controller = ServerController(enable_on_start=False)
        controller.start()
        assert controller.is_running is True
        mock_start_server.assert_called_once()
