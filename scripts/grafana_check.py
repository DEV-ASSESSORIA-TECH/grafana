#!/usr/bin/env python3
"""Sincroniza dashboards/ num Grafana real, como o Git Sync faz, e confere o resultado.

Cria um repositorio de provisionamento do tipo 'local' (mesmo motor de leitura e
sincronizacao do Git Sync) apontando para a pasta dashboards/ montada no
container do Grafana e verifica:

- o sync termina com sucesso, sem avisos (ex.: pasta sem _folder.json);
- cada dashboard e cada pasta do repositorio existem no Grafana, com o UID do arquivo;
- o spec V2 armazenado e identico ao do arquivo;
- o dashboard ficou na pasta do _folder.json do seu diretorio;
- a leitura pela API v1 converte sem falha (clientes legados continuam lendo).

Uso (Grafana 13.0.3 com o feature toggle 'provisioning' e a pasta montada):

    docker run -d --name grafana -p 3000:3000 \\
      -v "$PWD/dashboards:/repo/dashboards:ro" \\
      -e GF_FEATURE_TOGGLES_ENABLE=provisioning,kubernetesDashboards \\
      -e GF_PATHS_PERMITTED_PROVISIONING_PATHS=/repo \\
      grafana/grafana:13.0.3
    python scripts/grafana_check.py --url http://localhost:3000 --path /repo/dashboards
"""
import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = ROOT / "dashboards"
REPO = "grafana-dashboards-check"
PROV = "provisioning.grafana.app/v1beta1"

ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
ap.add_argument("--url", default="http://localhost:3000")
ap.add_argument("--user", default="admin")
ap.add_argument("--password", default="admin")
ap.add_argument("--path", default="/repo/dashboards", help="pasta dashboards/ vista de dentro do container")
ap.add_argument("--namespace", default="default")
ap.add_argument("--timeout", type=int, default=120)
args = ap.parse_args()

AUTH = "Basic " + base64.b64encode(f"{args.user}:{args.password}".encode()).decode()


def api(method, path, body=None, ok404=False):
    req = urllib.request.Request(
        args.url.rstrip("/") + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": AUTH, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if ok404 and e.code == 404:
            return None
        raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read().decode()[:500]}")


def wait(what, fn):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(2)
    raise SystemExit(f"tempo esgotado esperando {what}")


def health():
    try:
        return api("GET", "/api/health")
    except OSError:  # Grafana ainda subindo
        return None


wait("Grafana responder", health)
ns = f"/apis/{PROV}/namespaces/{args.namespace}"

# repositorio local -> mesmo pipeline de leitura/sync do Git Sync
if api("GET", f"{ns}/repositories/{REPO}", ok404=True):
    api("DELETE", f"{ns}/repositories/{REPO}")
    wait("remocao do repositorio anterior", lambda: api("GET", f"{ns}/repositories/{REPO}", ok404=True) is None)
api("POST", f"{ns}/repositories", {
    "apiVersion": PROV, "kind": "Repository", "metadata": {"name": REPO},
    "spec": {"title": "grafana-dashboards (verificacao)", "type": "local",
             "local": {"path": args.path}, "workflows": [],
             "sync": {"enabled": True, "target": "folder", "intervalSeconds": 60}},
})


def synced():
    st = (api("GET", f"{ns}/repositories/{REPO}") or {}).get("status") or {}
    sync = st.get("sync") or {}
    return st if sync.get("state") in ("success", "warning", "error") and sync.get("finished") else None


status = wait("o sync terminar", synced)
errors = []
sync = status["sync"]
print(f"sync: {sync['state']}")
for msg in sync.get("message") or []:
    print(f"  {msg}")
if sync["state"] != "success":
    errors.append(f"sync terminou com estado '{sync['state']}'")

# pastas
folders = {}
for f in sorted(DASHBOARDS.rglob("_folder.json")):
    res = json.loads(f.read_text(encoding="utf-8"))
    uid = res["metadata"]["name"]
    folders[f.parent] = uid
    got = api("GET", f"/apis/folder.grafana.app/v1/namespaces/{args.namespace}/folders/{uid}", ok404=True)
    if not got:
        errors.append(f"{f.relative_to(ROOT)}: pasta {uid} nao criada")
    elif got["spec"].get("title") != res["spec"]["title"]:
        errors.append(f"{f.relative_to(ROOT)}: titulo {got['spec'].get('title')!r} != {res['spec']['title']!r}")

# dashboards
count = 0
for f in sorted(DASHBOARDS.rglob("*.json")):
    if f.name == "_folder.json":
        continue
    rel = f.relative_to(ROOT)
    res = json.loads(f.read_text(encoding="utf-8"))
    uid = res["metadata"]["name"]
    base = f"/apis/dashboard.grafana.app/{{}}/namespaces/{args.namespace}/dashboards/{uid}"
    got = api("GET", base.format("v2"), ok404=True)
    if not got:
        errors.append(f"{rel}: dashboard {uid} nao sincronizado")
        continue
    count += 1
    ann = got["metadata"].get("annotations") or {}
    if ann.get("grafana.app/managerId") != REPO:
        errors.append(f"{rel}: nao gerenciado pelo repositorio ({ann.get('grafana.app/managerId')})")
    if ann.get("grafana.app/folder") != folders.get(f.parent):
        errors.append(f"{rel}: na pasta {ann.get('grafana.app/folder')}, esperado {folders.get(f.parent)}")
    if got["spec"] != res["spec"]:
        errors.append(f"{rel}: spec armazenado difere do arquivo")
    v1 = api("GET", base.format("v1"))
    if ((v1.get("status") or {}).get("conversion") or {}).get("failed"):
        errors.append(f"{rel}: conversao V2 -> v1 falhou: {v1['status']['conversion']}")
    print(f"ok  {rel} -> {uid} (pasta {ann.get('grafana.app/folder')})")

for e in errors:
    print(f"ERRO   {e}")
print(f"{count} dashboards e {len(folders)} pastas sincronizados, {len(errors)} erro(s)")
sys.exit(1 if errors else 0)
