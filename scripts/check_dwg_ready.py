from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def detect_converter() -> str | None:
    explicit = os.environ.get("ODAFILECONVERTER_PATH")
    if explicit and Path(explicit).exists():
        return explicit

    local_candidate = ROOT / "third_party" / "oda" / "ODAFileConverter"
    if local_candidate.exists():
        return str(local_candidate)

    return shutil.which("ODAFileConverter")


def main() -> int:
    converter = detect_converter()
    if converter:
        print(f"DWG ready: {converter}")
        return 0

    print("DWG not ready.")
    print("Place ODAFileConverter at third_party/oda/ODAFileConverter or set ODAFILECONVERTER_PATH.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())