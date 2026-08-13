"""Make the project root importable during tests.

``python -m pytest`` happens to add the current directory to ``sys.path``, but a
bare ``pytest`` does not, and Streamlit's ``AppTest`` harness runs ``ui.py``
without the script-directory entry that ``streamlit run`` would provide. Adding
the root explicitly makes ``import config`` / ``import services`` work in every
one of those cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
