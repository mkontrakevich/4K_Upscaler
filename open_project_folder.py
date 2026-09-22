from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
SOURCE = Path(CFG["source"])
OUTPUT = SOURCE / CFG["output_folder"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostics", action="store_true")
    args = parser.parse_args()
    target = OUTPUT / "_diagnostics" if args.diagnostics else OUTPUT
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(str(target))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
