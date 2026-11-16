#!/usr/bin/env python
"""Root wrapper for Django's command-line utility (delegates to src/)."""
import os
from pathlib import Path
import sys

# Prevent Python from generating __pycache__/*.pyc files.
sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")


def main() -> None:
    """Run administrative tasks with src/ on sys.path."""
    src_dir = Path(__file__).resolve().parent / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "hello_world.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
