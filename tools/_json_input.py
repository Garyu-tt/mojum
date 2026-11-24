"""Load the shared JSON depth check for tools run from a source checkout."""

import json
from pathlib import Path
import sys

source_dir = str(Path(__file__).resolve().parents[1] / "src")
if source_dir not in sys.path:
    sys.path.insert(0, source_dir)

from mojum._json_nesting import exceeds_json_depth


def backend_json_text(data: bytes) -> str:
    """Use json.loads' byte encoding detection before inspecting the text."""
    return data.decode(json.detect_encoding(data), "surrogatepass")
