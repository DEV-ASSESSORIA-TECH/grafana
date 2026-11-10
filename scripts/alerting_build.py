#!/usr/bin/env python3
"""Achata alerting/rules e alerting/notifications numa pasta de provisionamento do Grafana.

O Grafana le so os arquivos do primeiro nivel de provisioning/alerting (subpastas sao ignoradas sem erro).
Cada arquivo vira <secao>-<nome>.yaml: alerting/rules/hypervisor.yaml -> rules-hypervisor.yaml.
alerting/inhibition fica de fora (nao e provisionavel por arquivo; ver alerting_inhibition.py).

Arquivos rules-*.yaml e notifications-*.yaml do destino que nao vieram do repositorio sao removidos;
os demais arquivos do destino nao sao tocados. Remover um arquivo NAO apaga a regra no Grafana:
para isso use deleteRules (ver alerting/README.md).

Uso:
    python scripts/alerting_build.py --out /opt/observability/grafana/provisioning/alerting
    curl -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" $GRAFANA_URL/api/admin/provisioning/alerting/reload
"""
import argparse
import sys
from pathlib import Path

import catalogo

ROOT = Path(__file__).resolve().parent.parent
SECTIONS = ("rules", "notifications")

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--out", required=True, help="pasta provisioning/alerting de destino")
ap.add_argument("--dry-run", action="store_true", help="so mostra o que seria feito")
args = ap.parse_args()

out = Path(args.out)
cat, errors = catalogo.load()
if errors:
    sys.exit("catalogo.yml invalido:\n  " + "\n  ".join(errors))
wanted = {}
for section in SECTIONS:
    for src in sorted((ROOT / "alerting" / section).glob("*.yaml")):
        wanted[f"{section}-{src.name}"] = src

if not args.dry_run:
    out.mkdir(parents=True, exist_ok=True)
for name, src in wanted.items():
    dst = out / name
    text, errors = catalogo.render(src.read_text(encoding="utf-8"), cat)
    if errors:
        sys.exit(f"{src.relative_to(ROOT)}: " + "; ".join(errors))
    data = text.encode("utf-8")
    same = dst.exists() and dst.read_bytes() == data
    print(f"{'igual ' if same else 'copia '} {src.relative_to(ROOT)} -> {dst}")
    if not same and not args.dry_run:
        dst.write_bytes(data)
        dst.chmod(0o644)  # o Grafana nao sobe se nao conseguir ler um arquivo de provisionamento
for section in SECTIONS:
    for stale in sorted(out.glob(f"{section}-*.yaml")) if out.exists() else []:
        if stale.name not in wanted:
            print(f"remove  {stale}")
            if not args.dry_run:
                stale.unlink()
