"""FastAPI web application entry point for CST Agent Workbench.

Launch with:
    python -m cst_agent_workbench.web_app                # connect to real CST
    python -m cst_agent_workbench.web_app --dry-run      # offline demo mode
    python -m cst_agent_workbench.web_app --port 8787    # custom port
"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="CST Agent Workbench Web UI")
    parser.add_argument("--dry-run", "--demo", action="store_true", help="Run in offline demo mode (no CST needed)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="Bind port (default: 8787)")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is required. Install with: pip install -e .[web]")
        sys.exit(1)

    from cst_agent_workbench.web_api import create_app

    app = create_app(dry_run=args.dry_run)
    mode = "dry-run (offline)" if args.dry_run else "live (CST connected)"
    print(f"Starting CST Agent Workbench web server ({mode})")
    print(f"  API:  http://{args.host}:{args.port}")
    print(f"  Docs: http://{args.host}:{args.port}/docs")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
