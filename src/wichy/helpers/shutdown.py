"""Simple shutdown event for coordinating shutdown between threads"""

import threading

shutdown_requested = threading.Event()
