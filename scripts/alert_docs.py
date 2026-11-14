#!/usr/bin/env python3
"""Documentacao operacional dos alertas (docs/alerts), conforme contracts/alerts/docs.yml.

Gera, a partir do repositorio, o cabecalho de cada documento (entre os marcadores) e o indice:
severidade, area, escopo, tempo ate disparar, avaliacao, quem inibe e quem e inibido, notificacao e links.
As secoes manuais (O que significa, Verificar, Resolver) nunca sao tocadas; documento novo nasce com
elas marcadas 'A preencher.'.

Uso:
    python scripts/alert_docs.py --write    # gera/atualiza cabecalhos e indice
    python scripts/alert_docs.py --check    # CI: falha se algo estiver faltando ou desatualizado
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import catalogo  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = "A preencher."
SCOPE_PT = {"hypervisor": "hosts", "vm": "VMs", "guest": "sistemas convidados", "service": "serviços/containers",
            "external": "serviços externos", "collection": "coleta"}
EQUAL_PT = {"dep_hypervisor": "do mesmo host", "dep_vm": "da mesma VM"}
AREA_PT = {"hypervisor": "Hipervisor", "windows": "Windows", "linux": "Linux", "containers": "Containers",
           "servicos": "Serviços", "monitoramento": "Monitoramento", "bancos": "Bancos de dados",
           "backups": "Backups"}


def sev_text(value):
    default, alt = catalogo.severity_values(value)
    return f"{default} ({alt} se o container for secundario no catalogo.yml)" if alt else default


def load(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


contract = load("contracts/alerts/docs.yml")
catalog = load("contracts/alerts/codes.yml")["codes"]
labels_contract = load("contracts/alerts/labels.yml")["labels"]
BEGIN, END = contract["generated"]["markers"]["begin"], contract["generated"]["markers"]["end"]
GRAFANA = contract["grafana_url"].rstrip("/")
SECTIONS = [s["title"] for s in contract["manual"]["sections"]]
scopes = list(labels_contract["scope"]["values"])

rules = {}  # uid -> (regra, grupo, arquivo)
for path in sorted((ROOT / "alerting" / "rules").glob("*.yaml")):
    for group in yaml.safe_load(catalogo.render(path.read_text(encoding="utf-8"), catalogo.load()[0])[0]).get("groups") or []:
        for rule in group["rules"]:
            rules[rule["uid"]] = (rule, group, path.name)
inhibitions = []
for path in sorted((ROOT / "alerting" / "inhibition").glob("*.yaml")):
    inhibitions += [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def matcher_ok(m, labels):
    value = labels.get(m["label"])
    if value is None:
        return m["type"] in ("!=", "!~")
    if m["label"].startswith("dep_"):
        value = "chave"  # template: assume valor preenchido
    full = re.fullmatch(m["value"], value) is not None if m["type"] in ("=~", "!~") else value == m["value"]
    return full if m["type"] in ("=", "=~") else not full


def matches_all(matchers, labels):
    return all(matcher_ok(m, labels) for m in matchers)


def scope_names(matchers):
    for m in matchers:
        if m["label"] == "scope" and m["type"] in ("=", "=~"):
            names = [SCOPE_PT[s] for s in scopes if re.fullmatch(m["value"], s)]
            return ", ".join(names[:-1]) + (" e " if len(names) > 1 else "") + names[-1]
    return "alertas"


def codes_matching(matchers):
    out = []
    for code in catalog:
        if catalog[code].get("status") == "active" and matches_all(
                [m for m in matchers if m["label"] == "alert_code"], {"alert_code": code}):
            out.append(code)
    return out


def duration(text):
    num, unit = int(text[:-1]), text[-1]
    if num == 0:
        return "imediato"
    return {"s": f"{num} s", "m": f"{num} min", "h": f"{num} h"}[unit]


def header(code):
    entry = catalog[code]
    rule, group, filename = rules[entry["uid"]]
    labels = rule["labels"]
    ann = rule.get("annotations") or {}
    source = "log (Loki)" if rule["data"][0]["datasourceUid"] == "loki" else "métrica (Prometheus)"
    lines = [BEGIN, f"# {code} · {entry['title']}", ""]
    if entry.get("status") == "retired":
        lines += ["> **Retirado.** Este código não está mais em uso e não é reutilizado.", ""]
    lines += ["| Severidade | Área | Escopo | Dispara após | Avaliação | Fonte |",
              "|---|---|---|---|---|---|",
              f"| {sev_text(labels['severity'])} | {labels['area']} | {labels['scope']} | {duration(rule['for'])} | "
              f"a cada {duration(group['interval'])} (`{group['name']}`) | {source} |", ""]
    inhibits, inhibited = [], []
    for inh in inhibitions:
        spec, name = inh["spec"], inh["metadata"]["name"]
        equal = " e ".join(EQUAL_PT.get(e, e) for e in spec.get("equal") or [])
        if matches_all(spec["source_matchers"], labels):
            inhibits.append(f"{scope_names(spec['target_matchers'])} {equal} (`{name}`)")
        if matches_all(spec["target_matchers"], labels):
            inhibited.append(f"{', '.join(codes_matching(spec['source_matchers']))} {equal} (`{name}`)")
    lines.append(f"- **Inibe:** {'; '.join(inhibits) if inhibits else 'nada'}.")
    lines.append(f"- **É inibido por:** {'; '.join(inhibited) if inhibited else 'nada'}.")
    lines.append(f"- **Notificação:** rota `severity = {catalogo.severity_values(labels['severity'])[0]}` da política padrão (UI); "
                 f"o agrupamento é o da política.")
    links = [f"[regra no Grafana]({GRAFANA}/alerting/grafana/{entry['uid']}/view)"]
    if ann.get("__dashboardUid__"):
        links.append(f"[painel]({GRAFANA}/d/{ann['__dashboardUid__']}?viewPanel=panel-{ann['__panelId__']})")
    links.append(f"[regra no repositório](../../alerting/rules/{filename})")
    lines += [f"- **Links:** {' · '.join(links)}", "", END]
    return "\n".join(lines)


def skeleton():
    return "\n".join(f"\n## {s}\n\n{PLACEHOLDER}" for s in SECTIONS) + "\n"


def index():
    lines = ["# Alertas", "",
             "Documentação operacional de cada alerta: o que significa, o que verificar e como resolver.",
             "Padrão em [`contracts/alerts/docs.yml`](../../contracts/alerts/docs.yml). Arquivo gerado por "
             "`scripts/alert_docs.py`; não edite à mão.", ""]
    by_area = {}
    for code, entry in catalog.items():
        rule = rules.get(entry["uid"], (None,))[0]
        area = rule["labels"]["area"] if rule else "retirados"
        by_area.setdefault(area, []).append((code, entry))
    for area in list(AREA_PT) + sorted(set(by_area) - set(AREA_PT)):
        if area not in by_area:
            continue
        lines += [f"## {AREA_PT.get(area, area.capitalize())}", "", "| Código | Alerta | Severidade |", "|---|---|---|"]
        for code, entry in sorted(by_area[area]):
            lines.append(f"| [{code}]({code}.md) | {entry['title']} | {entry['severity']} |")
        lines.append("")
    return "\n".join(lines)


def doc_path(code):
    return ROOT / contract["path"].format(code=code)


def split(text):
    if BEGIN not in text or END not in text:
        return None, text
    before, rest = text.split(BEGIN, 1)
    _, after = rest.split(END, 1)
    return before, after


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = ap.parse_args()
    errors = []
    expected = {}
    for code, entry in catalog.items():
        if entry["uid"] not in rules and entry.get("status") == "active":
            errors.append(f"{code}: regra {entry['uid']} nao encontrada em alerting/rules")
            continue
        path = doc_path(code)
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current is None:
            new = header(code) + "\n" + skeleton()
        else:
            before, after = split(current)
            if before is None:
                errors.append(f"{path.relative_to(ROOT)}: sem os marcadores do trecho gerado")
                continue
            new = before + header(code) + after
        expected[path] = new
        url = contract["runbook_url"].format(code=code)
        if entry.get("status") == "active" and entry.get("runbook") != url:
            errors.append(f"contracts/alerts/codes.yml [{code}]: runbook deve ser {url}")
        body = split(new)[1]
        positions = [body.find(f"\n## {s}\n") for s in SECTIONS]
        if -1 in positions or positions != sorted(positions):
            errors.append(f"{path.relative_to(ROOT)}: secoes manuais ausentes ou fora de ordem ({', '.join(SECTIONS)})")
        else:
            for i, s in enumerate(SECTIONS):
                end = positions[i + 1] if i + 1 < len(SECTIONS) else len(body)
                text = body[positions[i] + len(s) + 5:end].strip()
                if not text or text == PLACEHOLDER:
                    errors.append(f"{path.relative_to(ROOT)}: secao '{s}' vazia")
    expected[ROOT / contract["index"]] = index()
    known = {p.name for p in expected}
    for path in sorted((ROOT / "docs" / "alerts").glob("*.md")) if (ROOT / "docs" / "alerts").is_dir() else []:
        if path.name not in known:
            errors.append(f"{path.relative_to(ROOT)}: documento sem codigo no catalogo")

    stale = [p for p, text in expected.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.write:
        for p in stale:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(expected[p], encoding="utf-8")
            print(f"atualizado {p.relative_to(ROOT)}")
    else:
        errors += [f"{p.relative_to(ROOT)}: desatualizado; rode python scripts/alert_docs.py --write" for p in stale]
    for e in errors:
        print(f"ERRO   {e}")
    print(f"{len(expected) - 1} documentos, {len(errors)} erro(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
