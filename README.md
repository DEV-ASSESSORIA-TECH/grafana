# grafana-dashboards

Dashboards Grafana da infraestrutura e os contratos de dados que eles consomem.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dashboards/` | Dashboards em uso (JSON para importar no Grafana) |
| `contracts/prometheus/` | Métricas, jobs e labels observados por fonte (Alloy, Prometheus, Uptime Kuma) |
| `contracts/loki/` | Streams e labels de log observados |
| `references/` | Dashboards da comunidade usados como base. Não editar |
| `scripts/validate.py` | Valida sintaxe e se as consultas usam só métricas/labels dos contratos |

## Dashboards

| Dashboard | UID | Job Prometheus | Logs (Loki) |
|---|---|---|---|
| Servidores Linux | `servers-linux` | `integrations/unix` | `systemd-journal` |
| Servidores Windows | `servers-windows` | `integrations/windows` | `windows-eventlog` |
| Hypervisors | `hypervisors` | `integrations/hypervisor` (inventário) + `integrations/hyperv` (contadores) | `hyperv-eventlog` |
| Serviços | `servicos` | `uptime-kuma` | `docker` (container do Kuma) |

## Convenções

- Uma variável de seleção: **Server** nos servidores, **Host** nos hypervisors (`instance`). Job e datasource ficam fixos no JSON.
- Serviços filtra pelas tags do Uptime Kuma (**Criticidade**, **Exposição**, **Grupo**). Tag nova no Kuma exige reiniciar o container (ver contrato `uptime-kuma`).
- Datasources entram via `__inputs` (`DS_PROMETHEUS`, `DS_LOKI`): o Grafana pede na importação.
- Toda consulta fixa o `job`. Toda métrica (Prometheus) e label (Loki) usada precisa existir no contrato desse job — o CI falha caso contrário.
- Os dashboards seguem o mesmo layout: **Resumo** (medidores + status) → **CPU / Memória / Disco** → **Rede** (placa principal) → **Logs** → seções de detalhe recolhidas.
- Bytes em base 1024 (GiB, MiB/s) nos dois dashboards; rede em bits/s.
- Títulos em português; termos técnicos (Load, Commit, IOPS, Swap, PSI...) ficam em inglês.

## Validar

```bash
pip install pyyaml
python scripts/validate.py
```

O mesmo roda no GitHub Actions a cada push.
