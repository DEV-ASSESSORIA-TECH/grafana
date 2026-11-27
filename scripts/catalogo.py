#!/usr/bin/env python3
"""Catalogo de monitoramento (catalogo.yml): carrega, valida, expande nas regras e audita contra a producao.

As regras de alerta nao trazem listas fixas de hosts, VMs, servidores ou containers. Elas usam marcadores que o
build (alerting_build.py) e os validadores expandem a partir do catalogo.yml:

    @catalogo:<secao>@              todos os nomes da secao
    @catalogo:<secao>=<classes>@    nomes com uma das classes (separadas por virgula)
    @catalogo:<secao>!=<classes>@   nomes listados com outra classe
    @catalogo:backups@              backups esperados, como expressao PromQL (uma serie por backup)
    @catalogo:atendimento=<limite>@ limites da fila do TiFlux em segundos, como expressao PromQL (uma serie por
                                    department; ver a secao atendimento do catalogo.yml)

O resultado e uma alternancia de regex (a|b|c) com os nomes escapados; lista vazia vira "" (=~"" nao casa com
nada, !~"" nao exclui nada). A secao backups (instance: [service/backup_id, ...]) vira uma expressao com uma serie
(instance, service, backup_id) por item, ou uma expressao vazia sem itens. A secao atendimento vira uma expressao
com o limite de cada departamento (sobrescrita do catalogo, ou o padrao para os demais departamentos que o
tiflux-exporter publica); departamento com o limite 'ignorar' fica sem serie, e a comparacao nunca casa.

Uso:
    python scripts/catalogo.py                       # resumo do catalogo
    python scripts/catalogo.py --render alerting/rules/hypervisor.yaml
    GRAFANA_TOKEN=... python scripts/catalogo.py --audit --url https://mon.exemplo/grafana
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
FILE = ROOT / "catalogo.yml"
# secao -> (classes aceitas, classe de quem nao esta listado)
SECTIONS = {
    "hosts": (("critica", "importante", "informativa", "ignorar"), None),
    "vms": (("critica", "importante", "informativa", "ignorar"), None),
    "servidores": (("critico", "normal", "ignorar"), "normal"),
    "containers": (("importante", "secundario", "ignorar"), "importante"),
}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MARK = re.compile(r"@catalogo:([a-z]+)(?:(!?=)([a-z_,]+))?@")
# severidade que depende do catalogo: '{{ if match "^(...)$" $labels.name }}info{{ else }}warning{{ end }}'
SEVERITY = re.compile(r'^\{\{ if match "\^\((.*)\)\$" \$\$?labels\.name \}\}(\w+)\{\{ else \}\}(\w+)\{\{ end \}\}$')
# severidade que depende da criticidade publicada pelo backup (backup_info, contracts/prometheus/backup.yml):
# '{{ if eq $labels.criticality "baixa" }}warning{{ else }}critical{{ end }}'
SEVERITY_CRITICALITY = re.compile(
    r'^\{\{ if eq \$\$?labels\.criticality "baixa" \}\}(\w+)\{\{ else \}\}(\w+)\{\{ end \}\}$')
BACKUP_ID = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*/[a-z0-9]+([-_][a-z0-9]+)*$")
# fila de atendimento (TiFlux, TFX-401 a 405): limite -> minutos padrao quando o catalogo nao define
ATENDIMENTO_LIMITS = ("caixa_entrada", "triagem", "cliente_nao_lida", "cliente_lida", "sem_movimentacao")
DEPARTMENT = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
# departamentos que o tiflux-exporter publica (zero preenchido), base do limite padrao
ATENDIMENTO_DEPTS = 'group by (department) (tiflux_chats_open{job="integrations/tiflux"})'
# filtro de containers ignorados nos dashboards (Git Sync le o JSON direto, sem build). Forma exigida, logo apos o job:
#   Prometheus  {job="integrations/docker",name!~"|<ignorados>",...}
#   Loki        {job="docker",container!~"|<ignorados>",...}
DASH_IGNORE = re.compile(r'((?:integrations/docker\\",name|job=\\"docker\\",container)!~\\"\|)([^"\\]*(?:\\\\[^"\\]*)*)(\\")')


def severity_values(value):
    """(padrao, alternativa) de uma severidade com template; (valor, None) se fixa."""
    m = SEVERITY.match(value or "") or SEVERITY_CRITICALITY.match(value or "")
    return (m.group(m.lastindex), m.group(m.lastindex - 1)) if m else (value, None)


def severity_condition(value):
    """Quando vale a severidade alternativa (texto da documentacao); None se fixa."""
    if SEVERITY.match(value or ""):
        return "se o container for secundario no catalogo.yml"
    if SEVERITY_CRITICALITY.match(value or ""):
        return "se o backup publicar criticality=baixa"
    return None


def sync_dashboards(cat, write=False, root=ROOT):
    """Confere (ou grava) o filtro de containers ignorados nos dashboards. Devolve os arquivos fora do catalogo."""
    want = json.dumps(regex(names(cat, "containers", "=", "ignorar")))[1:-1]
    stale = []
    for path in sorted((root / "dashboards").rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        new = DASH_IGNORE.sub(lambda m: m.group(1) + want + m.group(3), text)
        if new != text:
            stale.append(path)
            if write:
                path.write_text(new, encoding="utf-8")
    return stale


class UniqueLoader(yaml.SafeLoader):
    """Chave repetida no YAML vira erro (o PyYAML ficaria so com a ultima, em silencio)."""


def _mapping(loader, node, deep=False):
    keys = set()
    for k, _ in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in keys:
            raise yaml.constructor.ConstructorError(None, None, f"nome repetido: {key}", k.start_mark)
        keys.add(key)
    return loader.construct_mapping(node, deep)


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load(path=FILE):
    """Devolve (catalogo, erros). catalogo = {secao: {nome: classe}}; hosts guarda a classe padrao das VMs."""
    errors = []
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader) or {}
    except (OSError, yaml.YAMLError) as e:
        return {s: {} for s in SECTIONS}, [f"{path.name}: {e}"]
    cat = {}
    for key in data:
        if key not in SECTIONS and key not in ("versao", "backups", "atendimento"):
            errors.append(f"{path.name}: secao desconhecida: {key}")
    for section, (classes, _) in SECTIONS.items():
        raw = data.get(section) or {}
        if not isinstance(raw, dict):
            errors.append(f"{path.name}: {section} deve ser um mapa nome: classe")
            raw = {}
        cat[section] = {}
        for name, cls in raw.items():
            if section == "hosts" and isinstance(cls, dict):
                cls = cls.get("padrao")
            name = str(name)
            if not NAME.match(name):
                errors.append(f"{path.name}: {section}: nome invalido: {name!r}")
            if cls not in classes:
                errors.append(f"{path.name}: {section}.{name}: classe {cls!r} (aceitas: {', '.join(classes)})")
            cat[section][name] = cls
    raw = data.get("backups") or {}
    cat["backups"] = {}
    if not isinstance(raw, dict):
        errors.append(f"{path.name}: backups deve ser um mapa instance: [service/backup_id, ...]")
        raw = {}
    for name, items in raw.items():
        name = str(name)
        if not NAME.match(name):
            errors.append(f"{path.name}: backups: nome invalido: {name!r}")
        if not isinstance(items, list) or not items:
            errors.append(f"{path.name}: backups.{name}: lista de service/backup_id")
            items = []
        for item in items:
            if not isinstance(item, str) or not BACKUP_ID.match(item):
                errors.append(f"{path.name}: backups.{name}: {item!r} fora do formato service/backup_id")
            elif item in cat["backups"].get(name, []):
                errors.append(f"{path.name}: backups.{name}: {item} repetido")
            else:
                cat["backups"].setdefault(name, []).append(item)
    cat["atendimento"] = load_atendimento(data.get("atendimento"), path.name, errors)
    return cat, errors


def _minutes(value, where, errors, allow_ignore):
    if allow_ignore and value == "ignorar":
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        errors.append(f"{where}: {value!r} (minutos inteiros > 0{' ou ignorar' if allow_ignore else ''})")
        return None
    return value


def load_atendimento(raw, fname, errors):
    """{'padrao': {limite: minutos}, 'departamentos': {department: {limite: minutos | None (ignorar)}}}."""
    out = {"padrao": {}, "departamentos": {}}
    if raw is None:
        errors.append(f"{fname}: secao atendimento ausente (limites da fila do TiFlux)")
        return out
    if not isinstance(raw, dict):
        errors.append(f"{fname}: atendimento deve ser um mapa com padrao e departamentos")
        return out
    for key in sorted(set(raw) - {"padrao", "departamentos"}):
        errors.append(f"{fname}: atendimento: chave desconhecida: {key}")
    padrao = raw.get("padrao") or {}
    if not isinstance(padrao, dict):
        errors.append(f"{fname}: atendimento.padrao deve ser um mapa limite: minutos")
        padrao = {}
    for limit in ATENDIMENTO_LIMITS:
        if limit not in padrao:
            errors.append(f"{fname}: atendimento.padrao.{limit} ausente")
        else:
            out["padrao"][limit] = _minutes(padrao[limit], f"{fname}: atendimento.padrao.{limit}", errors, False)
    for key in sorted(set(padrao) - set(ATENDIMENTO_LIMITS)):
        errors.append(f"{fname}: atendimento.padrao: limite desconhecido: {key} ({', '.join(ATENDIMENTO_LIMITS)})")
    depts = raw.get("departamentos") or {}
    if not isinstance(depts, dict):
        errors.append(f"{fname}: atendimento.departamentos deve ser um mapa department: limites")
        depts = {}
    for dept, limits in depts.items():
        dept = str(dept)
        where = f"{fname}: atendimento.departamentos.{dept}"
        if not DEPARTMENT.match(dept):
            errors.append(f"{where}: nome fora do label department (minusculas ASCII separadas por _)")
        if limits == "ignorar":
            limits = {limit: "ignorar" for limit in ATENDIMENTO_LIMITS}
        if not isinstance(limits, dict) or not limits:
            errors.append(f"{where}: mapa limite: minutos (ou ignorar para o departamento inteiro)")
            continue
        out["departamentos"][dept] = {}
        for limit, value in limits.items():
            if limit not in ATENDIMENTO_LIMITS:
                errors.append(f"{where}: limite desconhecido: {limit} ({', '.join(ATENDIMENTO_LIMITS)})")
                continue
            out["departamentos"][dept][limit] = _minutes(value, f"{where}.{limit}", errors, True)
    return out


def atendimento_vector(cat, limit):
    """Limite (segundos) por department como expressao PromQL, entre parenteses.

    Departamentos do catalogo com valor proprio viram uma serie cada; os demais publicados pelo exporter recebem o
    padrao. 'ignorar' tira o departamento das duas partes (sem serie, a comparacao 'X > on (department) limite'
    nunca casa)."""
    at = cat.get("atendimento") or {}
    default = (at.get("padrao") or {}).get(limit)
    own, excluded = [], []
    for dept, limits in sorted((at.get("departamentos") or {}).items()):
        if limit not in limits:
            continue
        excluded.append(dept)
        if limits[limit] is not None:
            own.append(f'label_replace(vector({limits[limit] * 60}), "department", "{dept}", "", "")')
    base = ATENDIMENTO_DEPTS
    if excluded:
        base = base.replace('job="integrations/tiflux"', f'job="integrations/tiflux",department!~"{regex(excluded)}"')
    parts = own + ([f"({base} * 0 + {default * 60})"] if default else [])
    return "(" + " or ".join(parts) + ")" if parts else "(vector(1) < 0)"


def names(cat, section, op=None, classes=None):
    items = cat[section]
    if op is None:
        return sorted(items)
    wanted = set(classes.split(","))
    return sorted(n for n, c in items.items() if (c in wanted) == (op == "="))


def regex(values):
    return "|".join(re.sub(r"([.\\+*?()\[\]{}^$|])", r"\\\1", v) for v in values)


def backups_vector(cat):
    """Backups esperados como expressao PromQL: uma serie (instance, service, backup_id) com valor 1 por item."""
    parts = []
    for instance, items in sorted(cat.get("backups", {}).items()):
        for item in items:
            service, backup_id = item.split("/")
            parts.append(f'label_replace(label_replace(label_replace(vector(1), "instance", "{instance}", "", ""), '
                         f'"service", "{service}", "", ""), "backup_id", "{backup_id}", "", "")')
    # entre parenteses: "unless"/"and" ligam mais forte que "or" e pegariam so o ultimo item
    return "(" + " or ".join(parts) + ")" if parts else "(vector(1) < 0)"


def render(text, cat):
    """Expande os marcadores; devolve (texto, erros)."""
    errors = []

    def sub(m):
        section, op, classes = m.groups()
        if section == "backups" and not op:
            return backups_vector(cat)
        if section == "atendimento":
            if op != "=" or classes not in ATENDIMENTO_LIMITS:
                errors.append(f"marcador de atendimento invalido: {m.group(0)} ({', '.join(ATENDIMENTO_LIMITS)})")
                return m.group(0)
            return atendimento_vector(cat, classes)
        if section not in SECTIONS:
            errors.append(f"marcador com secao desconhecida: {m.group(0)}")
            return m.group(0)
        if op and not set(classes.split(",")) <= set(SECTIONS[section][0]):
            errors.append(f"marcador com classe desconhecida: {m.group(0)}")
            return m.group(0)
        return regex(names(cat, section, op, classes))

    out = MARK.sub(sub, text)
    if "@catalogo" in out and not errors:
        errors.append("marcador @catalogo malformado")
    return out, errors


def summary(cat):
    for section, (_, default) in SECTIONS.items():
        print(f"{section}:")
        groups = {}
        for n, c in sorted(cat[section].items()):
            groups.setdefault(c, []).append(n)
        for c, ns in groups.items():
            print(f"  {c:12} {', '.join(ns)}")
        if default:
            print(f"  (nao listado = {default})")
    print("backups (esperados):")
    for n, items in sorted(cat.get("backups", {}).items()):
        print(f"  {n:28} {', '.join(items)}")
    at = cat.get("atendimento") or {}
    print("atendimento (limites em minutos):")
    print(f"  {'padrao':28} " + ", ".join(f"{k}={v}" for k, v in (at.get("padrao") or {}).items()))
    for dept, limits in sorted((at.get("departamentos") or {}).items()):
        print(f"  {dept:28} " + ", ".join(f"{k}={'ignorar' if v is None else v}" for k, v in limits.items()))


# --------------------------------------------------------------- auditoria
def prom(url, token, expr):
    q = urllib.parse.urlencode({"query": expr})
    req = urllib.request.Request(f"{url.rstrip('/')}/api/datasources/proxy/uid/prometheus/api/v1/query?{q}",
                                 headers={"Authorization": f"Bearer {token}", "User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return [r["metric"] for r in json.load(resp)["data"]["result"]]


def audit(cat, url, token):
    """Compara o catalogo com o que a producao reportou nos ultimos 7 dias. Devolve o numero de pendencias."""
    pend = 0
    hosts = {m["instance"] for m in prom(url, token, 'group by (instance) (max_over_time(up{job="integrations/hypervisor"}[7d]))')}
    vms = {}
    for m in prom(url, token, 'max by (instance, vm) (max_over_time(hypervisor_vm_info{job="integrations/hypervisor"}[7d]))'):
        vms[m["vm"]] = {"host": m["instance"], "on": False}
    for m in prom(url, token, 'max by (instance, vm) (hypervisor_vm_state{job="integrations/hypervisor",state="running"} == 1)'):
        if m["vm"] in vms:
            vms[m["vm"]]["on"] = True
    servers = {m["instance"]: m["job"].split("/")[-1] for m in prom(
        url, token, 'group by (instance, job) (max_over_time(up{job=~"integrations/(unix|windows)"}[7d]))')}
    containers = {}
    for m in prom(url, token, 'group by (instance, name) (max_over_time(container_last_seen{job="integrations/docker",name!=""}[7d]))'):
        containers.setdefault(m["name"], set()).add(m["instance"])
    running = {m["name"] for m in prom(url, token, 'group by (name) (container_last_seen{job="integrations/docker",name!=""})')}

    def head(title):
        print(f"\n{title}")

    head("Hosts Hyper-V (classe = padrao das VMs nao listadas)")
    for h in sorted(set(cat["hosts"]) | hosts):
        cls = cat["hosts"].get(h)
        note = "" if h in hosts else "  <- nao reporta ha 7 dias (remover?)"
        if cls is None:
            note, pend = "  <- FALTA no catalogo (VMs dele nao alertam)", pend + 1
        elif h not in hosts:
            pend += 1
        print(f"  {h:28} {cls or '-':12}{note}")

    head("VMs")
    for vm in sorted(set(cat["vms"]) | set(vms)):
        info = vms.get(vm)
        cls = cat["vms"].get(vm)
        eff = cls or (cat["hosts"].get(info["host"]) if info else None) or "-"
        where = f"{info['host']}, {'ligada' if info['on'] else 'desligada'}" if info else "nao existe ha 7 dias"
        note = "" if cls else "  (padrao do host)"
        if not info:
            note, pend = "  <- remover do catalogo?", pend + 1
        print(f"  {vm:28} {eff:12} {where}{note}")

    head("Servidores (nao listado = normal)")
    for s in sorted(set(cat["servidores"]) | set(servers)):
        cls = cat["servidores"].get(s, "normal")
        if s in cat["hosts"]:
            continue
        note = ""
        if s not in servers:
            note, pend = "  <- nao reporta ha 7 dias (remover?)", pend + 1
        print(f"  {s:28} {cls:12} {servers.get(s, '-')}{note}")

    head("Containers (visto nos ultimos 7 dias)")
    for c in sorted(set(cat["containers"]) | set(containers)):
        cls = cat["containers"].get(c, "importante")
        where = ", ".join(sorted(containers.get(c, []))) or "-"
        state = "rodando" if c in running else "parado/removido" if c in containers else "nao existe"
        note = ""
        if c not in containers:
            note, pend = "  <- remover do catalogo?", pend + 1
        print(f"  {c:34} {cls:12} {where:18} {state}{note}")
    head("Backups (esperados no catalogo x reportados nos ultimos 7 dias)")
    seen = {(m["instance"], f'{m["service"]}/{m["backup_id"]}') for m in prom(
        url, token, 'group by (instance, service, backup_id) (max_over_time(backup_collection_timestamp_seconds{job="integrations/backup"}[7d]))')}
    expected = {(n, i) for n, items in cat.get("backups", {}).items() for i in items}
    for n, i in sorted(expected | seen):
        note = ""
        if (n, i) not in seen:
            note, pend = "  <- esperado, nao reportado ha 7 dias", pend + 1
        elif (n, i) not in expected:
            note = "  (reportado, fora da lista de esperados)"
        print(f"  {n:28} {i:28}{note}")
    print(f"\n{pend} pendencia(s) no catalogo." if pend else "\nCatalogo em dia com a producao.")
    return pend


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--render", metavar="ARQUIVO", help="mostra o arquivo com os marcadores expandidos")
    ap.add_argument("--audit", action="store_true", help="compara com a producao (GRAFANA_TOKEN no ambiente)")
    ap.add_argument("--dashboards", action="store_true",
                    help="grava nos dashboards o filtro de containers ignorados (o CI confere)")
    ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", ""))
    args = ap.parse_args()
    cat, errors = load()
    for e in errors:
        print(f"ERRO {e}", file=sys.stderr)
    if errors:
        sys.exit(1)
    if args.render:
        text, errs = render(Path(args.render).read_text(encoding="utf-8"), cat)
        for e in errs:
            print(f"ERRO {e}", file=sys.stderr)
        print(text, end="")
        sys.exit(1 if errs else 0)
    if args.dashboards:
        for p in sync_dashboards(cat, write=True):
            print(f"atualizado {p.relative_to(ROOT)}")
        return
    if args.audit:
        token = os.environ.get("GRAFANA_TOKEN", "")
        if not args.url or not token:
            sys.exit("ERRO: --audit precisa de --url (ou GRAFANA_URL) e GRAFANA_TOKEN")
        audit(cat, args.url, token)
        return
    summary(cat)


if __name__ == "__main__":
    main()
