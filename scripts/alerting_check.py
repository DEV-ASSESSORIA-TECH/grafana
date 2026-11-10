#!/usr/bin/env python3
"""Confere num Grafana real se alerting/ foi carregado como esta no repositorio.

Pressupoe o Grafana subido com a saida de alerting_build.py em provisioning/alerting e
alerting_inhibition.py --apply ja executado. Autenticacao: GRAFANA_TOKEN (service account) ou --user/--password.
Verifica:

- cada regra existe com provenance 'file', na pasta e no grupo do arquivo, com titulo, labels (depois da
  interpolacao: $$labels vira $labels), annotations, for e consultas iguais as do arquivo;
- mute timings e templates provisionados por arquivo e iguais aos do repositorio (policies e contact points
  sao da UI e nao sao conferidos);
- regras de inibicao do repositorio presentes no Grafana com o mesmo spec.

Uso:
    python scripts/alerting_check.py --url http://localhost:3000
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalogo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INHIBITION = "/apis/notifications.alerting.grafana.app/v1beta1/namespaces/{ns}/inhibitionrules"

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://localhost:3000"))
ap.add_argument("--user", default="admin")
ap.add_argument("--password", default="admin")
ap.add_argument("--namespace", default="default")
ap.add_argument("--timeout", type=int, default=120)
args = ap.parse_args()
if os.environ.get("GRAFANA_TOKEN"):
    AUTH = f"Bearer {os.environ['GRAFANA_TOKEN']}"
else:
    AUTH = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()
errors = []


def api(path, ok404=False):
    req = urllib.request.Request(args.url.rstrip("/") + path, headers={"Authorization": AUTH})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        raise SystemExit(f"GET {path}: HTTP {e.code}: {e.read().decode()[:500]}")


def wait_health():
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        try:
            if api("/api/health"):
                return
        except OSError:
            pass
        time.sleep(2)
    raise SystemExit("tempo esgotado esperando o Grafana")


CATALOGO = catalogo.load()[0]


def load(section):
    docs = []
    for path in sorted((ROOT / "alerting" / section).glob("*.yaml")):
        text = catalogo.render(path.read_text(encoding="utf-8"), CATALOGO)[0]
        docs += [(path.relative_to(ROOT), d) for d in yaml.safe_load_all(text) if d]
    return docs


def unescape(value):
    """O que o provisionamento grava: $$ vira $."""
    return value.replace("$$", "$") if isinstance(value, str) else value


def same(where, what, got, want):
    if got != want:
        errors.append(f"{where}: {what} difere\n        Grafana: {json.dumps(got, ensure_ascii=False)[:300]}"
                      f"\n        arquivo: {json.dumps(want, ensure_ascii=False)[:300]}")


def seconds(duration):
    units = {"s": 1, "m": 60, "h": 3600}
    return int(duration[:-1]) * units[duration[-1]] if isinstance(duration, str) else duration


wait_health()
folders = {f["uid"]: f["title"] for f in api("/api/folders")}

# regras
count = 0
for rel, doc in load("rules"):
    for group in doc.get("groups") or []:
        for rule in group["rules"]:
            where = f"{rel} [{rule['uid']}]"
            got = api(f"/api/v1/provisioning/alert-rules/{rule['uid']}", ok404=True)
            if not got:
                errors.append(f"{where}: regra nao carregada")
                continue
            count += 1
            if got.get("provenance") != "file":
                errors.append(f"{where}: provenance {got.get('provenance')!r}, esperado 'file'")
            same(where, "pasta", folders.get(got["folderUID"]), group["folder"])
            same(where, "grupo", got["ruleGroup"], group["name"])
            same(where, "titulo", got["title"], rule["title"])
            same(where, "labels", got.get("labels") or {}, {k: unescape(v) for k, v in rule["labels"].items()})
            same(where, "annotations", got.get("annotations") or {}, rule["annotations"])
            same(where, "for", seconds(got["for"]), seconds(rule["for"]))
            same(where, "consultas", [q["model"].get("expr") for q in got["data"]],
                 [q["model"].get("expr") for q in rule["data"]])
            for key in ("condition", "noDataState", "execErrState", "isPaused"):
                same(where, key, got.get(key), rule.get(key))
            print(f"ok  regra {rule['title']}")

# notificacoes
for rel, doc in load("notifications"):
    for mute in doc.get("muteTimes") or []:
        got = api(f"/api/v1/provisioning/mute-timings/{mute['name']}", ok404=True)
        if not got:
            errors.append(f"{rel}: mute timing {mute['name']} nao carregado")
            continue
        if got.get("provenance") != "file":
            errors.append(f"{rel}: mute timing {mute['name']} com provenance {got.get('provenance')!r}")
        print(f"ok  mute timing {mute['name']}")
    for tpl in doc.get("templates") or []:
        got = api(f"/api/v1/provisioning/templates/{tpl['name']}", ok404=True)
        if not got:
            errors.append(f"{rel}: template {tpl['name']} nao carregado")
            continue
        if got.get("provenance") != "file":
            errors.append(f"{rel}: template {tpl['name']} com provenance {got.get('provenance')!r}")
        same(f"{rel} [{tpl['name']}]", "template", got.get("template"), tpl["template"])
        print(f"ok  template {tpl['name']}")

# inibicao
current = {i["metadata"]["name"]: i["spec"] for i in
           api(INHIBITION.format(ns=args.namespace)).get("items") or []}


def norm(spec):
    spec = dict(spec)
    for key in ("source_matchers", "target_matchers"):
        spec[key] = sorted(spec.get(key) or [], key=lambda m: json.dumps(m, sort_keys=True))
    spec["equal"] = sorted(spec.get("equal") or [])
    return spec


for rel, doc in load("inhibition"):
    name = doc["metadata"]["name"]
    if name not in current:
        errors.append(f"{rel} [{name}]: regra de inibicao ausente no Grafana")
        continue
    same(f"{rel} [{name}]", "spec", norm(current[name]), norm(doc["spec"]))
    print(f"ok  inibicao {name}")

for e in errors:
    print(f"ERRO   {e}")
print(f"{count} regras conferidas, {len(errors)} erro(s)")
sys.exit(1 if errors else 0)
