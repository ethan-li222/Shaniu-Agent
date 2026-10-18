"""Run from the project root: python run.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'backend'))
from shaniu.launcher import main

if __name__ == '__main__':
    raise SystemExit(main())
