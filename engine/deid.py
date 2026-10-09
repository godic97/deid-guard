#!/usr/bin/env python3
"""deid-guard engine entry point. Standard library only, Python 3.9+."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deidlib.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
