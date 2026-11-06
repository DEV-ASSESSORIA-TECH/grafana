#!/usr/bin/env python3
"""Valida os dashboards e contratos.

1. Sintaxe: JSON dos dashboards/referencias e YAML dos contratos.
2. Dashboards: title, uid e panels presentes; uid unico.
3. Contratos: toda metrica usada em consulta Prometheus existe no contrato
   do job fixado na consulta; toda consulta Loki usa job e labels observados.
4. Contrato de alertas: severity e area definidos; jobs de cada area com contrato.
"""
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
errors, warnings = [], []

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


def ds_type(obj, inherited):
    ds = obj.get("datasource")
    if isinstance(ds, dict):
        if ds.get("type"):
            return ds["type"]
        if "LOKI" in str(ds.get("uid", "")).upper():
            return "loki"
    return inherited


def iter_panels(panels):
    for p in panels or []:
        yield p
        yield from iter_panels(p.get("panels"))


# --------------------------------------------------------------- dashboards
uids = {}
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

    for var in (d.get("templating") or {}).get("list", []):
        if var.get("type") != "query":
            continue
        q = var.get("query")
        q = q.get("query", "") if isinstance(q, dict) else (q or "")
        m = re.match(r"\s*label_values\((.*),\s*\w+\s*\)\s*$", q)
        if m and ds_type(var, "prometheus") == "prometheus":
            check_prom(f"{rel} [variavel {var.get('name')}]", m.group(1))

    for p in iter_panels(d.get("panels")):
        panel_ds = ds_type(p, "prometheus")
        for t in p.get("targets") or []:
            expr = t.get("expr")
            if not expr:
                continue
            where = f"{rel} [{p.get('title') or p.get('id')}]"
            if ds_type(t, panel_ds) == "loki":
                check_loki(where, expr)
            else:
                check_prom(where, expr)

for w in warnings:
    print(f"AVISO  {w}")
for e in errors:
    print(f"ERRO   {e}")
print(f"{len(uids)} dashboards, {len(prom_jobs)} contratos Prometheus, "
      f"{len(loki_jobs)} contratos Loki, {len(errors)} erro(s)")
sys.exit(1 if errors else 0)
