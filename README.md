# grafana-dashboards

Dashboards Grafana da infraestrutura e os contratos de dados que eles consomem.

Os dashboards estão no **Dashboard Schema V2** (`dashboard.grafana.app/v2`, Grafana 13.0.3) e a pasta `dashboards/` é sincronizada com o Grafana pelo **Git Sync**.

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dashboards/` | Dashboards (recursos V2) e metadados de pasta. **Única pasta sincronizada pelo Git Sync** |
| `contracts/prometheus/` | Métricas, jobs e labels observados por fonte (Alloy, Prometheus, Uptime Kuma) |
| `contracts/loki/` | Streams e labels de log observados (inclui o heartbeat dos hosts, `alloy-heartbeat`) |
| `contracts/alerts/` | Labels obrigatórios das regras de alerta (`severity`, `area`) |
| `scripts/validate.py` | Valida formato V2, compatibilidade com Git Sync, datasources e se as consultas usam só métricas/labels dos contratos |
| `scripts/grafana_check.py` | Sincroniza `dashboards/` num Grafana 13.0.3 real e confere o resultado |

## Dashboards

| Dashboard | UID | Arquivo | Pasta no Grafana | Job Prometheus | Logs (Loki) |
|---|---|---|---|---|---|
| Servidores Linux | `servers-linux` | `servers/linux.json` | Servidores | `integrations/unix` | `systemd-journal` |
| Servidores Windows | `servers-windows` | `servers/windows.json` | Servidores | `integrations/windows` | `windows-eventlog` |
| Hypervisors | `hypervisors` | `virtualization/hypervisor.json` | Virtualização | `integrations/hypervisor` (inventário) + `integrations/hyperv` (contadores) | `hyperv-eventlog` |
| Serviços | `servicos` | `services/servicos.json` | Serviços | `uptime-kuma` | `docker` (container do Kuma) |

## Formato dos arquivos (Schema V2)

Cada dashboard é um recurso Kubernetes-style, um por arquivo:

```json
{
  "apiVersion": "dashboard.grafana.app/v2",
  "kind": "Dashboard",
  "metadata": { "name": "servers-linux" },
  "spec": { "title": "...", "elements": {}, "layout": {}, "variables": [], "...": "..." }
}
```

- `metadata.name` é o **UID** do dashboard. Não mudar: links, favoritos e URLs dependem dele.
- `metadata` contém **só** `name`. Nada de `namespace`, `uid`, `resourceVersion`, `generation`, `creationTimestamp`, `labels`, `annotations`, `managedFields` nem bloco `status`: são gerados pelo servidor e quebram o diff do Git.
- Painéis ficam em `spec.elements` (`panel-<id>`) e a posição em `spec.layout` (`RowsLayout` → `GridLayout`). Todo elemento precisa estar no layout, e vice-versa.
- Cada diretório dentro de `dashboards/` vira uma pasta no Grafana e precisa de um `_folder.json` com UID fixo (sem ele o Git Sync gera um UID que pode mudar a cada sync):

  ```json
  { "apiVersion": "folder.grafana.app/v1", "kind": "Folder", "metadata": { "name": "gd-servers" }, "spec": { "title": "Servidores" } }
  ```

- Dentro de `dashboards/` só entram arquivos `.json` (dashboards e `_folder.json`). Sem arquivos vazios ou placeholders.

## Datasources

Os datasources são provisionados no Grafana com UIDs fixos, e as consultas apontam direto para eles:

| Tipo (`group`) | UID (`datasource.name`) |
|---|---|
| `prometheus` | `prometheus` |
| `loki` | `loki` |

Não há mais `__inputs`/`__requires` nem `${DS_PROMETHEUS}`/`${DS_LOKI}`: o dashboard não é importado manualmente, chega pelo Git Sync. Se um UID mudar no Grafana, atualize `DATASOURCES` em `scripts/validate.py` e os dashboards juntos.

## Convenções

- Uma variável de seleção: **Server** nos servidores, **Host** nos hypervisors (`instance`). Job e datasource ficam fixos no JSON.
- Serviços filtra pelas tags do Uptime Kuma (**Criticidade**, **Exposição**, **Grupo**). Tag nova no Kuma exige reiniciar o container (ver contrato `uptime-kuma`).
- Toda consulta fixa o `job`. Toda métrica (Prometheus) e label (Loki) usada precisa existir no contrato desse job — o CI falha caso contrário.
- Os dashboards seguem o mesmo layout: **Resumo** (medidores + status) → **CPU / Memória / Disco** → **Rede** (placa principal) → **Logs** → seções de detalhe recolhidas.
- Bytes em base 1024 (GiB, MiB/s) nos dois dashboards; rede em bits/s.
- Títulos em português; termos técnicos (Load, Commit, IOPS, Swap, PSI...) ficam em inglês.

## Git Sync

Configuração da conexão do Git Sync no Grafana (tela de provisionamento / recurso `Repository` de `provisioning.grafana.app`):

| Campo | Valor |
|---|---|
| Repositório | `DEV-ASSESSORIA-TECH/grafana-dashboards` |
| Branch | `main` |
| Path | `dashboards` (sincroniza **apenas** essa pasta) |
| Sync target | `folder` (uma pasta raiz no Grafana para este repositório) |

O Grafana lê os recursos V2 como estão no Git: o diretório define a pasta, o `_folder.json` o UID/título dela e o `metadata.name` o UID do dashboard. Arquivos fora de `dashboards/` (contratos, scripts, README) não são lidos pelo Grafana.

**Primeira ativação (migração dos dashboards importados):** o Git Sync não assume dashboards que já existem na instância sem gerenciamento. Se `servers-linux`, `servers-windows`, `servicos` ou `hypervisors` foram importados manualmente, o sync termina em `warning` com `already exists and is not managed`. Antes de conectar o repositório, faça backup e exclua esses quatro dashboards; o sync os recria com os mesmos UIDs (URLs `/d/<uid>` continuam valendo). O histórico de versões do Grafana dos dashboards antigos não é transferido; o histórico passa a ser o do Git.

Fluxo de alteração:

1. Edite o dashboard no Grafana e salve pelo Git Sync (gera branch/PR), **ou** edite o JSON no repositório.
2. Se o JSON foi exportado manualmente da UI, exporte no formato de recurso V2 (não o classic/v1, nem "para outra instância", que reintroduz `__inputs`) e mantenha só `apiVersion`, `kind`, `metadata.name` e `spec`.
3. Rode as validações abaixo e abra o PR para `main`. Após o merge o Git Sync aplica a mudança.

Dashboard novo: crie o arquivo no diretório da pasta certa com um UID novo (até 40 caracteres, `[a-zA-Z0-9_-]`); diretório novo exige `_folder.json`.

## Validar

```bash
pip install pyyaml
python scripts/validate.py
```

Teste de sincronização num Grafana 13.0.3 real (mesmo motor de leitura do Git Sync, com repositório `local`):

```bash
docker run -d --name grafana -p 3000:3000 \
  -v "$PWD/dashboards:/repo/dashboards:ro" \
  -e GF_FEATURE_TOGGLES_ENABLE=provisioning,kubernetesDashboards \
  -e GF_PATHS_PERMITTED_PROVISIONING_PATHS=/repo \
  grafana/grafana:13.0.3
```

```bash
python scripts/grafana_check.py --url http://localhost:3000 --path /repo/dashboards
```

Os dois rodam no GitHub Actions a cada push.
