"""Contratos de dados (contracts/) e verificacao de consultas contra eles.

Compartilhado por validate_dashboards.py e validate_alerting.py. As funcoes de verificacao acrescentam
mensagens na lista 'errors' recebida.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# UIDs reais dos datasources provisionados no Grafana (grupo da consulta -> UID)
DATASOURCES = {"prometheus": "prometheus", "loki": "loki"}


class Contracts:
    def __init__(self, errors):
        self.prom_jobs = {}  # job -> set(metricas)
        self.loki_jobs = {}  # job -> set(labels)
        self.by_kind = {}    # kind -> [(arquivo, contrato)] dos contratos que nao sao de job
        for path in sorted(ROOT.glob("contracts/**/*.yml")):
            rel = path.relative_to(ROOT)
            try:
                c = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as e:
                errors.append(f"{rel}: YAML invalido: {e}")
                continue
            job, kind = c.get("job"), c.get("kind")
            if kind == "observability_contract" and c.get("selector_without_job"):
                for group in (c.get("metric_groups") or {}).values():  # metricas sem label job (remote write)
                    self.prom_jobs.setdefault("", set()).update(group.get("metrics_observed") or [])
            elif kind == "observability_contract" and job:
                metrics = set()
                for group in (c.get("metric_groups") or {}).values():
                    metrics |= set(group.get("metrics_observed") or [])
                self.prom_jobs[job] = metrics
            elif kind == "loki_log_contract" and job:
                self.loki_jobs[job] = set((c.get("identity") or {}).get("observed_labels") or [])
            elif kind:
                self.by_kind.setdefault(kind, []).append((rel, c))

    def one(self, kind, errors):
        """Contrato unico de um kind (ex.: alert_label_contract)."""
        found = self.by_kind.get(kind) or []
        if len(found) != 1:
            errors.append(f"contracts/: esperado 1 contrato '{kind}', encontrados {len(found)}")
            return None, {}
        return found[0]


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


def check_prom(where, expr, contracts, errors):
    jobs = jobs_in(expr)
    metrics = prom_metrics(expr)
    if not metrics:
        return
    if not jobs:
        if metrics <= contracts.prom_jobs.get("", set()):
            return  # so metricas de contrato sem job (ex. GRAFANA_ALERTS)
        errors.append(f"{where}: consulta sem job fixo: {expr[:90]}")
        return
    unknown = jobs - contracts.prom_jobs.keys()
    if unknown:
        errors.append(f"{where}: job sem contrato: {', '.join(sorted(unknown))}")
        return
    allowed = set().union(*(contracts.prom_jobs[j] for j in jobs))
    for m in sorted(metrics - allowed):
        errors.append(f"{where}: metrica fora do contrato ({', '.join(sorted(jobs))}): {m}")


def check_loki(where, expr, contracts, errors):
    expr = re.sub(r"`[^`]*`", "``", expr)  # templates (label_format/line_format) nao sao seletores
    for selector in re.findall(r"\{([^}]*)\}", expr):
        labels = dict(re.findall(r'(\w+)\s*(?:=~|!=|!~|=)\s*"([^"]*)"', selector))
        job = labels.get("job")
        if not job:
            errors.append(f"{where}: seletor Loki sem job: {{{selector}}}")
            continue
        if job not in contracts.loki_jobs:
            errors.append(f"{where}: job Loki sem contrato: {job}")
            continue
        for label in sorted(set(labels) - contracts.loki_jobs[job]):
            errors.append(f"{where}: label Loki nao observado em {job}: {label}")


def report(errors, warnings, summary):
    """Imprime avisos/erros e o resumo; devolve o codigo de saida."""
    for w in warnings:
        print(f"AVISO  {w}")
    for e in errors:
        print(f"ERRO   {e}")
    print(f"{summary}, {len(errors)} erro(s)")
    return 1 if errors else 0
