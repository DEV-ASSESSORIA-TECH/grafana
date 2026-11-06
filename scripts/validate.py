#!/usr/bin/env python3
"""Valida os dashboards (Dashboard Schema V2) e os contratos.

1. Sintaxe: JSON dos dashboards e YAML dos contratos. Arquivo vazio e erro.
2. Git Sync: em dashboards/ so ha recursos JSON (Dashboard V2 ou _folder.json);
   cada pasta com dashboards tem _folder.json; metadata so com 'name' (sem campos
   gerados pelo servidor); sem 'status'; UIDs de dashboard e pasta unicos e validos.
3. Schema V2: apiVersion/kind, title, elements e layout presentes; todo painel
   referenciado no layout existe e todo painel existe no layout (uma vez);
   ids de painel unicos.
4. Datasources: nada de importacao manual (__inputs, __requires, __elements,
   ${DS_*}); toda consulta aponta para um datasource provisionado por UID.
5. Contratos: toda metrica usada em consulta Prometheus existe no contrato do
   job fixado na consulta; toda consulta Loki usa job e labels observados.
6. Contrato de alertas: severity e area definidos; jobs de cada area com contrato.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DASHBOARDS = ROOT / "dashboards"
errors, warnings = [], []

DASHBOARD_API = "dashboard.grafana.app/v2"
FOLDER_API = "folder.grafana.app/v1"
FOLDER_FILE = "_folder.json"
# UIDs reais dos datasources provisionados no Grafana (grupo da consulta -> UID)
DATASOURCES = {"prometheus": "prometheus", "loki": "loki"}
# datasources embutidos do Grafana (anotacoes nativas)
BUILTIN_DATASOURCES = {("grafana", "-- Grafana --"), ("datasource", "grafana")}
UID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
LEGACY_KEYS = ("__inputs", "__requires", "__elements")

# ---------------------------------------------------------------- contratos
prom_jobs = {}  # job -> set(metricas)
loki_jobs = {}  # job -> set(labels)
alert_contracts = []  # (arquivo, contrato de labels de alerta)
for path in sorted(ROOT.glob("contracts/**/*.yml")):
    rel = path.relative_to(ROOT)
    try:
        c = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        errors.append(f"{rel}: YAML invalido: {e}")
        continue
    job = c.get("job")
    if c.get("kind") == "observability_contract" and job:
        metrics = set()
        for group in (c.get("metric_groups") or {}).values():
            metrics |= set(group.get("metrics_observed") or [])
        prom_jobs[job] = metrics
    elif c.get("kind") == "loki_log_contract" and job:
        loki_jobs[job] = set((c.get("identity") or {}).get("observed_labels") or [])
    elif c.get("kind") == "alert_label_contract":
        alert_contracts.append((rel, c))

# contrato de alertas: labels obrigatorios e jobs do mapeamento de area
for rel, c in alert_contracts:
    labels = c.get("labels") or {}
    for name in ("severity", "area"):
        if not (labels.get(name) or {}).get("values"):
            errors.append(f"{rel}: label '{name}' sem valores")
    for area, spec in ((labels.get("area") or {}).get("values") or {}).items():
        for job in (spec or {}).get("prometheus_jobs") or []:
            if job not in prom_jobs:
                errors.append(f"{rel}: area {area}: job Prometheus sem contrato: {job}")
        for job in (spec or {}).get("loki_jobs") or []:
            if job not in loki_jobs:
                errors.append(f"{rel}: area {area}: job Loki sem contrato: {job}")

# ------------------------------------------------------------ parser PromQL
PROM_WORDS = {"by", "without", "on", "ignoring", "group_left", "group_right",
              "bool", "and", "or", "unless", "offset", "inf", "nan"}


def prom_metrics(expr):
    """Nomes de metricas usados numa expressao PromQL (aproximado, sem parser)."""
    e = re.sub(r"\$\{?\w+(:\w+)?\}?", "", expr)               # variaveis Grafana
    e = re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'', "", e)  # strings
    e = re.sub(r"\{[^}]*\}", " ", e)                          # seletores
    e = re.sub(r"\[[^\]]*\]", " ", e)                         # ranges
    e = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", e)
    names = set()
    for m in re.finditer(r"(?<![\w.])([a-zA-Z_:][\w:]*)(\s*\()?", e):
        name, is_call = m.group(1), m.group(2)
        if is_call or name.lower() in PROM_WORDS:
            continue
        names.add(name)
    return names


def jobs_in(expr):
    return set(re.findall(r'\bjob\s*=\s*"([^"]+)"', expr))


def check_prom(where, expr):
    jobs = jobs_in(expr)
    metrics = prom_metrics(expr)
    if not metrics:
        return
    if not jobs:
        errors.append(f"{where}: consulta sem job fixo: {expr[:90]}")
        return
    unknown = jobs - prom_jobs.keys()
    if unknown:
        errors.append(f"{where}: job sem contrato: {', '.join(sorted(unknown))}")
        return
    allowed = set().union(*(prom_jobs[j] for j in jobs))
    for m in sorted(metrics - allowed):
        errors.append(f"{where}: metrica fora do contrato ({', '.join(sorted(jobs))}): {m}")


def check_loki(where, expr):
    expr = re.sub(r"`[^`]*`", "``", expr)  # templates (label_format/line_format) nao sao seletores
    for selector in re.findall(r"\{([^}]*)\}", expr):
        labels = dict(re.findall(r'(\w+)\s*(?:=~|!=|!~|=)\s*"([^"]*)"', selector))
        job = labels.get("job")
        if not job:
            errors.append(f"{where}: seletor Loki sem job: {{{selector}}}")
            continue
        if job not in loki_jobs:
            errors.append(f"{where}: job Loki sem contrato: {job}")
            continue
        for label in sorted(set(labels) - loki_jobs[job]):
            errors.append(f"{where}: label Loki nao observado em {job}: {label}")


# -------------------------------------------------------------- schema V2
def check_datasource(where, query):
    """DataQuery V2: grupo conhecido e datasource referenciado pelo UID provisionado."""
    group = query.get("group")
    ds = (query.get("datasource") or {}).get("name")
    if (group, ds) in BUILTIN_DATASOURCES:
        return group
    if group not in DATASOURCES:
        errors.append(f"{where}: tipo de datasource nao suportado: {group!r}")
    elif ds is None:
        errors.append(f"{where}: consulta {group} sem datasource.name (UID)")
    elif ds != DATASOURCES[group]:
        errors.append(f"{where}: datasource {group} com UID {ds!r}, esperado {DATASOURCES[group]!r}")
    return group


def check_query(where, query):
    group = check_datasource(where, query)
    expr = (query.get("spec") or {}).get("expr")
    if not expr:
        return
    if group == "loki":
        check_loki(where, expr)
    elif group == "prometheus":
        check_prom(where, expr)


def layout_refs(layout, where):
    """Nomes de elementos referenciados por qualquer layout V2 (recursivo)."""
    if not isinstance(layout, dict):
        return []
    kind, spec = layout.get("kind"), layout.get("spec") or {}
    if kind == "ElementReference":
        return [layout.get("name")]
    if kind in ("GridLayout", "AutoGridLayout"):
        refs = []
        for item in spec.get("items") or []:
            item_spec = item.get("spec") or {}
            refs += layout_refs(item_spec.get("element"), where)
            refs += layout_refs(item_spec.get("layout"), where)  # GridLayoutRow (legado)
        return refs
    if kind in ("RowsLayout", "TabsLayout"):
        refs = []
        for child in spec.get("rows") or spec.get("tabs") or []:
            refs += layout_refs((child.get("spec") or {}).get("layout"), where)
        return refs
    errors.append(f"{where}: tipo de layout desconhecido: {kind!r}")
    return []


def find_strings(obj, pattern):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from find_strings(v, pattern)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_strings(v, pattern)
    elif isinstance(obj, str) and pattern.search(obj):
        yield obj


def check_metadata(rel, res, allowed_api, kind):
    if res.get("apiVersion") != allowed_api:
        errors.append(f"{rel}: apiVersion {res.get('apiVersion')!r}, esperado {allowed_api!r}")
    if res.get("kind") != kind:
        errors.append(f"{rel}: kind {res.get('kind')!r}, esperado {kind!r}")
    extra = set(res) - {"apiVersion", "kind", "metadata", "spec"}
    if extra:
        errors.append(f"{rel}: campos fora do recurso (gerados pelo servidor?): {', '.join(sorted(extra))}")
    meta = res.get("metadata") or {}
    extra = set(meta) - {"name"}
    if extra:
        errors.append(f"{rel}: metadata so deve ter 'name' (remover {', '.join(sorted(extra))})")
    name = meta.get("name")
    if not name:
        errors.append(f"{rel}: metadata.name (UID) ausente")
    elif not UID_RE.match(name):
        errors.append(f"{rel}: metadata.name invalido (ate 40 caracteres [a-zA-Z0-9_-]): {name}")
    if not isinstance(res.get("spec"), dict):
        errors.append(f"{rel}: spec ausente")
    return name


def check_dashboard(rel, res):
    name = check_metadata(rel, res, DASHBOARD_API, "Dashboard")
    spec = res.get("spec") or {}
    for key in LEGACY_KEYS:
        if key in res or key in spec:
            errors.append(f"{rel}: '{key}' nao e permitido (importacao manual)")
    for s in sorted(set(find_strings(res, re.compile(r"\$\{?DS_")))):
        errors.append(f"{rel}: referencia de importacao manual a datasource: {s[:90]}")
    if "panels" in spec or "schemaVersion" in spec:
        errors.append(f"{rel}: spec no formato classico (panels/schemaVersion); use Schema V2")

    for key in ("title", "elements", "layout"):
        if not spec.get(key):
            errors.append(f"{rel}: campo 'spec.{key}' ausente")

    # layout <-> elementos
    elements = spec.get("elements") or {}
    refs = layout_refs(spec.get("layout"), rel)
    for ref in sorted(set(refs) - set(elements)):
        errors.append(f"{rel}: layout referencia elemento inexistente: {ref}")
    for key in sorted(set(elements) - set(refs)):
        errors.append(f"{rel}: elemento fora do layout (nao aparece no dashboard): {key}")
    for key in sorted({r for r in refs if refs.count(r) > 1}):
        errors.append(f"{rel}: elemento usado mais de uma vez no layout: {key}")

    # paineis e consultas
    ids = {}
    for key, el in elements.items():
        el_spec = el.get("spec") or {}
        if el.get("kind") != "Panel":
            warnings.append(f"{rel}: elemento {key} do tipo {el.get('kind')}: consultas nao verificadas")
            continue
        pid = el_spec.get("id")
        if pid in ids:
            errors.append(f"{rel}: id de painel {pid} repetido ({ids[pid]}, {key})")
        ids[pid] = key
        where = f"{rel} [{el_spec.get('title') or key}]"
        if not (el_spec.get("vizConfig") or {}).get("group"):
            errors.append(f"{where}: vizConfig.group (tipo do painel) ausente")
        queries = ((el_spec.get("data") or {}).get("spec") or {}).get("queries") or []
        for q in queries:
            check_query(where, (q.get("spec") or {}).get("query") or {})

    # variaveis
    for var in spec.get("variables") or []:
        vspec = var.get("spec") or {}
        where = f"{rel} [variavel {vspec.get('name')}]"
        if var.get("kind") == "DatasourceVariable":
            errors.append(f"{where}: variavel de datasource; os UIDs sao fixos")
        if var.get("kind") not in ("QueryVariable", "AdhocVariable", "GroupByVariable"):
            continue
        query = vspec.get("query") or {}
        if var.get("kind") == "QueryVariable":
            group = check_datasource(where, query)
        else:
            group = check_datasource(where, {"group": (vspec.get("datasource") or {}).get("type"),
                                             "datasource": {"name": (vspec.get("datasource") or {}).get("uid")}})
        q = (query.get("spec") or {}).get("query") or vspec.get("definition") or ""
        q = q.get("query", "") if isinstance(q, dict) else q
        m = re.match(r"\s*label_values\((.*),\s*\w+\s*\)\s*$", q)
        if m and group == "prometheus":
            check_prom(where, m.group(1))

    # anotacoes
    for ann in spec.get("annotations") or []:
        aspec = ann.get("spec") or {}
        check_query(f"{rel} [anotacao {aspec.get('name')}]", aspec.get("query") or {})
    return name


def check_folder(rel, res):
    name = check_metadata(rel, res, FOLDER_API, "Folder")
    if not (res.get("spec") or {}).get("title"):
        errors.append(f"{rel}: spec.title da pasta ausente")
    return name


# --------------------------------------------------------------- dashboards
uids, folder_uids = {}, {}
dirs_with_dashboards, dirs_with_folder = set(), set()
for path in sorted(p for p in DASHBOARDS.rglob("*") if p.is_file()):
    rel = path.relative_to(ROOT)
    if path.suffix != ".json":
        errors.append(f"{rel}: arquivo nao-JSON em dashboards/ (pasta sincronizada pelo Git Sync)")
        continue
    if not path.read_text(encoding="utf-8").strip():
        errors.append(f"{rel}: arquivo vazio")
        continue
    try:
        res = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        errors.append(f"{rel}: JSON invalido: {e}")
        continue
    if not isinstance(res, dict):
        errors.append(f"{rel}: esperado um objeto JSON")
        continue

    if path.name == FOLDER_FILE:
        dirs_with_folder.add(path.parent)
        uid, seen = check_folder(rel, res), folder_uids
    else:
        dirs_with_dashboards.add(path.parent)
        uid, seen = check_dashboard(rel, res), uids
    if uid in seen:
        errors.append(f"{rel}: uid '{uid}' repetido em {seen[uid]}")
    elif uid:
        seen[uid] = rel

for d in sorted(dirs_with_dashboards - dirs_with_folder):
    if d != DASHBOARDS:
        errors.append(f"{d.relative_to(ROOT)}: pasta sem {FOLDER_FILE} (UID da pasta ficaria instavel no Git Sync)")
for d in sorted(dirs_with_folder - dirs_with_dashboards):
    warnings.append(f"{d.relative_to(ROOT)}: pasta sem dashboards")
for uid in sorted(set(uids) & set(folder_uids)):
    errors.append(f"uid '{uid}' usado por dashboard ({uids[uid]}) e pasta ({folder_uids[uid]})")

for w in warnings:
    print(f"AVISO  {w}")
for e in errors:
    print(f"ERRO   {e}")
print(f"{len(uids)} dashboards, {len(folder_uids)} pastas, {len(prom_jobs)} contratos Prometheus, "
      f"{len(loki_jobs)} contratos Loki, {len(errors)} erro(s)")
sys.exit(1 if errors else 0)
