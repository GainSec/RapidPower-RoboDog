"""
Entry point for running the server as a module.

Usage:
    python -m robodog.server [--simulate] [--host HOST] [--port PORT]
"""

from .server import main

if __name__ == "__main__":
    main()
