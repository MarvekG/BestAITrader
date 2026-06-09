from pathlib import Path
import sys


SANDBOX_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_ROOT_TEXT = str(SANDBOX_ROOT)

if SANDBOX_ROOT_TEXT not in sys.path:
    sys.path.insert(0, SANDBOX_ROOT_TEXT)
