"""Script entry: the settings buttons (RunScript(service.kofin.companion,<action>))."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib"))

from companion.actions import main  # noqa: E402

if __name__ == "__main__":
    main(sys.argv[1:])
