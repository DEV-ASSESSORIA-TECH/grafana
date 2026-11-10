#!/usr/bin/env python3
"""Aplica alerting/inhibition no Grafana pela App Platform API (InhibitionRule, v1beta1).

Regras de inibicao nao sao provisionaveis por arquivo e nao tem UI. Este script compara o repositorio
com o Grafana e cria/atualiza as regras. Sem --apply so mostra a diferenca.
Com --prune tambem apaga do Grafana as regras que nao estao no repositorio.

Autenticacao: token de service account (Admin) em GRAFANA_TOKEN, ou --user/--password.

Uso:
    export GRAFANA_URL=https://grafana.exemplo GRAFANA_TOKEN=glsa_...
    python scripts/alerting_inhibition.py            # diferenca
    python scripts/alerting_inhibition.py --apply    # aplica
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
API = "notifications.alerting.grafana.app/v1beta1"

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://localhost:3000"))
ap.add_argument("--user", help="usuario (sem GRAFANA_TOKEN)")
ap.add_argument("--password", help="senha (sem GRAFANA_TOKEN)")
ap.add_argument("--namespace", default="default", help="namespace da org (org 1 = default)")
ap.add_argument("--apply", action="store_true", help="cria/atualiza no Grafana")
ap.add_argument("--prune", action="store_true", help="com --apply, apaga regras que nao estao no repositorio")
args = ap.parse_args()

token = os.environ.get("GRAFANA_TOKEN")
if token:
    AUTH = f"Bearer {token}"
elif args.user:
    AUTH = "Basic " + base64.b64encode(f"{args.user}:{args.password or ''}".encode()).decode()
else:
    sys.exit("defina GRAFANA_TOKEN ou --user/--password")
BASE = f"{args.url.rstrip('/')}/apis/{API}/namespaces/{args.namespace}/inhibitionrules"


def api(method, path="", body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": AUTH, "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {BASE + path}: HTTP {e.code}: {e.read().decode()[:500]}")


def norm(spec):
    """Spec comparavel: o Grafana reordena os matchers por label; equal ausente = []."""
    spec = dict(spec or {})
    for key in ("source_matchers", "target_matchers"):
        spec[key] = sorted((spec.get(key) or []), key=lambda m: json.dumps(m, sort_keys=True))
    spec["equal"] = sorted(spec.get("equal") or [])
    return json.dumps(spec, sort_keys=True)


desired = {}
for path in sorted((ROOT / "alerting" / "inhibition").glob("*.yaml")):
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if doc:
            desired[doc["metadata"]["name"]] = doc

current = {i["metadata"]["name"]: i for i in api("GET").get("items") or []}
changes = 0
for name, doc in desired.items():
    got = current.get(name)
    if got is None:
        print(f"criar      {name}")
        changes += 1
        if args.apply:
            api("POST", body={"apiVersion": API, "kind": "InhibitionRule",
                              "metadata": {"name": name}, "spec": doc["spec"]})
    elif norm(got.get("spec")) != norm(doc["spec"]):
        print(f"atualizar  {name}")
        changes += 1
        if args.apply:
            api("PUT", f"/{name}", {"apiVersion": API, "kind": "InhibitionRule",
                                    "metadata": {"name": name, "resourceVersion": got["metadata"]["resourceVersion"]},
                                    "spec": doc["spec"]})
    else:
        print(f"igual      {name}")
for name in sorted(set(current) - set(desired)):
    changes += 1
    if args.prune:
        print(f"apagar     {name}")
        if args.apply:
            api("DELETE", f"/{name}")
    else:
        print(f"so no Grafana (use --prune para apagar)  {name}")

if not args.apply and changes:
    print(f"{changes} diferenca(s); rode com --apply para aplicar")
