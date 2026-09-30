"""Entry point of TreeAICoach.exe (PyInstaller).

Using this tiny script instead of ``treeaicoach/__main__.py`` keeps the package directory
off ``sys.path`` in the frozen app (modules are only importable as ``treeaicoach.*``).
"""

import multiprocessing
import sys


def _run() -> int:
    from treeaicoach.main import main

    return main()


if __name__ == "__main__":
    multiprocessing.freeze_support()   # harmless; required if a dependency ever spawns processes
    sys.exit(_run())
