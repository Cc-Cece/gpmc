"""Verify wheel contents against canonical sources before storing/publishing it."""
import argparse
from pathlib import Path
import sys
import zipfile

from sync_gpmc import package_files

ROOT = Path(__file__).resolve().parents[1]


def verify(path):
    expected = package_files(ROOT / "gpmc")
    with zipfile.ZipFile(path) as bundle:
        actual = {name.removeprefix("gpmc/"): bundle.read(name).replace(b"\r\n", b"\n")
                  for name in bundle.namelist() if name.startswith("gpmc/") and not name.endswith("/")}
        if actual != expected:
            raise ValueError("Wheel is missing or has changed canonical package files")
        metadata = next(name for name in bundle.namelist() if name.endswith(".dist-info/METADATA"))
        if "License-Expression: MIT AND GPL-3.0-only" not in bundle.read(metadata).decode():
            raise ValueError("Missing combined license metadata")
    print(f"PASS: {path.name}: {len(expected)} canonical package files and license metadata")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", nargs="+", type=Path)
    args = parser.parse_args()
    for path in args.wheel:
        verify(path)
