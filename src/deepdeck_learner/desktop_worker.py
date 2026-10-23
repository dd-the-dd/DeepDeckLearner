"""Console entry point bundled beside the windowed desktop launcher."""

from __future__ import annotations

import multiprocessing
import runpy
import sys

from .runtime import WORKER_MODULES


def main() -> None:
    multiprocessing.freeze_support()
    if len(sys.argv) < 3 or sys.argv[1] != "-m" or sys.argv[2] not in WORKER_MODULES:
        raise SystemExit("This helper runs only DeepDeck's bundled programmatic agents.")
    module = sys.argv[2]
    sys.argv = [module, *sys.argv[3:]]
    runpy.run_module(module, run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
