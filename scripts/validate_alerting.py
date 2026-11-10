#!/usr/bin/env python3
"""Valida alerting/ contra os contratos de alerta (contracts/alerts) e de dados.

1. Estrutura: so README.md e YAML em rules/, notifications/ e inhibition/; sem CRLF; nomes achataveis
   sem colisao (alerting_build.py).
2. Regras: formato de provisionamento (apiVersion 1), UID valido e unico, titulo '[CODIGO] titulo' igual ao
   catalogo, labels do contrato (alert_code, severity, area, scope e as chaves exigidas pelo scope),
   $$labels nos labels (o provisionamento interpola variaveis de ambiente), summary/description,
   condition existente, datasource provisionado, metricas/labels das consultas nos contratos, painel
   vinculado existente nos dashboards.
3. Catalogo: todo codigo active tem regra e toda regra tem codigo; uid, severity e scope iguais.
4. Notificacoes: so mute timings e templates; policies e contact points sao da UI e nao podem aparecer
   em arquivo. Regras sem notification_settings (o destino sai da policy).
5. Inibicao: recurso InhibitionRule valido; labels do contrato; equal so com chaves dep_* e exigidas
   nao vazias (=~ .+) nos dois lados; alert_code dos matchers casa com algum codigo do catalogo.
"""
import json
import re
import sys

import yaml

import catalogo
import contracts as ct
from contracts import DATASOURCES, ROOT

ALERTING = ROOT / "alerting"
SECTIONS = ("rules", "notifications", "inhibition")
errors, warnings = [], []
contracts = ct.Contracts(errors)
_, LABELS = contracts.one("alert_label_contract", errors)
_, CATALOG = contracts.one("alert_code_catalog", errors)
_, NOTIF = contracts.one("alert_notification_contract", errors)

UID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
K8S_NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
TITLE_RE = re.compile(r"^\[([A-Z]+-\d+)\] (\S(?:.*\S)?)$")
ENV_VAR_RE = re.compile(r"(?<!\$)\$(?!\$)\{?[A-Za-z_]")  # $x interpolado pelo provisionamento ($$x nao)
DS_GROUP = {uid: group for group, uid in DATASOURCES.items()}
UNGUARDED_VALUE_RE = re.compile(r"\$values\.\w+\.Value")  # quebra o template quando a regra esta sem dados
BUILTIN_LABELS = {"alertname", "grafana_folder"}
MATCH_TYPES = {"=", "!=", "=~", "!~"}

label_spec = LABELS.get("labels") or {}
code_re = re.compile((label_spec.get("alert_code") or {}).get("format") or "^$")
severities = set((label_spec.get("severity") or {}).get("values") or {})
areas = (label_spec.get("area") or {}).get("values") or {}
scopes = (label_spec.get("scope") or {}).get("values") or {}
scope_requires = (label_spec.get("scope") or {}).get("requires") or {}
known_labels = set(label_spec)
dep_labels = {name for name in known_labels if name.startswith("dep_")}
codes = CATALOG.get("codes") or {}

for name in ("alert_code", "severity", "area", "scope"):
    if not label_spec.get(name):
        errors.append(f"contracts/alerts/labels.yml: label '{name}' nao definido")
prefixes = [(spec or {}).get("code_prefix") for spec in areas.values()]
for area, spec in areas.items():
    spec = spec or {}
    if not spec.get("code_prefix") or prefixes.count(spec.get("code_prefix")) > 1:
        errors.append(f"contracts/alerts/labels.yml: area {area}: code_prefix ausente ou repetido")
    for job in spec.get("prometheus_jobs") or []:
        if job not in contracts.prom_jobs:
            errors.append(f"contracts/alerts/labels.yml: area {area}: job Prometheus sem contrato: {job}")
    for job in spec.get("loki_jobs") or []:
        if job not in contracts.loki_jobs:
            errors.append(f"contracts/alerts/labels.yml: area {area}: job Loki sem contrato: {job}")
for scope, required in scope_requires.items():
    if scope not in scopes or not set(required) <= dep_labels:
        errors.append(f"contracts/alerts/labels.yml: scope.requires[{scope}] invalido")


CATALOGO, _cat_errors = catalogo.load()
errors.extend(_cat_errors)


def load_yaml(path):
    rel = path.relative_to(ROOT)
    raw = path.read_bytes()
    if b"\r" in raw:
        errors.append(f"{rel}: quebra de linha CRLF; usar LF")
    text = raw.decode("utf-8")
    if "@catalogo" in text:
        text, errs = catalogo.render(text, CATALOGO)
        errors.extend(f"{rel}: {e}" for e in errs)
    if not text.strip():
        errors.append(f"{rel}: arquivo vazio")
        return []
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError as e:
        errors.append(f"{rel}: YAML invalido: {e}")
        return []
    for d in docs:
        if not isinstance(d, dict):
            errors.append(f"{rel}: documento YAML deve ser um objeto")
    return [d for d in docs if isinstance(d, dict)]


# ------------------------------------------------------------- estrutura
files = {s: [] for s in SECTIONS}
flat_names = {}
for path in sorted(p for p in ALERTING.rglob("*") if p.is_file()):
    rel = path.relative_to(ROOT)
    parts = path.relative_to(ALERTING).parts
    if parts == ("README.md",):
        continue
    if len(parts) != 2 or parts[0] not in SECTIONS or path.suffix != ".yaml":
        errors.append(f"{rel}: fora da estrutura (alerting/README.md ou alerting/{{{','.join(SECTIONS)}}}/*.yaml)")
        continue
    files[parts[0]].append(path)
    if parts[0] != "inhibition":
        flat = f"{parts[0]}-{path.name}"
        if flat in flat_names:
            errors.append(f"{rel}: nome achatado {flat} repetido ({flat_names[flat]})")
        flat_names[flat] = rel


# --------------------------------------------------------------- dashboards
panels = {}  # uid do dashboard -> ids de painel
for path in (ROOT / "dashboards").rglob("*.json"):
    if path.name == "_folder.json":
        continue
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        panels[d["metadata"]["name"]] = {(e.get("spec") or {}).get("id") for e in d["spec"]["elements"].values()}
    except (ValueError, KeyError, TypeError, AttributeError):
        pass  # validate_dashboards.py aponta o erro


# ------------------------------------------------------------------ regras
def check_rule_labels(where, labels, code, log_only=False):
    for name, spec in label_spec.items():
        if (spec or {}).get("required") and not labels.get(name):
            errors.append(f"{where}: label obrigatorio ausente: {name}")
    for name in sorted(set(labels) - known_labels):
        errors.append(f"{where}: label fora do contrato (contracts/alerts/labels.yml): {name}")
    if code and not code_re.match(code):
        errors.append(f"{where}: alert_code fora do formato {code_re.pattern}: {code}")
    if labels.get("severity") and labels["severity"] not in severities:
        errors.append(f"{where}: severity invalida: {labels['severity']}")
    area = labels.get("area")
    if area and area not in areas:
        errors.append(f"{where}: area invalida: {area}")
    elif area and code and not code.startswith(f"{(areas[area] or {}).get('code_prefix')}-"):
        errors.append(f"{where}: alert_code {code} nao usa o prefixo da area {area} "
                      f"({(areas[area] or {}).get('code_prefix')})")
    scope = labels.get("scope")
    if scope and scope not in scopes:
        errors.append(f"{where}: scope invalido: {scope}")
    for name in scope_requires.get(scope) or []:
        if log_only and name == "dep_hypervisor":
            continue  # regra so de log: sem join com o inventario (labels.yml, dependency.log_rules)
        if not labels.get(name):
            errors.append(f"{where}: scope {scope} exige o label {name}")
    if labels.get("dep_vm") and not labels.get("dep_hypervisor") and not log_only:
        errors.append(f"{where}: dep_vm exige dep_hypervisor (com o host fora, so o alerta do host inibe)")
    if scope == "external" and set(labels) & dep_labels:
        errors.append(f"{where}: scope external nao tem chave de dependencia")
    for name, value in labels.items():
        if ENV_VAR_RE.search(str(value)):
            errors.append(f"{where}: label {name} usa $ simples (interpolado como variavel de ambiente); "
                          f"escrever $$labels")


def check_rule(rel, group, rule, seen_uids, seen_codes):
    uid = rule.get("uid")
    title = rule.get("title") or ""
    where = f"{rel} [{uid or title or '?'}]"
    for key in ("uid", "title", "condition", "data", "for", "noDataState", "execErrState", "labels", "annotations"):
        if key not in rule:
            errors.append(f"{where}: campo obrigatorio ausente: {key}")
    if uid is not None:
        if not UID_RE.match(str(uid)):
            errors.append(f"{where}: uid invalido (ate 40 caracteres [a-zA-Z0-9_-])")
        elif uid in seen_uids:
            errors.append(f"{where}: uid repetido ({seen_uids[uid]})")
        seen_uids[uid] = rel

    if rule.get("notification_settings"):
        errors.append(f"{where}: notification_settings envia direto a um contact point; o destino sai da policy (UI)")

    labels = rule.get("labels") or {}
    code = labels.get("alert_code")
    m = TITLE_RE.match(title)
    if not m:
        errors.append(f"{where}: titulo fora do formato '[CODIGO] titulo' (sem espacos/tabs nas pontas): {title!r}")
    elif m.group(1) != code:
        errors.append(f"{where}: codigo do titulo ({m.group(1)}) diferente do label alert_code ({code})")
    sources = {q.get("datasourceUid") for q in rule.get("data") or []} - {"__expr__"}
    check_rule_labels(where, labels, code, log_only=sources == {DATASOURCES["loki"]})

    if code:
        if code in seen_codes:
            errors.append(f"{where}: alert_code {code} repetido ({seen_codes[code]})")
        seen_codes[code] = where
        entry = codes.get(code)
        if not entry:
            errors.append(f"{where}: alert_code {code} fora do catalogo (contracts/alerts/codes.yml)")
        else:
            if entry.get("status") != "active":
                errors.append(f"{where}: {code} esta '{entry.get('status')}' no catalogo")
            for key in ("uid", "severity", "scope"):
                got = uid if key == "uid" else labels.get(key)
                if entry.get(key) != got:
                    errors.append(f"{where}: {key} {got!r} difere do catalogo ({entry.get(key)!r})")
            if m and entry.get("title") != m.group(2):
                errors.append(f"{where}: titulo {m.group(2)!r} difere do catalogo ({entry.get('title')!r})")
            runbook = (rule.get("annotations") or {}).get("runbook_url")
            if runbook != entry.get("runbook"):
                errors.append(f"{where}: annotation runbook_url {runbook!r} difere do catalogo ({entry.get('runbook')!r})")

    ann = rule.get("annotations") or {}
    for key in ("summary", "description"):
        if not ann.get(key):
            errors.append(f"{where}: annotation obrigatoria ausente: {key}")
    for key, value in ann.items():
        if UNGUARDED_VALUE_RE.search(str(value)):
            errors.append(f"{where}: annotation {key} usa $values.X.Value sem protecao; sem dados ela quebra. "
                          f"Escrever {{{{ with $values.A }}}}{{{{ humanize .Value }}}}{{{{ end }}}}")
    for key in sorted(set(ann) - {"summary", "description", "runbook_url", "__dashboardUid__", "__panelId__"}):
        errors.append(f"{where}: annotation nao padronizada: {key}")

    # o provisionamento do Grafana 13.3.0 ignora os campos dashboardUid/panelId; o vinculo vem das annotations
    for key in ("dashboardUid", "panelId"):
        if key in rule:
            errors.append(f"{where}: campo {key} e ignorado pelo provisionamento; usar a annotation __{key}__")
    dash, panel = ann.get("__dashboardUid__"), ann.get("__panelId__")
    if (dash is None) != (panel is None):
        errors.append(f"{where}: __dashboardUid__ e __panelId__ vao juntas")
    elif dash is not None:
        if dash not in panels:
            errors.append(f"{where}: __dashboardUid__ {dash} nao existe em dashboards/")
        elif not (isinstance(panel, str) and panel.isdigit()) or int(panel) not in panels[dash]:
            errors.append(f"{where}: __panelId__ {panel!r} nao e painel do dashboard {dash} (texto com o id)")

    refs = [q.get("refId") for q in rule.get("data") or []]
    for r in sorted({r for r in refs if refs.count(r) > 1}):
        errors.append(f"{where}: refId repetido: {r}")
    if rule.get("condition") not in refs:
        errors.append(f"{where}: condition {rule.get('condition')!r} nao e refId de nenhuma consulta")
    for q in rule.get("data") or []:
        ds = q.get("datasourceUid")
        model = q.get("model") or {}
        if ds == "__expr__":
            continue
        group = DS_GROUP.get(ds)
        if not group:
            errors.append(f"{where} [{q.get('refId')}]: datasourceUid {ds!r} nao provisionado "
                          f"({', '.join(sorted(DS_GROUP))})")
        elif group == "prometheus":
            ct.check_prom(f"{where} [{q.get('refId')}]", model.get("expr") or "", contracts, errors)
        elif group == "loki":
            ct.check_loki(f"{where} [{q.get('refId')}]", model.get("expr") or "", contracts, errors)


seen_uids, seen_codes, seen_groups = {}, {}, {}
rule_count = 0
for path in files["rules"]:
    rel = path.relative_to(ROOT)
    for doc in load_yaml(path):
        if doc.get("apiVersion") != 1:
            errors.append(f"{rel}: apiVersion deve ser 1")
        for key in sorted(set(doc) - {"apiVersion", "groups", "deleteRules"}):
            errors.append(f"{rel}: chave inesperada em arquivo de regras: {key}")
        for group in doc.get("groups") or []:
            for key in ("name", "folder", "interval", "rules"):
                if not group.get(key):
                    errors.append(f"{rel}: grupo {group.get('name')!r} sem {key}")
            gid = (group.get("orgId", 1), group.get("folder"), group.get("name"))
            if gid in seen_groups:
                errors.append(f"{rel}: grupo {gid[2]} da pasta {gid[1]} repetido ({seen_groups[gid]})")
            seen_groups[gid] = rel
            for rule in group.get("rules") or []:
                rule_count += 1
                check_rule(rel, group, rule, seen_uids, seen_codes)

# catalogo
for code, entry in codes.items():
    where = f"contracts/alerts/codes.yml [{code}]"
    if not code_re.match(code):
        errors.append(f"{where}: codigo fora do formato {code_re.pattern}")
    if (entry or {}).get("status") not in ("active", "retired"):
        errors.append(f"{where}: status deve ser active ou retired")
    elif entry["status"] == "active" and code not in seen_codes:
        errors.append(f"{where}: codigo active sem regra em alerting/rules")
    elif entry["status"] == "retired" and entry.get("uid") in seen_uids:
        errors.append(f"{where}: codigo retired com regra ativa ({entry['uid']})")
catalog_uids = [(e or {}).get("uid") for e in codes.values()]
for uid in sorted({u for u in catalog_uids if catalog_uids.count(u) > 1}):
    errors.append(f"contracts/alerts/codes.yml: uid {uid} usado por mais de um codigo")


# ----------------------------------------------------------- notificacoes
# Policies e contact points ficam na UI (contracts/alerts/notifications.yml): o arquivo substituiria a arvore
# inteira e travaria a edicao. Do repositorio saem so mute timings e templates.
UI_ONLY = {"policies", "resetPolicies", "contactPoints", "deleteContactPoints"}
defined = {"muteTimes": set(), "templates": set()}
for path in files["notifications"]:
    rel = path.relative_to(ROOT)
    for doc in load_yaml(path):
        if doc.get("apiVersion") != 1:
            errors.append(f"{rel}: apiVersion deve ser 1")
        for key in sorted(set(doc) & UI_ONLY):
            errors.append(f"{rel}: {key} e gerenciado na UI (contracts/alerts/notifications.yml)")
        allowed = {"apiVersion", "muteTimes", "deleteMuteTimes", "templates", "deleteTemplates"} | UI_ONLY
        for key in sorted(set(doc) - allowed):
            errors.append(f"{rel}: chave inesperada em arquivo de notificacao: {key}")
        for kind in defined:
            for item in doc.get(kind) or []:
                name = item.get("name")
                if not name:
                    errors.append(f"{rel}: {kind} sem name")
                elif name in defined[kind]:
                    errors.append(f"{rel}: {kind} {name} repetido")
                defined[kind].add(name)
for label in NOTIF.get("routing_labels") or []:
    if label not in known_labels:
        errors.append(f"contracts/alerts/notifications.yml: routing_labels com label fora do contrato: {label}")
for policy in NOTIF.get("recommended_ui_policies") or []:
    if policy.get("mute_timing") and policy["mute_timing"] not in defined["muteTimes"]:
        errors.append(f"contracts/alerts/notifications.yml: mute timing {policy['mute_timing']} nao declarado em alerting/")


# ---------------------------------------------------------------- inibicao
INHIBITION_API = "notifications.alerting.grafana.app/v1beta1"


def check_inhibition_matchers(where, side, matchers):
    if not matchers:
        errors.append(f"{where}: {side} vazio")
    result = {}
    for m in matchers or []:
        label, op, value = m.get("label"), m.get("type"), m.get("value")
        if set(m) != {"label", "type", "value"} or op not in MATCH_TYPES:
            errors.append(f"{where}: {side}: matcher invalido: {m!r}")
            continue
        if label not in known_labels | BUILTIN_LABELS:
            errors.append(f"{where}: {side}: label fora do contrato: {label}")
        if op in ("=~", "!~"):
            try:
                rx = re.compile(f"^(?:{value})$")
            except re.error as e:
                errors.append(f"{where}: {side}: regex invalida {value!r}: {e}")
                continue
            if label == "alert_code" and op == "=~" and not any(rx.match(c) for c in codes):
                errors.append(f"{where}: {side}: alert_code {value!r} nao casa com nenhum codigo do catalogo")
            if label == "scope" and op == "=~" and not any(rx.match(s) for s in scopes):
                errors.append(f"{where}: {side}: scope {value!r} nao casa com nenhum scope do contrato")
            if label == "scope" and op == "=~" and side == "target_matchers" and rx.match("external"):
                errors.append(f"{where}: scope external nunca e alvo de inibicao (nao depende da infraestrutura)")
        elif label == "scope" and op == "=" and side == "target_matchers" and value == "external":
            errors.append(f"{where}: scope external nunca e alvo de inibicao (nao depende da infraestrutura)")
        elif label == "alert_code" and op == "=" and value not in codes:
            errors.append(f"{where}: {side}: alert_code {value} fora do catalogo")
        result.setdefault(label, []).append((op, value))
    return result


inhibition_names = {}
for path in files["inhibition"]:
    rel = path.relative_to(ROOT)
    for doc in load_yaml(path):
        meta = doc.get("metadata") or {}
        name = meta.get("name")
        where = f"{rel} [{name or '?'}]"
        if doc.get("apiVersion") != INHIBITION_API or doc.get("kind") != "InhibitionRule":
            errors.append(f"{where}: esperado apiVersion {INHIBITION_API} e kind InhibitionRule")
        for key in sorted(set(doc) - {"apiVersion", "kind", "metadata", "spec"}):
            errors.append(f"{where}: campo fora do recurso (gerado pelo servidor?): {key}")
        for key in sorted(set(meta) - {"name"}):
            errors.append(f"{where}: metadata so deve ter 'name' (remover {key})")
        if not name or not K8S_NAME_RE.match(name):
            errors.append(f"{where}: metadata.name invalido (minusculas, numeros e '-')")
        elif name in inhibition_names:
            errors.append(f"{where}: nome repetido ({inhibition_names[name]})")
        inhibition_names[name] = rel
        spec = doc.get("spec") or {}
        for key in sorted(set(spec) - {"source_matchers", "target_matchers", "equal"}):
            errors.append(f"{where}: campo desconhecido em spec: {key}")
        src = check_inhibition_matchers(where, "source_matchers", spec.get("source_matchers"))
        tgt = check_inhibition_matchers(where, "target_matchers", spec.get("target_matchers"))
        if not spec.get("equal"):
            errors.append(f"{where}: sem equal (a inibicao valeria para todos os alvos, de qualquer host/VM)")
        for label in spec.get("equal") or []:
            if label not in dep_labels:
                errors.append(f"{where}: equal com label que nao e chave de dependencia: {label}")
            for side, matchers in (("source_matchers", src), ("target_matchers", tgt)):
                if ("=~", ".+") not in matchers.get(label, []):
                    errors.append(f"{where}: {side} deve exigir {label} =~ .+ (vazio e ausente sao iguais no equal)")

sys.exit(ct.report(errors, warnings,
                   f"{rule_count} regras, {len(seen_codes)} codigos, "
                   f"{len(defined['muteTimes'])} mute timings, {len(defined['templates'])} templates, "
                   f"{len(inhibition_names)} regras de inibicao"))
