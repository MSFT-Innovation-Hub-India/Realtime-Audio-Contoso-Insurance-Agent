import sys
from pathlib import Path

# make the app modules (app.py, voicelive_client.py, ...) importable from the tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
