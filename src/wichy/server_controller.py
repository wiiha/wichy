"""Server controller - manages web server lifecycle."""

from typing import Optional

from wichy.config import settings

# Import at module level for easier testing/patching
from wichy.server import start_server_in_background as _start_server_in_background


class ServerController:
    """Controls the web server lifecycle."""

    def __init__(self, port: Optional[int] = None, enable_on_start: bool = True):
        """
        Initialize ServerController.

        Args:
            port: Port to run the server on. Defaults to settings.server_port.
            enable_on_start: If True, server will start when controller is initialized.
        """
        self.port = port if port is not None else settings.server_port
        self._server_thread = None
        self._actual_port = None
        self._enable_on_start = enable_on_start

    def start(self) -> int:
        """
        Start the web server in background thread.

        Returns:
            The actual port the server is running on.
        """
        if self._server_thread is not None:
            # Server already started
            return self._actual_port

        self._actual_port = _start_server_in_background(port=self.port)
        self._server_thread = True  # Just a flag that it's running
        return self._actual_port

    def stop(self):
        """Stop the web server (if running)."""
        # Currently no stop mechanism in wichy.server, but this is placeholder
        self._server_thread = None
        self._actual_port = None

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._server_thread is not None

    @property
    def actual_port(self) -> Optional[int]:
        """Get the actual port the server is running on."""
        return self._actual_port

    def get_startup_info(self) -> dict:
        """
        Get information to print after server starts.

        Returns:
            Dict with server URL and graph editor URL.
        """
        if not self._actual_port:
            return {}

        return {
            "port": self._actual_port,
            "url": f"http://127.0.0.1:{self._actual_port}",
            "graph_url": f"http://127.0.0.1:{self._actual_port}/tools/graph/",
        }
