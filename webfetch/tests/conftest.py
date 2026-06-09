from pathlib import Path
import sys


WEBFETCH_ROOT = Path(__file__).resolve().parents[1]
WEBFETCH_ROOT_TEXT = str(WEBFETCH_ROOT)

if WEBFETCH_ROOT_TEXT not in sys.path:
    sys.path.insert(0, WEBFETCH_ROOT_TEXT)
