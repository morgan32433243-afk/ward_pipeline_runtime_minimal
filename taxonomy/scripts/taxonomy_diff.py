#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


def _labels(path: Path) -> set[str]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    labels = set()
    for item in payload.get("items") or []:
        labels.add(f"{item.get('kind', 'diagnosis')}:{item.get('label', '')}")
    specialties = payload.get("specialties") or {}
    if isinstance(specialties, dict):
        for specialty_id in specialties:
            labels.add(f"service:{specialty_id}")
    else:
        for item in specialties:
            if isinstance(item, dict):
                labels.add(f"service:{item.get('id', '')}")
    return labels


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: taxonomy_diff.py OLD.yml NEW.yml", file=sys.stderr)
        return 2
    old = _labels(Path(sys.argv[1]))
    new = _labels(Path(sys.argv[2]))
    print(json.dumps({"added": sorted(new - old), "removed": sorted(old - new)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
