"""Обёртка: тест стратегии HYPEUSDT.

    python scripts/test_strategy_hype.py
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "test_strategy.py"

if __name__ == "__main__":
    argv = list(sys.argv[1:])
    if "--config" not in argv:
        argv = ["--config", "config/settings_hype.yaml", "HYPEUSDT", *argv]
    sys.argv = [str(SCRIPT), *argv]
    runpy.run_path(str(SCRIPT), run_name="__main__")
