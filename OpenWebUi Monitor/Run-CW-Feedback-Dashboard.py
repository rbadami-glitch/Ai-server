"""Run the read-only feedback dashboard with a persistent local access token.

The Scheduled Task runs this launcher as SYSTEM. The token stays outside Git in
a restricted file and is never printed by this process.
"""

import argparse
import importlib.util
import re
from http.server import ThreadingHTTPServer
from pathlib import Path


SOURCE = Path(__file__).with_name("OpenWebUI-Feedback-Dashboard.py")


def read_token(path):
    token = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", token):
        raise ValueError("Feedback dashboard access-token file is invalid.")
    return token


def load_dashboard():
    spec = importlib.util.spec_from_file_location("cw_feedback_dashboard", SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Feedback dashboard source could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    token = read_token(args.token_file)
    dashboard = load_dashboard()
    database = dashboard.DEFAULT_DATABASE
    if not database.is_file():
        parser.error(f"Open WebUI database not found: {database}")
    dashboard.read_feedback(database)
    server = ThreadingHTTPServer(
        (dashboard.HOST, dashboard.DEFAULT_PORT),
        dashboard.make_handler(database, token),
    )
    server.daemon_threads = True
    print(f"Feedback dashboard listening on {dashboard.HOST}:{dashboard.DEFAULT_PORT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
