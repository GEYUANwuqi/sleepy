"""Isolated, disposable server used only by the browser test suite."""

import argparse
import tempfile
from pathlib import Path

from sleepy.cli import main
from sleepy.security import HASHER
from sleepy.storage import Store

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="18765")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="sleepy-e2e-") as directory:
        store = Store(Path(directory))
        store.initialize()
        store.change_password(HASHER.hash("Initial123!"), must_change=True)
        store.close()
        main(["run", "--data-dir", directory, "--port", args.port], standalone_mode=False)
