import sys
from pathlib import Path

# Add src/ to path so tests can import config.manager, memory.manager, etc.
# Relative imports inside manager functions (e.g. from ..ha.ha_websocket) are
# only executed when those code paths are actually called — our tests avoid them.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
