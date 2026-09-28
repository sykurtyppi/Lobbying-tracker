"""
Streamlit dashboard package for the Lobbying Tracker.

Importing this package puts the repository root and ``src/`` on ``sys.path`` so
that ``config`` and the pipeline modules resolve regardless of the working
directory Streamlit was launched from.
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT, os.path.join(_REPO_ROOT, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
