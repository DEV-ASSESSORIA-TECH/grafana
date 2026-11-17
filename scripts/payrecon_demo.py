#!/usr/bin/env python3
"""Gera os dashboards de demonstracao do PayRecon (dados 100% simulados).

Um unico conjunto de dados, deterministico (semente fixa), alimenta os tres dashboards:

- payrecon-visao-geral   (dashboards/payrecon/visao-geral.json)   <- vw_movimento_diario
- payrecon-agenda        (dashboards/payrecon/agenda.json)        <- vw_agenda_recebimentos
- payrecon-conciliacao   (dashboards/payrecon/conciliacao.json)   <- vw_conciliacao

Os dados vao embutidos no JSON (TestData, cenario csv_content) com as mesmas colunas das views do contrato
contracts/postgres/payrecon.yml. Cada dashboard tem uma unica consulta TestData (no primeiro bloco do Resumo);
os demais paineis reaproveitam o resultado pelo datasource "-- Dashboard --". Na integracao real, troca-se
essa consulta por um SELECT na view correspondente e o resto do dashboard continua igual.

Uso:
    python scripts/payrecon_demo.py           # regrava os tres JSON
    python scripts/payrecon_demo.py --check   # falha se os JSON diferem do gerador (CI)
"""
import argparse
import datetime as dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dashboards" / "payrecon"

# ------------------------------------------------------------------ cenario
SEED = 20261117
DATA_BASE = dt.date(2026, 11, 17)            # posicao da demonstracao ("hoje" da simulacao), 08h00
HIST_INI = dt.date(2026, 9, 18)              # historico gerado (credito D+30 do periodo exibido)
PER_INI, PER_FIM = dt.date(2026, 10, 18), dt.date(2026, 11, 16)   # 30 dias de operacao exibidos
FERIADOS = {dt.date(2026, 10, 12), dt.date(2026, 11, 2), dt.date(2026, 11, 15), dt.date(2026, 11, 20),
            dt.date(2026, 12, 25), dt.date(2027, 1, 1)}
TZ = "-03:00"

MODALIDADES = ("pix", "debito", "credito")
BANDEIRAS = {
    "pix": {"sem_bandeira": 1.0},
    "debito": {"visa": 0.40, "mastercard": 0.38, "elo": 0.22},
    "credito": {"visa": 0.37, "mastercard": 0.36, "elo": 0.15, "hipercard": 0.07, "amex": 0.05},
}
TAXAS = {  # MDR negociado (ficticio)
    ("pix", "sem_bandeira"): 0.0075,
    ("debito", "visa"): 0.0109, ("debito", "mastercard"): 0.0109, ("debito", "elo"): 0.0139,
    ("credito", "visa"): 0.0219, ("credito", "mastercard"): 0.0219, ("credito", "elo"): 0.0259,
    ("credito", "hipercard"): 0.0265, ("credito", "amex"): 0.0299,
}
TICKET = {"pix": 118.0, "debito": 136.0, "credito": 168.0}
DIA_SEMANA = [0.93, 0.90, 0.95, 1.00, 1.17, 1.22, 0.86]   # seg..dom

# Ocorrencias da conciliacao (data prevista, modalidade, bandeira) -> regra
AJUSTES = {   # ajustes informados pela Cielo (EDI): explicam a diferenca, nao sao divergencia
    (dt.date(2026, 10, 28), "debito", "elo"): (-186.50, "Cancelamento de venda de 26/10 informado pela Cielo"),
    (dt.date(2026, 11, 3), "credito", "visa"): (-238.00, "Aluguel de 2 maquininhas (out/2026) descontado na agenda"),
    (dt.date(2026, 11, 10), "credito", "mastercard"): (-312.40, "Chargeback de venda contestada pelo portador"),
}
DIFERENCAS = {  # diferenca sem ajuste correspondente: em analise (nao e, por si so, divida da Cielo)
    (dt.date(2026, 10, 29), "debito", "visa"): (45.90, "Recebido a maior, sem ajuste no EDI: em análise"),
    (dt.date(2026, 11, 6), "credito", "elo"): (-87.35, "Recebido a menor, sem ajuste no EDI: em análise com a Cielo"),
}
NAO_LOCALIZADO = {
    (dt.date(2026, 11, 13), "credito", "amex"):
        "Crédito não identificado no extrato Sicredi: conferir lançamento agrupado ou outra conta",
}

# ------------------------------------------------------------------ visual
COR = {"verde": "#37872D", "laranja": "#E0752D", "vermelho": "#C4162A", "cinza": "#2F333A",
       "azul": "#1F60C4", "texto2": "#8E8E9E"}
COR_MOD = {"pix": "#1F9E89", "debito": "#3274D9", "credito": "#8F3BB8"}
NOME_MOD = {"pix": "Pix", "debito": "Débito", "credito": "Crédito"}
PRAZO_MOD = {"pix": "D+0", "debito": "D+1 útil", "credito": "D+30"}
COR_BAND = {"visa": "#5794F2", "mastercard": "#FF9830", "elo": "#FADE2A", "hipercard": "#F2495C",
            "amex": "#96D98D", "sem_bandeira": "#1F9E89"}
NOME_BAND = {"visa": "Visa", "mastercard": "Mastercard", "elo": "Elo", "hipercard": "Hipercard",
             "amex": "American Express", "sem_bandeira": "Sem bandeira (Pix)"}
SIT_AGENDA = {"liquidado": ("Liquidado", COR["verde"]), "aguardando": ("Aguardando crédito", COR["azul"]),
              "previsto": ("Previsto", "#8E8E9E"), "nao_localizado": ("Não localizado", COR["vermelho"])}
SIT_CONC = {"conciliado": ("Conciliado", COR["verde"]),
            "conciliado_ajuste": ("Conciliado com ajuste", "#56A64B"),
            "divergente": ("Em análise", COR["laranja"]),
            "nao_localizado": ("Não localizado", COR["vermelho"]),
            "aguardando": ("Aguardando crédito", COR["azul"]),
            "a_receber": ("A receber (não vencido)", "#8E8E9E")}
BRL = "currency:financial:R$ "        # Intl.NumberFormat do navegador: R$ 12.345,67 em pt-BR, sem K/M
PCT = "currency:financial:%:suffix"   # 1,63% em pt-BR
INTEIRO = "locale"                    # 12.345 em pt-BR
DATA = "time:DD/MM/YYYY"
DS_TESTDATA = {"group": "grafana-testdata-datasource", "name": "testdata"}
DS_DASHBOARD = {"group": "datasource", "name": "-- Dashboard --"}
SOURCE_PANEL = 2
FOLDER_NAV = "payrecon"
HOME = "payrecon-visao-geral"


def iso(d):
    return f"{d.isoformat()}T00:00:00{TZ}"


def util(d):
    return d.weekday() < 5 and d not in FERIADOS


def proximo_util(d):
    while not util(d):
        d += dt.timedelta(days=1)
    return d


def data_prevista(venda, mod):
    if mod == "pix":
        return venda                                   # D+0, inclusive fim de semana
    if mod == "debito":
        return proximo_util(venda + dt.timedelta(days=1))
    return proximo_util(venda + dt.timedelta(days=30))  # credito a vista: D+30 corridos, proximo dia util


def gerar():
    rnd = random.Random(SEED)
    vendas = []   # (data, mod, band, qtd, bruto, taxa, liquido)
    d = HIST_INI
    while d <= PER_FIM:
        fator = DIA_SEMANA[d.weekday()] * rnd.uniform(0.93, 1.07)
        if d.day in (5, 6, 20, 21):
            fator *= 1.08                              # dias de pagamento
        if d in FERIADOS or (d + dt.timedelta(days=1)) in FERIADOS:
            fator *= 1.10                              # vespera/feriado: viagens
        total = 54000 * fator
        mix = {"pix": rnd.uniform(0.27, 0.32), "debito": rnd.uniform(0.29, 0.33)}
        mix["credito"] = 1 - mix["pix"] - mix["debito"]
        for mod in MODALIDADES:
            for band, peso in BANDEIRAS[mod].items():
                bruto = round(total * mix[mod] * peso * rnd.uniform(0.9, 1.1), 2)
                qtd = max(1, round(bruto / (TICKET[mod] * rnd.uniform(0.92, 1.08))))
                taxa = round(bruto * TAXAS[(mod, band)], 2)
                vendas.append((d, mod, band, qtd, bruto, taxa, round(bruto - taxa, 2)))
        d += dt.timedelta(days=1)

    mov = [v for v in vendas]
    agenda, chaves = [], {}
    for (dv, mod, band, qtd, bruto, taxa, liq) in vendas:
        dp = data_prevista(dv, mod)
        if dp < PER_INI:
            continue                                   # liquidado antes do periodo exibido
        k = (dp, mod, band)
        chaves.setdefault(k, []).append(liq)
        if k in NAO_LOCALIZADO:
            sit = "nao_localizado"
        elif dp < DATA_BASE:
            sit = "liquidado"
        elif dp == DATA_BASE:
            sit = "aguardando"
        else:
            sit = "previsto"
        agenda.append((dp, dv, mod, band, bruto, taxa, liq, (dp - dv).days, (dp - DATA_BASE).days, sit))

    conc = []
    for k in sorted(chaves, key=lambda k: (k[0], MODALIDADES.index(k[1]), k[2])):
        dp, mod, band = k
        previsto = round(sum(chaves[k]), 2)
        ajuste, ocorr = AJUSTES.get(k, (0.0, ""))
        esperado = round(previsto + ajuste, 2)
        if dp > DATA_BASE:
            sit, recebido, dif = "a_receber", 0.0, 0.0
        elif dp == DATA_BASE:
            sit, recebido, dif = "aguardando", 0.0, 0.0
            ocorr = "Previsto para a data-base; o banco pode creditar ao longo do dia"
        elif k in NAO_LOCALIZADO:
            sit, recebido = "nao_localizado", 0.0
            dif, ocorr = -esperado, NAO_LOCALIZADO[k]
        elif k in DIFERENCAS:
            dif, ocorr = DIFERENCAS[k]
            sit, recebido = "divergente", round(esperado + dif, 2)
        else:
            recebido, dif = esperado, 0.0
            sit = "conciliado_ajuste" if ajuste else "conciliado"
        conc.append((dp, mod, band, previsto, ajuste, esperado, recebido, dif, sit,
                     int(sit in ("conciliado", "conciliado_ajuste")),
                     int(sit in ("aguardando", "nao_localizado")), int(sit == "divergente"),
                     int(sit not in ("a_receber", "aguardando")), ocorr))
    return mov, agenda, conc


def csv(header, rows):
    def cell(v):
        if isinstance(v, dt.date):
            return iso(v)
        if isinstance(v, float):
            return f"{v:.2f}"
        v = str(v)
        return f'"{v}"' if ("," in v or '"' in v) else v
    return ",".join(header) + "\n" + "\n".join(",".join(cell(c) for c in r) for r in rows) + "\n"


MOV_COLS = ["data_venda", "modalidade", "bandeira", "qtd_transacoes", "valor_bruto", "valor_taxa", "valor_liquido"]
AG_COLS = ["data_prevista", "data_venda", "modalidade", "bandeira", "valor_bruto", "valor_taxa", "valor_liquido",
           "prazo_dias", "dias_para_liquidacao", "situacao"]
CONC_COLS = ["data_prevista", "modalidade", "bandeira", "valor_previsto", "valor_ajustes", "valor_esperado",
             "valor_recebido", "diferenca", "situacao", "conciliado", "pendente", "divergente", "avaliado",
             "ocorrencia"]


# ------------------------------------------------------------------ blocos V2
def tr(group, options):
    return {"kind": "Transformation", "group": group, "spec": {"options": options}}


def t_time(*fields):
    return tr("convertFieldType", {"conversions": [{"targetField": f, "destinationType": "time"} for f in fields],
                                   "fields": {}})


def t_filter(time_field=None, mod=True, band=True, sit=False, extra=()):
    filters = []
    if time_field:
        filters += [{"fieldName": time_field, "config": {"id": "greaterOrEqual", "options": {"value": "${__from}"}}},
                    {"fieldName": time_field, "config": {"id": "lowerOrEqual", "options": {"value": "${__to}"}}}]
    if mod:
        filters.append({"fieldName": "modalidade", "config": {"id": "regex", "options": {"value": "^${modalidade:regex}$"}}})
    if band:
        filters.append({"fieldName": "bandeira", "config": {"id": "regex", "options": {"value": "^${bandeira:regex}$"}}})
    if sit:
        filters.append({"fieldName": "situacao", "config": {"id": "regex", "options": {"value": "^${situacao:regex}$"}}})
    filters += list(extra)
    return tr("filterByValue", {"type": "include", "match": "all", "filters": filters})


def f_eq(field, value):
    return {"fieldName": field, "config": {"id": "equal", "options": {"value": value}}}


def f_regex(field, value):
    return {"fieldName": field, "config": {"id": "regex", "options": {"value": value}}}


def f_between(field, lo, hi):
    return {"fieldName": field, "config": {"id": "between", "options": {"from": lo, "to": hi}}}


def f_ge(field, value):
    return {"fieldName": field, "config": {"id": "greaterOrEqual", "options": {"value": value}}}


def t_reduce(reducer="sum"):
    return tr("reduce", {"mode": "reduceFields", "reducers": [reducer], "includeTimeField": False})


def t_groupby(keys, aggs):
    fields = {k: {"operation": "groupby", "aggregations": []} for k in keys}
    for f, a in aggs.items():
        fields[f] = {"operation": "aggregate", "aggregations": [a]}
    return tr("groupBy", {"fields": fields})


def t_binary(left, op, right, alias, replace=False):
    return tr("calculateField", {"mode": "binary", "binary": {"left": {"matcher": {"id": "byName", "options": left}},
                                                              "operator": op,
                                                              "right": {"fixed": right} if isinstance(right, (int, float)) and not isinstance(right, bool)
                                                              else {"matcher": {"id": "byName", "options": right}}},
                                 "alias": alias, "replaceFields": replace})


def t_organize(exclude=(), order=(), rename=None):
    return tr("organize", {"excludeByName": {f: True for f in exclude},
                           "indexByName": {f: i for i, f in enumerate(order)},
                           "renameByName": rename or {}, "includeByName": {}})


def t_sort(field, desc=False):
    return tr("sortBy", {"fields": {}, "sort": [{"field": field, "desc": desc}]})


def t_rows_to_fields(name_field, value_field):
    return tr("rowsToFields", {"mappings": [{"fieldName": name_field, "handlerKey": "field.name"},
                                            {"fieldName": value_field, "handlerKey": "field.value"}]})


def t_share(codes, field):
    """Linha unica (rowsToFields) -> participacao de cada codigo em % do total (pct_<codigo>)."""
    out = [t_rows_to_fields(codes[0], field),
           tr("calculateField", {"mode": "reduceRow", "reduce": {"reducer": "sum", "include": list(codes[1])},
                                 "alias": "total",
                                 "replaceFields": False})]
    out.append(t_binary("total", "/", 100, "total_100"))
    for c in codes[1]:
        out.append(t_binary(c, "/", "total_100", f"pct_{c}"))
    return out + [t_organize(exclude=[*codes[1], "total", "total_100"])]


def per_code(names, colors, prefix=""):
    return [ov(prefix + k, ("displayName", names[k]), ("color", color(colors[k]))) for k in names]


def mapping(names, colors=None):
    return [{"type": "value", "options": {k: ({"text": v, "color": colors[k], "index": i} if colors else
                                               {"text": v, "index": i})
                                          for i, (k, v) in enumerate(names.items())}}]


MAP_MOD = mapping(NOME_MOD, COR_MOD)
MAP_BAND = mapping(NOME_BAND, COR_BAND)
MAP_SIT_AG = mapping({k: v[0] for k, v in SIT_AGENDA.items()}, {k: v[1] for k, v in SIT_AGENDA.items()})
MAP_SIT_CONC = mapping({k: v[0] for k, v in SIT_CONC.items()}, {k: v[1] for k, v in SIT_CONC.items()})


def ov(name, *props, regex=False):
    return {"matcher": {"id": "byRegexp" if regex else "byName", "options": name},
            "properties": [{"id": k, "value": v} for k, v in props]}


def color(c):
    return {"mode": "fixed", "fixedColor": c}


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in pairs]}


class Dash:
    def __init__(self, uid, csv_header, csv_rows):
        self.uid, self.elements, self.rows = uid, {}, []
        self.csv = csv(csv_header, csv_rows)
        self.next_id = 1

    def query(self, pid):
        if pid == SOURCE_PANEL:
            q = {"kind": "DataQuery", "group": DS_TESTDATA["group"], "version": "v0",
                 "datasource": {"name": DS_TESTDATA["name"]},
                 "spec": {"scenarioId": "csv_content", "csvContent": self.csv}}
        else:
            q = {"kind": "DataQuery", "group": DS_DASHBOARD["group"], "version": "v0",
                 "datasource": {"name": DS_DASHBOARD["name"]},
                 "spec": {"panelId": SOURCE_PANEL, "withTransforms": False}}
        return [{"kind": "PanelQuery", "spec": {"query": q, "refId": "A", "hidden": False}}]

    def panel(self, viz, title, desc, transformations, options, defaults, overrides=(), w=24, h=8, queries=True,
              links=()):
        pid = self.next_id
        self.next_id += 1
        self.elements[f"panel-{pid}"] = {"kind": "Panel", "spec": {
            "id": pid, "title": title, "description": desc, "links": list(links),
            "data": {"kind": "QueryGroup", "spec": {"queries": self.query(pid) if queries else [],
                                                    "transformations": list(transformations),
                                                    "queryOptions": {}}},
            "vizConfig": {"kind": "VizConfig", "group": viz, "version": "",
                          "spec": {"options": options, "fieldConfig": {"defaults": defaults,
                                                                       "overrides": list(overrides)}}}}}
        self.rows[-1][2].append((f"panel-{pid}", w, h))
        return pid

    def row(self, title, collapse=False):
        self.rows.append((title, collapse, []))

    def layout(self):
        rows = []
        for title, collapse, items in self.rows:
            grid, x, y, line_h = [], 0, 0, 0
            for name, w, h in items:
                if x + w > 24:
                    x, y, line_h = 0, y + line_h, 0
                grid.append({"kind": "GridLayoutItem", "spec": {"x": x, "y": y, "width": w, "height": h,
                                                                "element": {"kind": "ElementReference", "name": name}}})
                x += w
                line_h = max(line_h, h)
            row = {"kind": "RowsLayoutRow", "spec": {"title": title, "collapse": collapse,
                                                     "layout": {"kind": "GridLayout", "spec": {"items": grid}}}}
            if not title:
                row["spec"]["hideHeader"] = True
            rows.append(row)
        return {"kind": "RowsLayout", "spec": {"rows": rows}}


# ------------------------------------------------------------------ paineis comuns
def banner(d, subtitulo):
    html = (
        '<div style="display:flex;align-items:center;gap:16px;height:100%;padding:0 16px;'
        'background:#E0752D;color:#fff;border-radius:4px">'
        '<div style="font-size:18px;font-weight:700;letter-spacing:.5px;white-space:nowrap;flex-shrink:0">'
        'DEMONSTRAÇÃO — DADOS SIMULADOS</div>'
        f'<div style="font-size:13px;line-height:1.35">{subtitulo}</div></div>')
    d.row("")
    d.panel("text", "", "Dashboard de demonstração comercial do PayRecon. Valores, datas, bandeiras e ocorrências são "
            "fictícios e foram gerados por scripts/payrecon_demo.py; nenhuma integração real com Cielo ou Sicredi.",
            [], {"mode": "html", "content": html, "code": {"language": "plaintext", "showLineNumbers": False,
                                                          "showMiniMap": False}},
            {}, w=24, h=2, queries=False)


def t_keep(pattern):
    return tr("filterFieldsByName", {"include": {"pattern": pattern}})


def stat(d, name, desc, transformations, field, unit, color_steps, decimals=2, w=4, h=4, links=()):
    return d.panel("stat", "", desc, list(transformations) + [t_keep(f"^{field}$")], {
        "colorMode": "background_solid", "graphMode": "none", "justifyMode": "center", "orientation": "auto",
        "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
        "showPercentChange": False, "text": {"titleSize": 13, "valueSize": 26}, "textMode": "value_and_name",
        "wideLayout": False}, {
        "color": {"mode": "thresholds"}, "thresholds": color_steps, "decimals": decimals, "unit": unit,
        "noValue": "—"}, [ov(field, ("displayName", name))], w=w, h=h, links=links)


def var_custom(name, label, options, desc):
    return {"kind": "CustomVariable", "spec": {
        "name": name, "query": ", ".join(f"{v} : {k}" for k, v in options.items()),
        "current": {"text": ["All"], "value": ["$__all"]}, "options": [], "multi": True, "includeAll": True,
        "allValue": ".*", "label": label, "description": desc, "hide": "dontHide", "skipUrlSync": False,
        "allowCustomValue": False}}


VAR_MOD = var_custom("modalidade", "Modalidade", NOME_MOD, "Pix, débito ou crédito")
VAR_BAND = var_custom("bandeira", "Bandeira", NOME_BAND, "Bandeira do cartão (Pix não tem bandeira)")


def links(uid):
    return [
        {"title": "Visão Geral", "type": "link", "url": f"/grafana/d/{HOME}", "icon": "dashboard",
         "tooltip": "PayRecon: vendas por modalidade e bandeira", "tags": [], "asDropdown": False,
         "targetBlank": False, "includeVars": True, "keepTime": False},
        {"title": "PayRecon", "type": "dashboards", "icon": "external link", "tooltip": "", "tags": [FOLDER_NAV],
         "asDropdown": True, "targetBlank": False, "includeVars": True, "keepTime": False},
    ]


def resource(d, title, desc, time_from, time_to, variables, extra_tags):
    return {"apiVersion": "dashboard.grafana.app/v2", "kind": "Dashboard", "metadata": {"name": d.uid}, "spec": {
        "annotations": [{"kind": "AnnotationQuery", "spec": {
            "query": {"kind": "DataQuery", "group": "grafana", "version": "v0",
                      "datasource": {"name": "-- Grafana --"}, "spec": {}},
            "enable": True, "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts",
            "builtIn": True, "legacyOptions": {"type": "dashboard"}}}],
        "cursorSync": "Crosshair", "description": desc, "editable": True, "elements": d.elements,
        "layout": d.layout(), "links": links(d.uid), "liveNow": False, "preload": False,
        "tags": [FOLDER_NAV, "demonstracao", *extra_tags],
        "timeSettings": {"timezone": "America/Sao_Paulo", "from": time_from, "to": time_to, "autoRefresh": "",
                         "autoRefreshIntervals": ["1m", "5m", "15m", "1h"], "hideTimepicker": False,
                         "fiscalYearStartMonth": 0},
        "title": title, "variables": variables}}


def utc(d, end=False):
    t = dt.datetime.combine(d, dt.time(23, 59, 59) if end else dt.time(0)) + dt.timedelta(hours=3)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def fmt(d):
    return d.strftime("%d/%m/%Y")


PIE = {"pieType": "donut", "displayLabels": ["value"],
       "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
       "legend": {"displayMode": "table", "placement": "right", "showLegend": True, "values": ["value"]},
       "tooltip": {"mode": "single", "sort": "none", "hideZeros": False}}
BARGAUGE = {"displayMode": "basic", "legend": {"showLegend": False}, "maxVizHeight": 26, "minVizHeight": 14,
            "namePlacement": "left", "orientation": "horizontal",
            "reduceOptions": {"calcs": ["sum"], "fields": "", "values": False},
            "showUnfilled": True, "sizing": "auto", "valueMode": "color"}
TS_BARS = {"drawStyle": "bars", "fillOpacity": 85, "lineWidth": 1, "barAlignment": 0, "showPoints": "never",
           "stacking": {"mode": "normal", "group": "A"}, "axisSoftMin": 0, "spanNulls": False}
LEGEND_BOTTOM = {"displayMode": "list", "placement": "bottom", "showLegend": True, "calcs": []}
TOOLTIP = {"mode": "multi", "sort": "desc", "hideZeros": True}


def series_by_mod(field_suffix, time_field):
    """Overrides de nome e cor das series geradas por partitionByValues(modalidade) e eixo em DD/MM."""
    return [ov(time_field, ("unit", "time:DD/MM"))] + [ov(f"{field_suffix} {m}", ("displayName", NOME_MOD[m]), ("color", color(COR_MOD[m]))) for m in MODALIDADES]


def table_defaults():
    return {"custom": {"align": "auto", "cellOptions": {"type": "auto"}, "filterable": True, "minWidth": 80},
            "thresholds": steps((None, "text"))}


def col_overrides(cols):
    out = []
    for name, kind in cols.items():
        if kind == "data":
            out.append(ov(name, ("unit", DATA), ("custom.width", 110)))
        elif kind == "brl":
            out.append(ov(name, ("unit", BRL), ("decimals", 2), ("custom.align", "right"), ("custom.minWidth", 125)))
        elif kind == "mod":
            out.append(ov(name, ("mappings", MAP_MOD), ("custom.cellOptions", {"type": "color-text"}),
                          ("custom.width", 100)))
        elif kind == "band":
            out.append(ov(name, ("mappings", MAP_BAND), ("custom.width", 150)))
        elif kind == "int":
            out.append(ov(name, ("unit", INTEIRO), ("decimals", 0), ("custom.width", 110)))
        elif kind == "pct":
            out.append(ov(name, ("unit", PCT), ("decimals", 2), ("custom.width", 110)))
    return out


# ------------------------------------------------------------------ 1. Visao Geral
def visao_geral(mov):
    d = Dash(HOME, MOV_COLS, mov)
    f = lambda **k: [t_time("data_venda"), t_filter("data_venda", **k)]
    banner(d, f"Posto de combustível fictício · maquininhas Cielo e conta Sicredi · vendas de {fmt(PER_INI)} a "
              f"{fmt(PER_FIM)} (30 dias) · posição em {fmt(DATA_BASE)}. Use o seletor de período e os filtros de "
              "modalidade e bandeira.")
    d.row("Resumo do período")
    cinza = steps((None, COR["cinza"]))
    stat(d, "Movimentações", "Quantidade de transações aprovadas (Pix, débito e crédito) no período e filtros.",
         f() + [t_reduce()], "qtd_transacoes", INTEIRO, cinza, decimals=0)
    stat(d, "Total recebido (bruto)", "Valor bruto das vendas pagas pelos clientes no período.",
         f() + [t_reduce()], "valor_bruto", BRL, steps((None, COR["azul"])))
    stat(d, "Líquido após taxas", "Bruto menos as taxas da adquirente (MDR): valor que a Cielo repassa ao Sicredi.",
         f() + [t_reduce()], "valor_liquido", BRL, steps((None, COR["verde"])))
    stat(d, "Taxas Cielo (MDR)", "Soma das taxas descontadas pela adquirente no período.",
         f() + [t_reduce()], "valor_taxa", BRL, cinza)
    stat(d, "Taxa média", "Taxas ÷ valor bruto no período (média ponderada pelo valor).",
         f() + [t_reduce(), t_binary("valor_taxa", "/", "valor_bruto", "taxa"), t_binary("taxa", "*", 100, "taxa_media")],
         "taxa_media", PCT, cinza)
    stat(d, "Ticket médio", "Valor bruto ÷ quantidade de transações.",
         f() + [t_reduce(), t_binary("valor_bruto", "/", "qtd_transacoes", "ticket_medio")],
         "ticket_medio", BRL, cinza)

    d.row("Por modalidade")
    for m in MODALIDADES:
        stat(d, f"{NOME_MOD[m]} · liquidação {PRAZO_MOD[m]}",
             f"Valor bruto recebido em {NOME_MOD[m]} no período. Prazo de liquidação na conta: {PRAZO_MOD[m]}.",
             f(extra=[f_eq("modalidade", m)]) + [t_reduce()], "valor_bruto", BRL,
             steps((None, COR_MOD[m])), w=8)

    d.row("Evolução diária")
    d.panel("timeseries", "Recebimentos por dia e modalidade (bruto)",
            "Valor bruto vendido por dia, empilhado por modalidade. Fins de semana e vésperas de feriado concentram "
            "mais movimento.",
            f() + [t_groupby(["data_venda", "modalidade"], {"valor_bruto": "sum"}),
                   tr("partitionByValues", {"fields": ["modalidade"], "keepFields": False})],
            {"legend": LEGEND_BOTTOM, "tooltip": TOOLTIP},
            {"unit": BRL, "decimals": 0, "min": 0, "color": {"mode": "palette-classic"}, "custom": TS_BARS},
            series_by_mod("valor_bruto (sum)", "data_venda"), w=16, h=9)
    d.panel("piechart", "Participação por modalidade",
            "Fatia de cada modalidade no valor bruto do período.",
            f() + [t_groupby(["modalidade"], {"valor_bruto": "sum"})] +
            t_share(("modalidade", list(NOME_MOD)), "valor_bruto (sum)"),
            PIE, {"unit": PCT, "decimals": 1, "color": {"mode": "palette-classic"}},
            per_code(NOME_MOD, COR_MOD, "pct_"), w=8, h=9)

    d.row("Bandeiras")
    d.panel("piechart", "Distribuição por bandeira (cartões)",
            "Débito + crédito por bandeira. Pix não tem bandeira e fica de fora deste gráfico.",
            f(extra=[f_regex("modalidade", "^(debito|credito)$")]) +
            [t_groupby(["bandeira"], {"valor_bruto": "sum"})] +
            t_share(("bandeira", [b for b in NOME_BAND if b != "sem_bandeira"]), "valor_bruto (sum)"),
            PIE, {"unit": PCT, "decimals": 1, "color": {"mode": "palette-classic"}},
            per_code({k: v for k, v in NOME_BAND.items() if k != "sem_bandeira"}, COR_BAND, "pct_"), w=10, h=9)
    d.panel("barchart", "Débito × crédito por bandeira",
            "Valor bruto de cada bandeira separado entre débito e crédito.",
            f(extra=[f_regex("modalidade", "^(debito|credito)$")]) +
            [t_groupby(["bandeira", "modalidade"], {"valor_bruto": "sum"}),
             tr("groupingToMatrix", {"columnField": "modalidade", "rowField": "bandeira",
                                     "valueField": "valor_bruto (sum)", "emptyValue": "zero"}),
             t_organize(order=["bandeira\\modalidade", "debito", "credito"])],
            {"orientation": "horizontal", "stacking": "none", "showValue": "never", "barWidth": 0.8,
             "groupWidth": 0.75, "xTickLabelRotation": 0, "xField": "bandeira\\modalidade",
             "legend": LEGEND_BOTTOM, "tooltip": TOOLTIP},
            {"unit": BRL, "decimals": 0, "color": {"mode": "palette-classic"},
             "custom": {"fillOpacity": 85, "lineWidth": 0}},
            [ov("bandeira\\modalidade", ("mappings", MAP_BAND)),
             ov("debito", ("displayName", "Débito"), ("color", color(COR_MOD["debito"]))),
             ov("credito", ("displayName", "Crédito"), ("color", color(COR_MOD["credito"])))], w=14, h=9)

    d.row("Comparativo entre modalidades")
    d.panel("barchart", "Bruto, taxas e líquido por modalidade",
            "Quanto cada modalidade vendeu, quanto ficou em taxa e quanto chega líquido à conta.",
            f() + [t_groupby(["modalidade"], {"valor_bruto": "sum", "valor_taxa": "sum", "valor_liquido": "sum"})],
            {"orientation": "auto", "stacking": "none", "showValue": "never", "barWidth": 0.85, "groupWidth": 0.7,
             "xTickLabelRotation": 0, "xField": "modalidade", "legend": LEGEND_BOTTOM, "tooltip": TOOLTIP,
             "text": {"valueSize": 11}},
            {"unit": BRL, "decimals": 0, "color": {"mode": "palette-classic"},
             "custom": {"fillOpacity": 85, "lineWidth": 0}},
            [ov("modalidade", ("mappings", MAP_MOD)),
             ov("valor_bruto (sum)", ("displayName", "Bruto"), ("color", color("#5794F2"))),
             ov("valor_taxa (sum)", ("displayName", "Taxas"), ("color", color(COR["laranja"]))),
             ov("valor_liquido (sum)", ("displayName", "Líquido"), ("color", color(COR["verde"])))], w=10, h=10)
    d.panel("table", "Resumo por modalidade e bandeira",
            "Totais do período por modalidade e bandeira. Taxa média = taxas ÷ bruto. Clique no cabeçalho para ordenar.",
            f() + [t_groupby(["modalidade", "bandeira"], {"qtd_transacoes": "sum", "valor_bruto": "sum",
                                                         "valor_taxa": "sum", "valor_liquido": "sum"}),
                   t_binary("valor_bruto (sum)", "/", "qtd_transacoes (sum)", "Ticket médio"),
                   t_binary("valor_taxa (sum)", "/", "valor_bruto (sum)", "tx"),
                   t_binary("tx", "*", 100, "Taxa média"),
                   t_organize(exclude=["tx"], order=["modalidade", "bandeira", "qtd_transacoes (sum)", "valor_bruto (sum)",
                                                     "valor_taxa (sum)", "Taxa média", "valor_liquido (sum)", "Ticket médio"],
                              rename={"modalidade": "Modalidade", "bandeira": "Bandeira",
                                      "qtd_transacoes (sum)": "Transações", "valor_bruto (sum)": "Bruto",
                                      "valor_taxa (sum)": "Taxas", "valor_liquido (sum)": "Líquido"})],
            {"cellHeight": "sm", "showHeader": True,
             "footer": {"show": True, "reducer": ["sum"], "countRows": False, "fields": ["Transações", "Bruto", "Taxas", "Líquido"]},
             "sortBy": [{"displayName": "Bruto", "desc": True}]},
            table_defaults(),
            col_overrides({"Modalidade": "mod", "Bandeira": "band", "Transações": "int", "Bruto": "brl",
                           "Taxas": "brl", "Taxa média": "pct", "Líquido": "brl", "Ticket médio": "brl"}), w=14, h=10)

    d.row("Movimento diário")
    d.panel("table", "Movimento por dia",
            "Bruto por dia e modalidade, com o total do dia.",
            f() + [t_groupby(["data_venda", "modalidade"], {"valor_bruto": "sum"}),
                   tr("groupingToMatrix", {"columnField": "modalidade", "rowField": "data_venda",
                                           "valueField": "valor_bruto (sum)", "emptyValue": "zero"}),
                   t_time("data_venda\\modalidade"),
                   tr("calculateField", {"mode": "reduceRow", "reduce": {"reducer": "sum", "include": ["pix", "debito", "credito"]},
                                         "alias": "Total do dia", "replaceFields": False}),
                   t_organize(order=["data_venda\\modalidade", "pix", "debito", "credito", "Total do dia"],
                              rename={"data_venda\\modalidade": "Data", "pix": "Pix", "debito": "Débito",
                                      "credito": "Crédito"})],
            {"cellHeight": "sm", "showHeader": True,
             "footer": {"show": True, "reducer": ["sum"], "countRows": False, "fields": ["Pix", "Débito", "Crédito", "Total do dia"]},
             "sortBy": [{"displayName": "Data", "desc": True}]},
            table_defaults(),
            col_overrides({"Data": "data", "Pix": "brl", "Débito": "brl", "Crédito": "brl", "Total do dia": "brl"}),
            w=24, h=14)
    return resource(d, "PayRecon — Visão Geral · DEMONSTRAÇÃO",
                    "DEMONSTRAÇÃO — DADOS SIMULADOS. Vendas recebidas por Pix, débito e crédito (Cielo), por "
                    "modalidade e bandeira, com evolução diária e taxas.",
                    utc(PER_INI), utc(PER_FIM, end=True), [VAR_MOD, VAR_BAND], ["vendas"])


# ------------------------------------------------------------------ 2. Agenda
def agenda(rows):
    d = Dash("payrecon-agenda", AG_COLS, rows)
    base = lambda **k: [t_time("data_prevista", "data_venda"), t_filter(**k)]
    rng = lambda **k: [t_time("data_prevista", "data_venda"), t_filter("data_prevista", sit=True, **k)]
    banner(d, f"Agenda de recebimentos da Cielo para o posto fictício · posição em {fmt(DATA_BASE)} às 08h00 · "
              "Pix D+0, débito D+1 (dia útil) e crédito à vista D+30 (próximo dia útil). Valores futuros são "
              "previsões, nunca atraso.")
    d.row("Resumo da agenda")
    cinza = steps((None, COR["cinza"]))
    stat(d, f"Aguardando crédito · {DATA_BASE:%d/%m}",
         "Previsto para a data-base: o crédito pode cair ao longo do dia e ainda não é atraso.",
         base(extra=[f_eq("dias_para_liquidacao", 0)]) + [t_reduce()], "valor_liquido", BRL,
         steps((None, COR["azul"])))
    stat(d, "Próximos 7 dias", "Líquido previsto da data-base até 6 dias depois (independe do seletor de período).",
         base(extra=[f_between("dias_para_liquidacao", -0.5, 6.5)]) + [t_reduce()], "valor_liquido", BRL, cinza)
    stat(d, "Próximos 30 dias", "Líquido previsto da data-base até 29 dias depois (independe do seletor de período).",
         base(extra=[f_between("dias_para_liquidacao", -0.5, 29.5)]) + [t_reduce()], "valor_liquido", BRL, cinza)
    stat(d, "Débito a receber", "Débito ainda não liquidado (data prevista na data-base ou depois).",
         base(mod=False, extra=[f_eq("modalidade", "debito"), f_ge("dias_para_liquidacao", 0)]) + [t_reduce()],
         "valor_liquido", BRL, steps((None, COR_MOD["debito"])))
    stat(d, "Crédito a receber", "Crédito ainda não liquidado (data prevista na data-base ou depois).",
         base(mod=False, extra=[f_eq("modalidade", "credito"), f_ge("dias_para_liquidacao", 0)]) + [t_reduce()],
         "valor_liquido", BRL, steps((None, COR_MOD["credito"])))
    stat(d, "Taxas a descontar", "Taxas (MDR) embutidas nos recebíveis ainda não liquidados.",
         base(extra=[f_ge("dias_para_liquidacao", 0)]) + [t_reduce()], "valor_taxa", BRL, cinza)

    d.row("Prazos de liquidação")
    d.panel("canvas", "", "Quanto falta receber em cada modalidade e o prazo de liquidação de cada uma. Pix é "
            "liquidado no mesmo dia e não forma agenda.",
            base(mod=False, extra=[f_ge("dias_para_liquidacao", 0)]) +
            [t_groupby(["modalidade"], {"valor_liquido": "sum"}),
             tr("rowsToFields", {"mappings": [{"fieldName": "modalidade", "handlerKey": "field.name"},
                                              {"fieldName": "valor_liquido (sum)", "handlerKey": "field.value"}]})],
            canvas_prazos(), {"unit": BRL, "decimals": 2, "color": {"mode": "fixed", "fixedColor": "#ffffff"},
                              "noValue": "R$ 0,00"},
            w=24, h=6)

    d.row("Recebimentos previstos por dia")
    d.panel("timeseries", "Valores previstos por dia (líquido)",
            "Líquido a receber por data prevista, separado por modalidade. Use o seletor de período para ver o "
            "histórico já liquidado. Feriados empurram o crédito para o próximo dia útil (ex.: 20/11 → 23/11).",
            rng() + [t_groupby(["data_prevista", "modalidade"], {"valor_liquido": "sum"}),
                     tr("partitionByValues", {"fields": ["modalidade"], "keepFields": False})],
            {"legend": LEGEND_BOTTOM, "tooltip": TOOLTIP},
            {"unit": BRL, "decimals": 0, "min": 0, "color": {"mode": "palette-classic"}, "custom": TS_BARS},
            series_by_mod("valor_liquido (sum)", "data_prevista"), w=24, h=9)

    d.row("Evolução dos recebíveis")
    d.panel("timeseries", "Recebíveis acumulados no período",
            "Soma acumulada do líquido previsto ao longo do período selecionado (linha) e o valor de cada dia "
            "(barras).",
            rng() + [t_groupby(["data_prevista"], {"valor_liquido": "sum"}),
                     tr("calculateField", {"mode": "cumulativeFunctions",
                                           "cumulative": {"field": "valor_liquido (sum)", "reducer": "sum"},
                                           "alias": "Acumulado", "replaceFields": False})],
            {"legend": LEGEND_BOTTOM, "tooltip": TOOLTIP},
            {"unit": BRL, "decimals": 0, "min": 0, "color": {"mode": "palette-classic"},
             "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 12, "showPoints": "never",
                        "spanNulls": True, "axisSoftMin": 0}},
            [ov("data_prevista", ("unit", "time:DD/MM")),
             ov("Acumulado", ("color", color(COR["verde"])), ("custom.axisPlacement", "left")),
             ov("valor_liquido (sum)", ("displayName", "No dia"), ("color", color("#5794F2")),
                ("custom.drawStyle", "bars"), ("custom.fillOpacity", 70), ("custom.axisPlacement", "right"))],
            w=16, h=9)
    d.panel("bargauge", "A receber por bandeira",
            "Líquido ainda não liquidado por bandeira (data-base em diante).",
            base(extra=[f_ge("dias_para_liquidacao", 0)]) +
            [t_groupby(["bandeira"], {"valor_liquido": "sum"}), t_sort("valor_liquido (sum)", desc=True),
             t_rows_to_fields("bandeira", "valor_liquido (sum)")],
            dict(BARGAUGE, namePlacement="top"), {"unit": BRL, "decimals": 2, "min": 0, "color": {"mode": "palette-classic"}},
            per_code(NOME_BAND, COR_BAND), w=8, h=9)

    d.row("Agenda detalhada")
    d.panel("table", "Agenda de recebimentos",
            "Uma linha por dia de venda, modalidade e bandeira. Situação: Previsto (futuro), Aguardando crédito "
            "(data-base), Liquidado ou Não localizado (vencido sem crédito identificado; ver Conciliação).",
            rng() + [t_organize(exclude=["dias_para_liquidacao"],
                                order=["data_prevista", "data_venda", "prazo_dias", "modalidade", "bandeira",
                                       "valor_bruto", "valor_taxa", "valor_liquido", "situacao"],
                                rename={"data_prevista": "Data prevista", "data_venda": "Data da venda",
                                        "prazo_dias": "Prazo", "modalidade": "Modalidade", "bandeira": "Bandeira",
                                        "valor_bruto": "Bruto", "valor_taxa": "Taxa", "valor_liquido": "Líquido",
                                        "situacao": "Situação"}),
                     t_sort("Data prevista")],
            {"cellHeight": "sm", "showHeader": True,
             "footer": {"show": True, "reducer": ["sum"], "countRows": False, "fields": ["Bruto", "Taxa", "Líquido"]},
             "sortBy": [{"displayName": "Data prevista", "desc": False}]},
            table_defaults(),
            col_overrides({"Data prevista": "data", "Data da venda": "data", "Modalidade": "mod",
                           "Bandeira": "band", "Bruto": "brl", "Taxa": "brl", "Líquido": "brl"}) +
            [ov("Prazo", ("unit", "prefix:D+"), ("decimals", 0), ("custom.width", 80)),
             ov("Situação", ("mappings", MAP_SIT_AG), ("custom.cellOptions", {"type": "color-background"}),
                ("custom.width", 170))],
            w=24, h=13)
    var_sit = var_custom("situacao", "Situação", {k: v[0] for k, v in SIT_AGENDA.items()},
                         "Situação do recebível na data-base")
    return resource(d, "PayRecon — Agenda de Recebimentos · DEMONSTRAÇÃO",
                    "DEMONSTRAÇÃO — DADOS SIMULADOS. Valores previstos para recebimento por dia, modalidade e "
                    "bandeira, com os prazos de liquidação da Cielo.",
                    utc(DATA_BASE), utc(DATA_BASE + dt.timedelta(days=29), end=True),
                    [VAR_MOD, VAR_BAND, var_sit], ["agenda"])


def canvas_el(kind, name, text, size, color_, place, bg="transparent", border=None, radius=4, align="center",
              border_width=0):
    el = {"type": kind, "name": name,
          "config": {"text": text, "size": size, "color": color_, "align": align, "valign": "middle"},
          "constraint": {"horizontal": "scale", "vertical": "scale"},
          "placement": dict(zip(("left", "top", "right", "bottom"), place)),
          "background": {"color": {"fixed": bg} if isinstance(bg, str) else bg},
          "border": {"color": {"fixed": border or "transparent"}, "width": border_width, "radius": radius}}
    return el


def fixed(t):
    return {"mode": "fixed", "fixed": t}


def field(f):
    return {"mode": "field", "field": f, "fixed": ""}


def canvas_root(elements):
    return {"inlineEditing": False, "showAdvancedTypes": False, "panZoom": False, "infinitePan": False,
            "root": {"type": "frame", "name": "Element root", "elements": elements,
                     "background": {"color": {"fixed": "transparent"}},
                     "border": {"color": {"fixed": "transparent"}},
                     "constraint": {"horizontal": "left", "vertical": "top"}}}


def canvas_prazos():
    els = []
    cards = [("pix", "Pix", "D+0", "liquidado no mesmo dia, inclusive fim de semana"),
             ("debito", "Débito", "D+1", "próximo dia útil após a venda"),
             ("credito", "Crédito à vista", "D+30", "30 dias corridos; se cair em fim de semana ou feriado, "
                                                    "vai para o próximo dia útil")]
    for i, (m, nome, prazo, regra) in enumerate(cards):
        l, r = 1 + i * 33, 100 - (1 + i * 33 + 31)
        els.append(canvas_el("rectangle", f"card {m}", fixed(""), 12, {"fixed": "#ffffff"}, (l, 4, r, 4),
                             bg="#1b1d22", border=COR_MOD[m], border_width=2))
        els.append(canvas_el("rectangle", f"prazo {m}", fixed(prazo), 15, {"fixed": "#ffffff"},
                             (l + 2, 12, 100 - (l + 2 + 7), 64), bg=COR_MOD[m], radius=3))
        els.append(canvas_el("text", f"nome {m}", fixed(nome), 16, {"fixed": "#ffffff"},
                             (l + 10, 12, r + 2, 64), align="left"))
        els.append(canvas_el("text", f"regra {m}", fixed(regra), 11, {"fixed": COR["texto2"]},
                             (l + 2, 40, r + 2, 40), align="left"))
        if m == "pix":
            els.append(canvas_el("text", "valor pix", fixed("Sem saldo a receber"), 20, {"fixed": COR["texto2"]},
                                 (l + 2, 62, r + 2, 10), align="left"))
        else:
            els.append(canvas_el("metric-value", f"valor {m}", field(m), 24, {"fixed": "#ffffff"},
                                 (l + 2, 60, r + 2, 10), align="left"))
            els.append(canvas_el("text", f"rotulo {m}", fixed("a receber (líquido)"), 11, {"fixed": COR["texto2"]},
                                 (l + 2, 60, r + 2, 10), align="right"))
    return canvas_root(els)


# ------------------------------------------------------------------ 3. Conciliacao
def conciliacao(rows):
    d = Dash("payrecon-conciliacao", CONC_COLS, rows)
    f = lambda **k: [t_time("data_prevista"), t_filter("data_prevista", sit=True, **k)]
    avaliado = f_eq("avaliado", 1)
    nao_futuro = f_regex("situacao", "^(?!a_receber$).*")
    banner(d, f"Cielo (previsto) × Sicredi (creditado) para o posto fictício · posição em {fmt(DATA_BASE)} às 08h00 · "
              "valores com data futura ficam como A receber e não entram como atraso; diferença sem ajuste "
              "informado fica Em análise, sem presumir dívida da adquirente.")
    d.row("Resumo da conciliação")
    cinza = steps((None, COR["cinza"]))
    stat(d, "Previsto pela Cielo", "Líquido que a Cielo informou para pagamento até a data-base, já com ajustes "
         "informados por ela (aluguel, cancelamento, chargeback). Não inclui valores a receber no futuro.",
         f(extra=[nao_futuro]) + [t_reduce()], "valor_esperado", BRL, cinza)
    stat(d, "Recebido no Sicredi", "Créditos da Cielo identificados no extrato Sicredi no período.",
         f(extra=[nao_futuro]) + [t_reduce()], "valor_recebido", BRL, steps((None, COR["azul"])))
    stat(d, "Conciliado", "Valor recebido que bate com o previsto (inclusive quando a diferença é explicada por "
         "ajuste informado pela Cielo).", f(extra=[f_eq("conciliado", 1)]) + [t_reduce()], "valor_recebido", BRL,
         steps((None, COR["verde"])))
    stat(d, "Pendente", "Previsto ainda sem crédito: aguardando (vence na data-base) ou não localizado (vencido sem "
         "crédito identificado).", f(extra=[f_eq("pendente", 1)]) + [t_reduce()], "valor_esperado", BRL,
         steps((None, COR["cinza"])))
    stat(d, "Divergente (em análise)", "Soma das diferenças absolutas sem ajuste correspondente. Diferença não é "
         "presumida como dívida da Cielo: fica em análise até a explicação.",
         f(extra=[f_eq("divergente", 1)]) + [tr("calculateField", {"mode": "unary", "unary": {"operator": "abs", "fieldName": "diferenca"},
                                                                    "alias": "divergencia", "replaceFields": False}),
                                             t_reduce()], "divergencia", BRL,
         steps((None, COR["verde"]), (0.01, COR["laranja"])))
    stat(d, "Lançamentos conciliados", "Percentual dos lançamentos vencidos (conciliados, divergentes e não "
         "localizados) que foram conciliados. Aguardando e a receber não entram na conta.",
         f(extra=[avaliado]) + [t_reduce("mean"), t_binary("conciliado", "*", 100, "indice")], "indice", PCT,
         steps((None, COR["vermelho"]), (95, COR["laranja"]), (98, COR["verde"])), decimals=1)

    d.row("Indicadores de divergência")
    d.panel("canvas", "", "Do previsto pela Cielo ao creditado no Sicredi, no período e filtros selecionados. "
            "A diferença líquida soma divergências e não localizados; pendências aguardando não entram.",
            f(extra=[nao_futuro]) + [t_reduce()], canvas_fluxo(),
            {"unit": BRL, "decimals": 2, "color": {"mode": "thresholds"}, "thresholds": steps((None, "#ffffff"))},
            [ov("diferenca", ("thresholds", steps((None, COR["laranja"]), (-0.01, "#ffffff"), (0.01, COR["laranja"])))),
             ov("/^(conciliado|pendente|divergente|avaliado)$/", ("decimals", 0), regex=True),
             ov("divergente", ("thresholds", steps((None, COR["cinza"]), (1, COR["laranja"]))),
                ("unit", "suffix: em análise")),
             ov("pendente", ("thresholds", steps((None, COR["cinza"]), (1, COR["azul"]))),
                ("unit", "suffix: pendentes")),
             ov("conciliado", ("thresholds", steps((None, COR["cinza"]), (1, COR["verde"]))),
                ("unit", "suffix: conciliados"))],
            w=16, h=8)
    d.panel("bargauge", "Lançamentos por situação",
            "Quantidade de lançamentos (data prevista × modalidade × bandeira) em cada situação.",
            f() + [t_groupby(["situacao"], {"valor_esperado": "count"}),
                   t_rows_to_fields("situacao", "valor_esperado (count)")],
            BARGAUGE, {"unit": "none", "decimals": 0, "min": 0, "color": {"mode": "fixed", "fixedColor": "#8E8E9E"}},
            per_code({k: v[0] for k, v in SIT_CONC.items()}, {k: v[1] for k, v in SIT_CONC.items()}), w=8, h=8)

    d.row("Comparativo diário")
    d.panel("timeseries", "Previsto × recebido por dia",
            "Barras: previsto pela Cielo (com ajustes) e creditado no Sicredi em cada data. Linha: diferença "
            "(recebido − previsto), eixo da direita. No dia da data-base o crédito ainda pode cair.",
            f(extra=[nao_futuro]) + [t_groupby(["data_prevista"], {"valor_esperado": "sum", "valor_recebido": "sum",
                                                                   "diferenca": "sum"})],
            {"legend": LEGEND_BOTTOM, "tooltip": {"mode": "multi", "sort": "none", "hideZeros": False}},
            {"unit": BRL, "decimals": 2, "color": {"mode": "palette-classic"},
             "custom": {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1, "barAlignment": 0,
                        "showPoints": "never", "axisSoftMin": 0}},
            [ov("data_prevista", ("unit", "time:DD/MM")),
             ov("valor_esperado (sum)", ("displayName", "Previsto Cielo"), ("color", color("#8E8E9E")),
                ("custom.barWidthFactor", 0.45), ("custom.barAlignment", -1)),
             ov("valor_recebido (sum)", ("displayName", "Recebido Sicredi"), ("color", color(COR["azul"])),
                ("custom.barWidthFactor", 0.45), ("custom.barAlignment", 1)),
             ov("diferenca (sum)", ("displayName", "Diferença"), ("color", color(COR["laranja"])),
                ("custom.drawStyle", "line"), ("custom.lineWidth", 2), ("custom.fillOpacity", 0),
                ("custom.showPoints", "always"), ("custom.pointSize", 5), ("custom.axisPlacement", "right"),
                ("custom.axisSoftMin", -200), ("custom.axisSoftMax", 200), ("custom.axisLabel", "Diferença"))],
            w=24, h=10)

    d.row("Ocorrências")
    d.panel("table", "Ocorrências da conciliação",
            "Lançamentos que pedem atenção ou explicação: ajustes informados pela Cielo, divergências em análise, "
            "créditos não localizados. Valores aguardando crédito na data-base não são ocorrência.",
            f(extra=[f_regex("situacao", "^(conciliado_ajuste|divergente|nao_localizado)$")]) +
            [t_organize(exclude=["conciliado", "pendente", "divergente", "avaliado", "valor_previsto"],
                        order=["situacao", "data_prevista", "modalidade", "bandeira", "valor_ajustes", "valor_esperado",
                               "valor_recebido", "diferenca", "ocorrencia"],
                        rename={"situacao": "Situação", "data_prevista": "Data prevista", "modalidade": "Modalidade",
                                "bandeira": "Bandeira", "valor_ajustes": "Ajustes Cielo",
                                "valor_esperado": "Previsto", "valor_recebido": "Recebido",
                                "diferenca": "Diferença", "ocorrencia": "Ocorrência"}),
             t_sort("Data prevista", desc=True)],
            {"cellHeight": "sm", "showHeader": True, "footer": {"show": False, "reducer": ["sum"], "countRows": False},
             "sortBy": [{"displayName": "Situação", "desc": True}]},
            table_defaults(), conc_table_overrides() + [ov("Ocorrência", ("custom.minWidth", 420), ("custom.wrapText", True)),
             ov("Ajustes Cielo", ("custom.width", 120)), ov("Previsto", ("custom.width", 125)),
             ov("Recebido", ("custom.width", 125)), ov("Diferença", ("custom.width", 115)),
             ov("Bandeira", ("custom.width", 140)), ov("Modalidade", ("custom.width", 95)),
             ov("Data prevista", ("custom.width", 110))], w=24, h=11)

    d.row("Conciliação detalhada")
    d.panel("table", "Conciliação por data, modalidade e bandeira",
            "Previsto = líquido das vendas na agenda Cielo; Ajustes = descontos/créditos informados pela Cielo; "
            "Esperado = previsto + ajustes; Diferença = recebido − esperado (só em lançamentos vencidos).",
            f() + [t_organize(exclude=["conciliado", "pendente", "divergente", "avaliado"],
                              order=["data_prevista", "modalidade", "bandeira", "valor_previsto", "valor_ajustes",
                                     "valor_esperado", "valor_recebido", "diferenca", "situacao", "ocorrencia"],
                              rename={"data_prevista": "Data prevista", "modalidade": "Modalidade",
                                      "bandeira": "Bandeira", "valor_previsto": "Previsto (vendas)",
                                      "valor_ajustes": "Ajustes Cielo", "valor_esperado": "Previsto",
                                      "valor_recebido": "Recebido", "diferenca": "Diferença",
                                      "situacao": "Situação", "ocorrencia": "Ocorrência"}),
                   t_sort("Data prevista", desc=True)],
            {"cellHeight": "sm", "showHeader": True,
             "footer": {"show": True, "reducer": ["sum"], "countRows": False,
                        "fields": ["Previsto (vendas)", "Ajustes Cielo", "Previsto", "Recebido", "Diferença"]},
             "sortBy": [{"displayName": "Data prevista", "desc": True}]},
            table_defaults(), conc_table_overrides() + col_overrides({"Previsto (vendas)": "brl"}) +
            [ov("Ocorrência", ("custom.minWidth", 260))], w=24, h=13)

    d.row("Regras da conciliação", collapse=True)
    d.panel("text", "Como a conciliação é classificada", "", [], {"mode": "markdown", "content": REGRAS,
            "code": {"language": "plaintext", "showLineNumbers": False, "showMiniMap": False}}, {},
            w=24, h=11, queries=False)
    var_sit = var_custom("situacao", "Situação", {k: v[0] for k, v in SIT_CONC.items()}, "Situação da conciliação")
    return resource(d, "PayRecon — Conciliação Financeira · DEMONSTRAÇÃO",
                    "DEMONSTRAÇÃO — DADOS SIMULADOS. Comparação entre o previsto pela Cielo e os créditos no Sicredi: "
                    "conciliados, pendentes e divergentes.",
                    utc(PER_INI), utc(DATA_BASE, end=True), [VAR_MOD, VAR_BAND, var_sit], ["conciliacao"])


def conc_table_overrides():
    return col_overrides({"Data prevista": "data", "Modalidade": "mod", "Bandeira": "band", "Ajustes Cielo": "brl",
                          "Previsto": "brl", "Recebido": "brl"}) + [
        ov("Diferença", ("unit", BRL), ("decimals", 2), ("custom.align", "right"),
           ("custom.cellOptions", {"type": "color-text"}),
           ("thresholds", steps((None, COR["laranja"]), (-0.005, "text"), (0.005, COR["laranja"])))),
        ov("Situação", ("mappings", MAP_SIT_CONC), ("custom.cellOptions", {"type": "color-background"}),
           ("custom.width", 190))]


def canvas_fluxo():
    els = []
    boxes = [("previsto", "Previsto nas vendas", "valor_previsto", "#8E8E9E"),
             ("ajustes", "Ajustes informados", "valor_ajustes", "#8E8E9E"),
             ("esperado", "Previsto pela Cielo", "valor_esperado", "#8E8E9E"),
             ("recebido", "Recebido no Sicredi", "valor_recebido", COR["azul"]),
             ("diferenca", "Diferença líquida", "diferenca", COR["laranja"])]
    n = len(boxes)
    width = 100 / n
    for i, (key, label, fld, cor) in enumerate(boxes):
        l = i * width + 1
        r = 100 - (l + width - 3)
        els.append(canvas_el("rectangle", f"box {key}", fixed(""), 12, {"fixed": "#ffffff"}, (l, 8, r, 42),
                             bg="#1b1d22", border=cor, border_width=2))
        els.append(canvas_el("text", f"rotulo {key}", fixed(label), 12, {"fixed": COR["texto2"]}, (l, 12, r, 72)))
        els.append(canvas_el("metric-value", f"valor {key}", field(fld), 18,
                             {"field": fld, "fixed": "#ffffff"}, (l, 30, r, 46)))
        if i < n - 1:
            els.append(canvas_el("text", f"seta {key}", fixed("→" if i != 0 else "+"), 22, {"fixed": COR["texto2"]},
                                 (l + width - 3, 20, 100 - (l + width - 1), 56)))
    chips = [("conciliado", "conciliado"), ("pendente", "pendente"), ("divergente", "divergente")]
    for i, (key, fld) in enumerate(chips):
        l = 18 + i * 22
        els.append(canvas_el("rectangle", f"chip {key}", field(fld), 13, {"fixed": "#ffffff"},
                             (l, 66, 100 - (l + 20), 12), bg={"field": fld, "fixed": COR["cinza"]}, radius=3))
    return canvas_root(els)


REGRAS = """| Situação | Quando | Leitura |
|---|---|---|
| **Conciliado** | Recebido no Sicredi = previsto pela Cielo (tolerância de R$ 0,01) | Ok |
| **Conciliado com ajuste** | A diferença é explicada por ajuste informado pela Cielo (aluguel de maquininha, cancelamento, chargeback) | Ok: o desconto é legítimo e está documentado |
| **Divergente (em análise)** | Recebido ≠ previsto e não há ajuste que explique | Investigar: pode ser ajuste ainda não lido, tarifa bancária ou erro. **Não é presumido como dívida da Cielo** |
| **Não localizado** | Data prevista já passou e nenhum crédito correspondente foi identificado | Verificar crédito agrupado, data diferente ou outra conta antes de reclamar |
| **Aguardando crédito** | Data prevista = data-base | Ainda não é atraso: o banco pode creditar ao longo do dia |
| **A receber** | Data prevista depois da data-base | Previsão: nunca entra como atraso nem como divergência |

**Prazos de liquidação usados:** Pix D+0 (inclusive fim de semana), débito D+1 dia útil, crédito à vista D+30 corridos (se cair em fim de semana ou feriado, vai para o próximo dia útil).

**Chave da conciliação:** data prevista × modalidade × bandeira (um crédito da Cielo no extrato Sicredi por chave). *Previsto pela Cielo* = líquido das vendas + ajustes informados. *Diferença* = recebido − previsto, só em lançamentos vencidos.

*Demonstração: todos os valores e ocorrências são simulados.*"""


# ------------------------------------------------------------------ saida
def build():
    mov, ag, conc = gerar()
    return {
        "visao-geral.json": visao_geral(mov),
        "agenda.json": agenda(ag),
        "conciliacao.json": conciliacao(conc),
        "_folder.json": {"apiVersion": "folder.grafana.app/v1", "kind": "Folder",
                         "metadata": {"name": "gd-payrecon"}, "spec": {"title": "PayRecon (demonstração)"}},
    }


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="so confere se os JSON estao atualizados")
    args = ap.parse_args()
    stale = []
    for name, obj in build().items():
        path = OUT / name
        text = dump(obj)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            OUT.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"gravado {path.relative_to(ROOT)}")
    if stale:
        print("desatualizado (rode python scripts/payrecon_demo.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
