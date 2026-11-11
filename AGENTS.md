# AGENTS.md

Grafana dashboards, alert rules and the data contracts they consume. Docs and file contents are in Portuguese (ASCII in YAML); keep that.

## Layout

- `dashboards/` — Grafana V2 resources, synced by Git Sync.
- `alerting/` — rules, notifications, inhibition (file provisioning; see `alerting/README.md`).
- `contracts/` — what data exists and how to consume it (Prometheus, Loki, alert labels/codes).
- `catalogo.yml` — the only place that names and classifies hosts, VMs, servers and containers.
- `docs/alerts/` — one runbook per alert code.

## Contracts must stay neutral

A contract describes format and consumption rules: metrics, labels, keys, units, recipes. It never describes the state of the environment.

- No real host names. Use `srv-exemplo` / `SRV-EXEMPLO` in examples.
- No rollout status ("ainda", "hoje", "pendente", "de teste", "script antigo"). Deployment notes go to `alerting/README.md` or the commit message.
- No snapshot data: host lists, counts, per-metric coverage, dated value lists.
- A missing series is not a diagnosis. Do not infer that a host lacks Alloy or is outdated from an export.
- `status` is `observed_baseline` or `defined`.

`scripts/validate_contracts.py` enforces this in CI.

## Conventions

- Every query pins `job`; every metric/label used must exist in that job's contract.
- New alert: add the code to `contracts/alerts/codes.yml`, the rule in `alerting/rules/`, and `docs/alerts/<CODE>.md` (`python scripts/alert_docs.py --write`).
- Uptime Kuma tag values are lowercase ASCII, masculine (`criticidade=critico`).

## Before pushing

```bash
pip install pyyaml
python scripts/validate_contracts.py
python scripts/catalogo.py
python scripts/validate_alerting.py
python scripts/alert_docs.py --check
python scripts/validate_dashboards.py
```
