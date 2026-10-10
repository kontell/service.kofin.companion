"""Entry point of the companion service: everything lives under lib/."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from companion.runner import run  # noqa: E402

if __name__ == "__main__":
    run()
