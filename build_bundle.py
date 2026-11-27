"""Build a self-contained trusted-provider bundle without modifying legacy code."""
from pathlib import Path

def bundle(root):
    root = Path(root)
    return ((root/"sdk_manifest.json").read_bytes(),
            (root/"src/mojum/genesis.py").read_bytes()+b"\n\n"+
            (root/"sdk_adapter.py").read_bytes())
