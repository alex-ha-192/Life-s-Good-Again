#!/usr/bin/env python3
"""Kept for compatibility. Developer Mode renewal now lives in ../lgtv_devmode.py:

    python3 lgtv_devmode.py renew --force     # extend now
    python3 lgtv_devmode.py schedule          # print the timer for this machine
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import lgtv_devmode  # noqa: E402

if __name__ == "__main__":
    sys.argv = [sys.argv[0]] + (sys.argv[1:] or ["renew", "--force"])
    lgtv_devmode.main()
