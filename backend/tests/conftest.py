import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Most tests exercise the API without a session; auth tests switch this on explicitly.
os.environ.setdefault("REQUIRE_LOGIN", "false")
