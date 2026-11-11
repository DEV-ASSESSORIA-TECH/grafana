#!/usr/bin/env python3
"""Neutralidade dos contratos (contracts/).

Contrato descreve formato e regra de consumo (metricas, labels, chaves, receitas). Nao descreve o estado do
ambiente: quais hosts tem o que, o que falta implantar, contagens de uma exportacao. Isso envelhece e leva a
conclusoes erradas. Lista de hosts e classes fica em catalogo.yml; estado de implantacao, em README/commits.

Falha quando um contrato tem:
1. Nome real de servidor (srv-*). Exemplos usam srv-exemplo / SRV-EXEMPLO.
2. Linguagem de estado temporario (ainda, hoje, atualmente, de teste...).
3. Chaves de snapshot/inventario (snapshot, coverage, hosts_pending, containers_by_host...).
4. Data fora das chaves de data permitidas (observed_at_utc, observed_date, defined_date, validated_at).
5. status fora de observed_baseline | defined.
"""
import re
import sys

import yaml

from contracts import ROOT

HOST_RE = re.compile(r"(?i)\bsrv[-_](?!exemplo)[a-z0-9]")
TEMPORAL_RE = re.compile(
    r"(?i)\b(ainda|hoje|atualmente|por enquanto|no momento|por ora|de teste|em teste|"
    r"script antigo|versao antiga|pendente de|vira observed)\b")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
DATE_KEYS = {"observed_at_utc", "observed_date", "defined_date", "validated_at"}
FORBIDDEN_KEYS = {
    "snapshot", "coverage", "hosts_observed", "hosts_pending", "streams_per_host", "containers_by_host",
    "host_channel_pairs", "instances_present", "instances_missing", "baseline_instances",
    "alloy_changes_applied", "values_added_at", "private_inventory", "full_metric_catalog",
}
FORBIDDEN_TOP_KEYS = {"observed"}  # bloco de inventario dos contratos Loki
STATUS = {"observed_baseline", "defined"}

errors = []


def walk(node, rel, key=None, depth=0):
    if isinstance(node, yaml.MappingNode):
        for k, v in node.value:
            name = k.value
            line = k.start_mark.line + 1
            if name in FORBIDDEN_KEYS or (depth == 0 and name in FORBIDDEN_TOP_KEYS):
                errors.append(f"{rel}:{line}: chave de snapshot/inventario: {name}")
            if depth == 0 and name == "status" and getattr(v, "value", None) not in STATUS:
                errors.append(f"{rel}:{line}: status '{v.value}' fora de {sorted(STATUS)}")
            walk(v, rel, name, depth + 1)
    elif isinstance(node, yaml.SequenceNode):
        for v in node.value:
            walk(v, rel, key, depth + 1)
    elif isinstance(node, yaml.ScalarNode):
        if DATE_RE.match(str(node.value)) and key not in DATE_KEYS:
            errors.append(f"{rel}:{node.start_mark.line + 1}: data fora das chaves permitidas ({key})")


files = sorted(ROOT.glob("contracts/**/*.yml"))
for path in files:
    rel = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    for n, line in enumerate(text.splitlines(), 1):
        if m := HOST_RE.search(line):
            errors.append(f"{rel}:{n}: nome real de servidor ({m.group(0)}...); usar srv-exemplo")
        if m := TEMPORAL_RE.search(line):
            errors.append(f"{rel}:{n}: estado temporario ('{m.group(0)}'); contrato descreve regra, nao situacao")
    try:
        walk(yaml.compose(text), rel)
    except yaml.YAMLError as e:
        errors.append(f"{rel}: YAML invalido: {e}")

for e in errors:
    print(f"ERRO   {e}")
print(f"{len(files)} contratos, {len(errors)} erro(s) de neutralidade")
sys.exit(1 if errors else 0)
