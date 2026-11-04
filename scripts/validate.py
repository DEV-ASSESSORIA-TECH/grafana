#!/usr/bin/env python3
"""Valida sintaxe dos dashboards (JSON) e contratos (YAML)."""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
errors, warnings, uids = [], [], {}

for path in sorted(ROOT.glob("dashboards/**/*.json")) + sorted(ROOT.glob("references/*.json")):
    rel = path.relative_to(ROOT)
    if path.stat().st_size == 0:
        warnings.append(f"{rel}: vazio (placeholder), ignorado")
        continue
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"{rel}: JSON invalido: {e}")
        continue
    if rel.parts[0] != "dashboards":
        continue
    for key in ("title", "uid", "panels"):
        if not d.get(key):
            errors.append(f"{rel}: campo '{key}' ausente")
    uid = d.get("uid")
    if uid in uids:
        errors.append(f"{rel}: uid '{uid}' repetido em {uids[uid]}")
    elif uid:
        uids[uid] = rel

for path in sorted(ROOT.glob("contracts/**/*.yml")):
    rel = path.relative_to(ROOT)
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        errors.append(f"{rel}: YAML invalido: {e}")

for w in warnings:
    print(f"AVISO  {w}")
for e in errors:
    print(f"ERRO   {e}")
print(f"{len(uids)} dashboards, {len(errors)} erro(s)")
sys.exit(1 if errors else 0)
