"""BacktesterV3 entry point — launches the NiceGUI web application."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="BacktesterV3 — systematic backtesting with LEAN engine")
    parser.add_argument("--port", type=int, default=8050, help="Port for web UI (default: 8050)")
    parser.add_argument("--output-root", type=str, default="outputs", help="Output directory root")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    from backtester.gui.app import launch

    launch(port=args.port, output_root=args.output_root, reload=args.reload)


if __name__ == "__main__":
    main()
