# grafana-dashboards

Dashboards Grafana da infraestrutura e os contratos de dados que eles consomem.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dashboards/` | Dashboards em uso (JSON para importar no Grafana) |
| `contracts/prometheus/` | Métricas, jobs e labels observados por fonte (Alloy, Prometheus, Uptime Kuma) |
| `contracts/loki/` | Streams e labels de log observados |
| `references/` | Dashboards da comunidade usados como base. Não editar |
| `scripts/validate.py` | Valida JSON dos dashboards e YAML dos contratos |

## Dashboards

| Dashboard | UID | Job Prometheus | Logs (Loki) |
|---|---|---|---|
| Servidores Linux | `servers-linux` | `integrations/unix` | `systemd-journal` |
| Servidores Windows | `servers-windows` | `integrations/windows` | `windows-eventlog` |

## Convenções

- Só existe a variável **Server** (`instance`). Job e datasource ficam fixos no JSON.
- Datasources entram via `__inputs` (`DS_PROMETHEUS`, `DS_LOKI`): o Grafana pede na importação.
- Toda métrica usada num painel precisa existir no contrato correspondente.

## Validar

```bash
pip install pyyaml
python scripts/validate.py
```

O mesmo roda no GitHub Actions a cada push.
