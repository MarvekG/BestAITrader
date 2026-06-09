from pathlib import Path
import sys


WEB_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT_TEXT = str(WEB_ROOT)

if WEB_ROOT_TEXT not in sys.path:
    sys.path.insert(0, WEB_ROOT_TEXT)
