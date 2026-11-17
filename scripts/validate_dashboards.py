#!/usr/bin/env python3
"""Valida os dashboards (Dashboard Schema V2) contra os contratos.

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
6. Navegacao: tag do dominio, menu de paineis (dropdown por tag) e link para a pagina inicial do dominio.
   Infraestrutura: tag infra e Visao geral. PayRecon (dashboards/payrecon): tag payrecon e payrecon-visao-geral.
7. Dados de demonstracao (TestData csv_content): o cabecalho do CSV e identico as colunas de uma view de um
   contrato sql_dataset_contract (contracts/postgres/).

Os alertas sao validados por validate_alerting.py.
"""
import json
import re
import sys

import catalogo
import contracts as ct
from contracts import DATASOURCES, ROOT

DASHBOARDS = ROOT / "dashboards"
errors, warnings = [], []

DASHBOARD_API = "dashboard.grafana.app/v2"
FOLDER_API = "folder.grafana.app/v1"
FOLDER_FILE = "_folder.json"
# datasources embutidos do Grafana (anotacoes nativas)
BUILTIN_DATASOURCES = {("grafana", "-- Grafana --"), ("datasource", "grafana"),
                       ("datasource", "-- Dashboard --")}  # reaproveita o resultado de outro painel
UID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,40}$")
LEGACY_KEYS = ("__inputs", "__requires", "__elements")
NAV_TAG = "infra"      # dashboards com esta tag aparecem no menu Paineis
HOME_UID = "overview"  # Visao geral: pagina inicial
# dominios com navegacao propria (pasta em dashboards/ -> (tag do menu, UID da pagina inicial))
NAV_DOMAINS = {"payrecon": ("payrecon", "payrecon-visao-geral")}

contracts = ct.Contracts(errors)


def check_prom(where, expr):
    ct.check_prom(where, expr, contracts, errors)


def check_loki(where, expr):
    ct.check_loki(where, expr, contracts, errors)


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
    if group == "grafana-testdata-datasource":
        check_testdata(where, query.get("spec") or {})
        return
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


def check_navigation(rel, name, spec):
    """Navegacao comum: tag do dominio (menu de paineis) e link para a pagina inicial nos demais dashboards."""
    folder = rel.parts[1] if len(rel.parts) > 2 else ""
    tag, home = NAV_DOMAINS.get(folder, (NAV_TAG, HOME_UID))
    if tag not in (spec.get("tags") or []):
        errors.append(f"{rel}: falta a tag '{tag}' (menu de paineis do dominio)")
    if folder in NAV_DOMAINS and NAV_TAG in (spec.get("tags") or []):
        errors.append(f"{rel}: tag '{NAV_TAG}' em dashboard de outro dominio ({folder})")
    links = spec.get("links") or []
    if not any(link.get("type") == "dashboards" and link.get("asDropdown") and tag in (link.get("tags") or [])
               for link in links):
        errors.append(f"{rel}: falta o link do menu (type dashboards, tag {tag}, asDropdown)")
    if name != home and not any(link.get("url") == f"/grafana/d/{home}" for link in links):
        errors.append(f"{rel}: falta o link para a pagina inicial (/grafana/d/{home})")


def check_testdata(where, spec):
    """Demonstracao: CSV embutido com as colunas de uma view do contrato SQL."""
    if spec.get("scenarioId") != "csv_content":
        errors.append(f"{where}: TestData so com o cenario csv_content (dados de demonstracao)")
        return
    lines = (spec.get("csvContent") or "").strip().splitlines()
    header = lines[0].split(",") if lines else []
    if header not in contracts.sql_views.values():
        errors.append(f"{where}: cabecalho do CSV nao corresponde a nenhuma view de contrato SQL: {header}")


def check_dashboard(rel, res):
    name = check_metadata(rel, res, DASHBOARD_API, "Dashboard")
    spec = res.get("spec") or {}
    check_navigation(rel, name, spec)
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

_cat, _cat_errors = catalogo.load()
errors.extend(_cat_errors)
for path in catalogo.sync_dashboards(_cat):
    warnings.append(f"{path.relative_to(ROOT)}: filtro de containers ignorados difere do catalogo.yml "
                    "(o workflow catalogo regrava e faz o commit sozinho)")

sys.exit(ct.report(errors, warnings,
                   f"{len(uids)} dashboards, {len(folder_uids)} pastas, {len(contracts.prom_jobs)} contratos Prometheus, "
                   f"{len(contracts.loki_jobs)} contratos Loki, {len(contracts.sql_views)} views SQL"))
