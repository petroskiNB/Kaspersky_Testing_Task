"""Fail a build when installed distributions differ from the version lock."""

import argparse
import importlib.metadata
from pathlib import Path
import re


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("requirements", type=Path)
    parser.add_argument("--allow-extra", nargs="*", default=[])
    args = parser.parse_args()
    expected = {}
    for line in args.requirements.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s;]+)", line)
        if not match:
            raise SystemExit("Every requirement must have an exact == version")
        name = canonical(match[1])
        if name in expected:
            raise SystemExit("Duplicate dependency in lock: " + name)
        expected[name] = match[2]
    installed = {canonical(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}
    errors = []
    for name, version in expected.items():
        if installed.get(name) != version:
            errors.append(f"{name}: expected {version}, installed {installed.get(name, 'MISSING')}")
    extras = installed.keys() - expected.keys() - {canonical(n) for n in args.allow_extra}
    errors.extend("Unpinned package: " + name for name in sorted(extras))
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"PASS: {len(expected)} exact package versions verified.")


if __name__ == "__main__":
    main()
