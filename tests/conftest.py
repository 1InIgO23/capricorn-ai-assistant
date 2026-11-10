"""Shared test setup: isolate the app data folder and make the package importable."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("CAPRI_HOME", tempfile.mkdtemp(prefix="capri_test_"))
