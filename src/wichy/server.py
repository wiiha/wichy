"""
Central Flask server for wichy web tools.

This module provides a Flask application factory that serves all web-based tools
via blueprints. The server runs in a background thread when wichy starts.
"""

import logging
import socket
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify

from wichy.config import settings


def get_logs_dir() -> Path:
    """Get the logs directory relative to workspace, creating it if needed."""
    logs_dir = settings.logs_dir
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def is_port_available(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a port is available for binding."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            return result != 0  # Port is available if connection fails
    except:
        return True


def find_available_port(start_port: int = 7891, host: str = "127.0.0.1", max_attempts: int = 100) -> int:
    """Find an available port starting from start_port."""
    for port in range(start_port, start_port + max_attempts):
        if is_port_available(port, host):
            return port
    raise RuntimeError(f"Could not find available port after {max_attempts} attempts starting from {start_port}")


def setup_logging() -> None:
    """Configure logging for the Flask app and werkzeug."""
    logs_dir = get_logs_dir()

    # Create rotating file handler
    file_handler = RotatingFileHandler(
        logs_dir / "server.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    file_handler.setLevel(logging.INFO)

    # Configure werkzeug logger to only log to file, not to stderr
    werkzeug_logger = logging.getLogger("werkzeug")
    werkzeug_logger.handlers = []  # Remove existing handlers
    werkzeug_logger.addHandler(file_handler)
    werkzeug_logger.setLevel(logging.INFO)
    werkzeug_logger.propagate = False  # Don't propagate to root logger

    # Configure werkzeug's _internal logger as well
    werkzeug_internal = logging.getLogger("werkzeug._internal")
    werkzeug_internal.handlers = []
    werkzeug_internal.addHandler(file_handler)
    werkzeug_internal.setLevel(logging.INFO)
    werkzeug_internal.propagate = False


def create_app() -> Flask:
    """Create and configure the Flask application."""
    # Setup logging (to file only, not stderr)
    setup_logging()

    app = Flask(__name__)

    # Health check endpoint
    @app.route("/health")
    def health():
        return jsonify({"status": "ok"})

    # Register tool blueprints
    register_blueprints(app)

    app.logger.info("Wichy server initialized")

    return app


def register_blueprints(app: Flask) -> None:
    """Register all tool blueprints with the Flask app."""
    from wichy.tools.graph import register as register_graph

    register_graph(app)


_server_thread: threading.Thread | None = None
_server_app: Flask | None = None
_server_port: int | None = None


def run_server(port: int | None = None, host: str = "127.0.0.1") -> None:
    """Run the Flask development server in the current thread."""
    if port is None:
        port = settings.server_port
    app = create_app()
    app.logger.info(f"Starting wichy server on {host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def start_server_in_background(port: int | None = None, host: str = "127.0.0.1") -> int:
    """Start the Flask server in a background thread.
    
    Returns:
        The actual port number the server is running on.
    """
    global _server_thread, _server_app, _server_port

    if _server_thread is not None and _server_thread.is_alive():
        return _server_port  # Server already running, return its port

    # Use settings port if not specified
    if port is None:
        port = settings.server_port

    # Find an available port
    actual_port = find_available_port(port, host)

    _server_app = create_app()
    _server_app.logger.info(f"Starting wichy server in background on {host}:{actual_port}")

    def run_app():
        from werkzeug.serving import run_simple

        run_simple(
            host,
            actual_port,
            _server_app,
            threaded=True,
            use_reloader=False,
            use_debugger=False,
        )

    _server_thread = threading.Thread(target=run_app, daemon=True)
    _server_thread.start()
    _server_port = actual_port

    return actual_port


def stop_background_server() -> None:
    """Stop the background Flask server if running."""
    global _server_thread, _server_app, _server_port

    if _server_thread is not None:
        # werkzeug doesn't have a clean way to stop from another thread
        # The daemon thread will be killed when the main process exits
        _server_thread = None
        _server_app = None
        _server_port = None


def is_server_running() -> bool:
    """Check if the background server is running."""
    return _server_thread is not None and _server_thread.is_alive()


def get_server_port() -> int | None:
    """Get the port number of the running server, or None if not running."""
    if _server_thread is not None and _server_thread.is_alive():
        return _server_port
    return None


if __name__ == "__main__":
    run_server()