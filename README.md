# grafana

Dashboards e alertas do Grafana da infraestrutura (Grafana **13.3.0**), e os contratos de dados que eles consomem.

- **Dashboards:** **Dashboard Schema V2** (`dashboard.grafana.app/v2`). A pasta `dashboards/` é sincronizada pelo **Git Sync**.
- **Alertas:** regras, mute timings, templates e inibição como código em `alerting/`, aplicados no servidor com `grafana-alerting`. Roteamento e contact points ficam na UI. Detalhes em [`alerting/README.md`](alerting/README.md).

## Estrutura

| Pasta | Conteúdo |
|---|---|
| `dashboards/` | Dashboards (recursos V2) e metadados de pasta. **Única pasta sincronizada pelo Git Sync** |
| `contracts/prometheus/` | Métricas, jobs e labels observados por fonte (Alloy, Prometheus, Uptime Kuma) |
| `contracts/loki/` | Streams e labels de log observados (inclui o heartbeat dos hosts, `alloy-heartbeat`) |
| `alerting/` | Regras (`rules/`), notificações (`notifications/`) e inibição (`inhibition/`). **Fora do Git Sync** |
| `contracts/alerts/` | Labels e dependências das regras (`labels.yml`), catálogo de códigos (`codes.yml`) e divisão repositório/UI das notificações (`notifications.yml`) |
| `docs/alerts/` | Documentação operacional de cada alerta (um arquivo por código; padrão em `contracts/alerts/docs.yml`) |
| `catalogo.yml` | Catálogo de monitoramento: classe de hosts, VMs, servidores e containers (ver `alerting/README.md`) |
| `deploy/` | `setup.sh` e `grafana-alerting.py`: deploy manual do alerting no servidor (ver `alerting/README.md`) |
| `scripts/contracts.py` | Leitura dos contratos e verificação de consultas PromQL/LogQL (usado pelos dois validadores) |
| `scripts/validate_dashboards.py` | Valida formato V2, compatibilidade com Git Sync, datasources, navegação (tag `infra`, links) e se as consultas usam só métricas/labels dos contratos |
| `scripts/grafana_check.py` | Sincroniza `dashboards/` num Grafana 13.3.0 real e confere o resultado |
| `scripts/validate_alerting.py` | Valida `alerting/` contra os contratos (códigos, labels, consultas, policies, inibição) |
| `scripts/alert_docs.py` | Gera o cabeçalho dos documentos de alerta e o índice; `--check` no CI |
| `scripts/alerting_build.py` | Achata `alerting/` na pasta de provisionamento do Grafana |
| `scripts/alerting_inhibition.py` | Aplica `alerting/inhibition` pela App Platform API |
| `scripts/alerting_check.py` | Confere num Grafana 13.3.0 real se `alerting/` foi carregado como está no repositório |

## Dashboards

| Dashboard | UID | Arquivo | Pasta no Grafana | Job Prometheus | Logs (Loki) |
|---|---|---|---|---|---|
| Servidores Linux | `servers-linux` | `servers/linux.json` | Servidores | `integrations/unix` | `systemd-journal` |
| Servidores Windows | `servers-windows` | `servers/windows.json` | Servidores | `integrations/windows` | `windows-eventlog` |
| Hypervisors | `hypervisors` | `virtualization/hypervisor.json` | Virtualização | `integrations/hypervisor` (inventário) + `integrations/hyperv` (contadores) | `hyperv-eventlog` |
| Serviços | `servicos` | `services/servicos.json` | Serviços | `uptime-kuma` | `docker` (container do Kuma) |
| Containers | `containers` | `containers/containers.json` | Containers | `integrations/docker` (cAdvisor) + `integrations/unix` (RAM do servidor) | `docker` |
| Visão geral | `overview` | `overview/overview.json` | Visão geral | `GRAFANA_ALERTS` (histórico de alertas) + jobs acima | — |

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

Não há mais `__inputs`/`__requires` nem `${DS_PROMETHEUS}`/`${DS_LOKI}`: o dashboard não é importado manualmente, chega pelo Git Sync. Se um UID mudar no Grafana, atualize `DATASOURCES` em `scripts/contracts.py` e os dashboards e regras de alerta juntos.

## Convenções

- **Navegação:** todo dashboard tem a tag `infra`, o link **Visão geral** e o menu **Painéis** (link do tipo `dashboards`, tag `infra`, em dropdown). Dashboard novo com a tag entra sozinho no menu dos outros. O CI cobra os três.
- **Página inicial:** a Visão geral (`overview`). Configure uma vez em *Administração → Geral → Preferências padrão → Painel inicial* (ou o botão Home e o logo levam para a tela padrão do Grafana).
- **Blocos de número:** status em stat de **fundo sólido**, nome em cima e valor embaixo (verde `#37872D`, laranja `#E0752D`, vermelho `#C4162A`). Informativos (sistema, ligado há, núcleos) em cinza neutro `#2F333A`. Blocos de área do Visão geral em **Canvas** nativo: valor/total, chips de críticos e avisos, borda na cor do pior alerta.
- Uma variável de seleção: **Server** nos servidores, **Host** nos hypervisors (`instance`). Job e datasource ficam fixos no JSON.
- **Containers:** filtros Servidor, Projeto (compose) e Container, mais a busca nos logs. A lista mostra os que sumiram primeiro (vistos em 24 h e parados agora); servidor que parou de reportar por inteiro (VM desligada) não conta como container sumido. Mem. % é sobre o limite do container ou, sem limite, sobre a RAM do servidor. Cada servidor Linux tem uma linha **Containers** com o resumo dele.
- Containers marcados `ignorar` no `catalogo.yml` ficam fora de todos os dashboards. O filtro fica escrito no JSON logo após o job (`{job="integrations/docker",name!~"|..."}` e `{job="docker",container!~"|..."}`); o workflow `catalogo` regrava e faz o commit quando o catálogo muda.
- Serviços filtra pelas tags do Uptime Kuma (**Criticidade**, **Exposição**, **Grupo**). Tag nova no Kuma exige reiniciar o container (ver contrato `uptime-kuma`).
- O Serviços ignora os monitores `push` (heartbeat dos hosts, que ficam no Hyper-V) e o grupo "Disponibilidade servidores": `monitor_type!~"group|push"`. Resumo e grupos em stat de fundo sólido; disponibilidade por hora em barras (status history).
- Certificados de terceiros (Tiflux, Sispcard) ficam fora de todos os painéis e dos alertas SVC-201/202/203: `monitor_name!~"Tiflux|Sispcard"`.
- Toda consulta fixa o `job`. Toda métrica (Prometheus) e label (Loki) usada precisa existir no contrato desse job — o CI falha caso contrário.
- Os dashboards seguem o mesmo layout: **Resumo** (medidores + status) → **CPU / Memória / Disco** → **Rede** (placa principal) → **Logs** → seções de detalhe recolhidas.
- Bytes em base 1024 (GiB, MiB/s) nos dois dashboards; rede em bits/s.
- Títulos em português; termos técnicos (Load, Commit, IOPS, Swap, PSI...) ficam em inglês.

## Git Sync

Configuração da conexão do Git Sync no Grafana (tela de provisionamento / recurso `Repository` de `provisioning.grafana.app`):

| Campo | Valor |
|---|---|
| Repositório | `DEV-ASSESSORIA-TECH/grafana` |
| Branch | `main` |
| Path | `dashboards` (sincroniza **apenas** essa pasta) |
| Sync target | `folder` (uma pasta raiz no Grafana para este repositório) |

O Grafana lê os recursos V2 como estão no Git: o diretório define a pasta, o `_folder.json` o UID/título dela e o `metadata.name` o UID do dashboard. Arquivos fora de `dashboards/` (alertas, contratos, scripts, README) não são lidos pelo Git Sync. Os alertas chegam ao Grafana pelo provisionamento por arquivo (ver `alerting/README.md`).

**Primeira ativação (migração dos dashboards importados):** o Git Sync não assume dashboards que já existem na instância sem gerenciamento. Se `servers-linux`, `servers-windows`, `servicos` ou `hypervisors` foram importados manualmente, o sync termina em `warning` com `already exists and is not managed`. Antes de conectar o repositório, faça backup e exclua esses quatro dashboards; o sync os recria com os mesmos UIDs (URLs `/d/<uid>` continuam valendo). O histórico de versões do Grafana dos dashboards antigos não é transferido; o histórico passa a ser o do Git.

Fluxo de alteração:

1. Edite o dashboard no Grafana e salve pelo Git Sync (gera branch/PR), **ou** edite o JSON no repositório.
2. Se o JSON foi exportado manualmente da UI, exporte no formato de recurso V2 (não o classic/v1, nem "para outra instância", que reintroduz `__inputs`) e mantenha só `apiVersion`, `kind`, `metadata.name` e `spec`.
3. Rode as validações abaixo e abra o PR para `main`. Após o merge o Git Sync aplica a mudança.

Dashboard novo: crie o arquivo no diretório da pasta certa com um UID novo (até 40 caracteres, `[a-zA-Z0-9_-]`); diretório novo exige `_folder.json`. Inclua a tag `infra` e os links **Visão geral** e **Painéis** (copie de um dashboard existente).

## Validar

```bash
pip install pyyaml
python scripts/validate_dashboards.py
```

```bash
python scripts/validate_alerting.py
```

Teste de sincronização num Grafana 13.3.0 real (mesmo motor de leitura do Git Sync, com repositório `local`). Na 13.3.0 o Git Sync não precisa de feature toggle:

```bash
docker run -d --name grafana -p 3000:3000 \
  -v "$PWD/dashboards:/repo/dashboards:ro" \
  -e GF_PATHS_PERMITTED_PROVISIONING_PATHS=/repo \
  grafana/grafana:13.3.0
```

```bash
python scripts/grafana_check.py --url http://localhost:3000 --path /repo/dashboards
```

No GitHub Actions há dois workflows, disparados pelos caminhos alterados: `dashboards.yml` (validação + sync num Grafana real) e `alerting.yml` (validação + carga do provisionamento, da inibição e conferência num Grafana real).
