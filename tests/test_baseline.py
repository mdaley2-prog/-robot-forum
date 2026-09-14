import ast
import unittest
from pathlib import Path

class SourceBaseline(unittest.TestCase):
    def test_source_parses(self):
        ast.parse(Path("robot_forum/app.py").read_text())

if __name__ == "__main__":
    unittest.main()
