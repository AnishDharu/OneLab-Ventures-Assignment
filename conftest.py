"""pytest configuration — adds project root to sys.path."""
import sys
from pathlib import Path

# Ensure the project root is on the path so `app.*` imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))
