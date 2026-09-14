"""Compatibility entrypoint. The canonical application lives in robot_forum/."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "robot_forum"))
from robot_forum.app import app
