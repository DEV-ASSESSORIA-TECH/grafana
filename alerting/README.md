# Alerting como código

Regras, mute timings, templates e inibição do Grafana Alerting (Grafana 13.3.0). O Git é a fonte da verdade para essas peças. **Roteamento e destinatários ficam na UI**: as regras só publicam labels, e a notification policy padrão (editada na UI) decide quem recebe.

| Pasta | Conteúdo | Como chega ao Grafana | Editável na UI |
|---|---|---|---|
| `rules/` | Grupos e regras de alerta, um arquivo por área | Provisionamento por arquivo | Não |
| `notifications/` | Mute timings e templates | Provisionamento por arquivo | Não (mas usáveis na UI) |
| `inhibition/` | Regras de inibição (`InhibitionRule`) | App Platform API | Não existe UI |
| — | Notification policies e contact points | UI | Sim |

Contratos em `contracts/alerts/`:

- `labels.yml`: labels obrigatórios (`alert_code`, `severity`, `area`, `scope`), chaves de dependência (`dep_hypervisor`, `dep_vm`), chave canônica e receitas de consulta.
- `codes.yml`: catálogo de códigos (código → UID, título, severidade, scope, runbook).
- `notifications.yml`: o que vem do repositório e o que fica na UI, e os labels disponíveis para roteamento.

## Cobertura

Todas as regras ficam na pasta INFRA do Grafana, um grupo por arquivo. O catálogo completo está em `contracts/alerts/codes.yml`.

| Arquivo | Grupo | Cobre |
|---|---|---|
| `hypervisor.yaml` | `hypervisor-1m`, `storage-1m`, `hypervisor-extra-1m` | Hosts Hyper-V (métricas, heartbeat, disco, F: de backup, memória, CPU, serviços vmms/vmcompute, horário e desligamento inesperado pelo Event Log), estado das VMs, inventário, VMs sem agente |
| `windows.yaml` | `windows-1m` | Servidores Windows convidados: sem métricas, serviços do AD, de aplicação e em curadoria, reinício, disco, memória, commit, CPU |
| `linux.yaml` | `linux-1m` | Servidores Linux: sem métricas, systemd, reinício, filesystem (uso, previsão de 24 h, somente leitura, inodes), memória, swap, CPU, relógio |
| `containers.yaml` | `containers-1m` | Containers Docker: sumiu, unhealthy, loop de reinício, OOM, coleta parada |
| `servicos.yaml` | `servicos-1m` | Uptime Kuma: serviços por criticidade, externos (15 min de tolerância), certificados, Kuma sem métricas |
| `monitoramento.yaml` | `monitoramento-5m` | Prometheus: configuração inválida, falha de compactação |

**Decisões que estão nas consultas** (mude aqui quando mudar a operação):

- **Classificação das VMs** (crítica/importante/informativa): listas de nomes em HYP-111/112/113 e, em minúsculas, em WIN-101/102 e LIN-101/102. Nesses dois, "crítica" vira `critical` e o resto `warning`. Ao reclassificar uma VM, atualize as quatro listas.
- **Volume F: do srv-host01 (backups):** fora de HYP-201/202; tem limite próprio em GiB livres (HYP-203 < 200, HYP-204 < 100).
- **Serviços Windows:** WIN-110 (AD, critical), WIN-111 (aplicação, warning) e WIN-112 (em curadoria, info) têm listas fixas por servidor. Serviço automático parado fora dessas listas não alerta.
- **Excluídos de propósito:** unidades `fwupd*` (LIN-110), o container `grafana-alerting-temp` do deploy (CTR) e a memória total dos hosts Hyper-V (em host, use HYP-205).
- **"Sem métricas" e "container sumiu"** comparam com o que existia nos últimos 7 dias e 24 horas: servidor ou container desativado de propósito para de alertar depois desse prazo (ou silencie antes).

## Padrão das regras

- **Título:** `[HYP-101] Host Hyper-V sem métricas`. O código é permanente; o UID da regra também nunca muda.
- **Labels:**

  ```yaml
  labels:
    alert_code: HYP-111
    severity: critical        # critical | warning | info
    area: hypervisor          # domínio do alvo (define o prefixo do código)
    scope: vm                 # hypervisor | vm | guest | service | external | collection
    dep_hypervisor: '{{ with $$labels.instance }}{{ . | toLower | reReplaceAll "[^a-z0-9_]" "_" }}{{ end }}'
    dep_vm: '{{ with $$labels.vm }}{{ . | toLower | reReplaceAll "[^a-z0-9_]" "_" }}{{ end }}'
  ```

- **Annotations:** `summary`, `description` e, quando o procedimento existir no Outline, `runbook_url` (igual ao `runbook` do catálogo). O painel vinculado vem de `__dashboardUid__` e `__panelId__` (texto).
- **Destino:** nunca `notification_settings` (envio direto a um contact point). O destino sai da policy.
- **Faixas de código:** 1xx disponibilidade, 2xx capacidade, 3xx saúde da coleta, 4xx desempenho.

### Armadilhas do provisionamento (verificadas no 13.3.0)

| Situação | Efeito | Regra |
|---|---|---|
| `$labels` num **label** | O provisionamento interpola variáveis de ambiente: vira `{{ .x }}` e quebra | Em labels escrever `$$labels`. Em annotations e consultas, `$labels` (não são interpoladas) |
| Template de label com label ausente (`DatasourceNoData`) | Erro de template | Envolver em `{{ with $$labels.x }}...{{ end }}` |
| Campos `dashboardUid`/`panelId` na regra | Ignorados sem aviso | Usar as annotations `__dashboardUid__`/`__panelId__` |
| Subpastas em `provisioning/alerting/` | Ignoradas (só aviso no log) | O deploy achata os arquivos |
| Arquivo inválido ou ilegível na pasta de provisionamento | **O Grafana não sobe** | O deploy ensaia antes e restaura sozinho se a produção não subir |
| Reload pela API (`/api/admin/provisioning/alerting/reload`) | Exige admin do servidor; token de service account recebe 403 | O deploy reinicia o container |
| Inibição: label ausente e vazio são iguais no `equal` | Um `DatasourceNoData` (chave vazia) inibiria todo alvo sem chave | Exigir `dep_* =~ .+` nos dois lados (o validador cobra) |

## Dependências e inibição

**Regra de ouro:** só existe dependência quando o alvo depende 100% do outro. Host → VM → serviço que roda inteiro naquela VM. Serviço em mais de uma VM (balanceado, failover) e serviço de terceiros não recebem chave.

A chave canônica é a mesma em métricas, alertas e tags do Uptime Kuma: minúsculas e todo caractere fora de `[a-z0-9_]` vira `_` (`SRV-WEB` → `srv_web`). O Kuma remove `-` dos valores de tag, e a inibição compara por igualdade exata. O inventário publica `vm_key` com essa chave.

| Regra (`inhibition/hypervisor.yaml`) | Origem | Inibe | `equal` |
|---|---|---|---|
| `hyp-host-indisponivel` | `HYP-101`, `HYP-102` (host sem métricas / sem heartbeat) | `scope` vm, guest, service, collection | `dep_hypervisor` |
| `hyp-vm-indisponivel` | `HYP-110`–`HYP-113` (VM fora ou em estado crítico) | `scope` guest, service | `dep_vm` |

Com o host fora, as métricas das VMs somem e os alertas de VM não disparam. Por isso quem tem `dep_vm` tem também `dep_hypervisor`, e o alerta do host inibe toda a cadeia. `scope: external` nunca é inibido.

| Uptime Kuma | Tag | Scope |
|---|---|---|
| Push `heartbeat-<host>` do hipervisor | nenhuma (a chave sai do nome) | `hypervisor`, é origem |
| Push `heartbeat-<host>` de VM (opcional) | nenhuma (o nome casa com `guest_host` do inventário) | `guest` |
| Serviço que roda inteiro numa VM | `vm:<chave>` (ex. `vm:srv_web`); o hipervisor vem do inventário | `service` |
| Serviço em mais de uma VM | nenhuma | `service`, sem chave |
| `grupo=externo` | nenhuma | `external` |

Tag nova no Kuma exige reiniciar o container dele. As consultas prontas estão em `contracts/alerts/labels.yml` (`recipes`).

Alertas inibidos continuam visíveis no Grafana (estado `suppressed`); só a notificação deixa de sair. A API é `v1beta1` e pode mudar.

## Notificações (na UI)

Monte a policy padrão pelos labels (`contracts/alerts/notifications.yml`, `routing_labels`). Recomendado:

- Rota `severity = info` com o mute timing **`sempre`** (vem do repositório): o alerta aparece no Grafana e nunca notifica.
- Agrupamento por `area` e `dep_hypervisor`: a queda de um host vira uma notificação só.
- Template `infra` (vem do repositório): no contact point, assunto `{{ template "infra.subject" . }}` e mensagem `{{ template "infra.message" . }}`.
- Manutenção pontual: silence na UI.

## Deploy (manual, no servidor)

A ferramenta `grafana-alerting` (`deploy/`) mantém tudo em **`/opt/grafana-alerting`** e não toca em mais nada de `/opt`:

| Caminho | Conteúdo |
|---|---|
| `repo/` | Clone local (só a branch, sem histórico de blobs). Cada execução faz `git fetch` e a ferramenta se atualiza sozinha |
| `live/` | Arquivos achatados que o Grafana lê, montados em `/etc/grafana/provisioning/alerting` |
| `packages/` | Pacotes `.tar.gz` de cada deploy e backups de `live/` (últimos 20 de cada) |
| `config.json`, `token`, `state.json` | URL, container e branch; token da service account (`0600`); o que está aplicado |

### Instalar (uma vez)

```bash
(
  set -e
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT
  curl -fsSL https://raw.githubusercontent.com/DEV-ASSESSORIA-TECH/grafana/main/deploy/setup.sh -o "$dir/setup.sh"
  sudo bash "$dir/setup.sh"
)
```

O setup instala `git`, `python3` e `python3-yaml` se faltarem, clona o repositório, instala o comando `/usr/local/sbin/grafana-alerting`, pergunta a URL do Grafana (padrão `https://mon.assessoriatech.com/grafana`) e o token. O token é de uma service account com papel **Admin**, criada em *Administration → Users and access → Service accounts*. O container de produção é encontrado pelas labels do compose (`observability`/`grafana`).

**Mount (você faz, uma vez):** o Grafana precisa ler `/opt/grafana-alerting/live`. O mount de provisioning atual é somente leitura, então o Docker não cria o ponto de montagem sozinho:

1. Ponto de montagem vazio:

   ```bash
   sudo mkdir /opt/observability/grafana/provisioning/alerting
   ```

2. No `compose.yaml`, serviço `grafana`, em `volumes`, depois do mount de provisioning:

   ```yaml
   - /opt/grafana-alerting/live:/etc/grafana/provisioning/alerting:ro
   ```

3. Recrie o container:

   ```bash
   cd /opt/observability && sudo docker compose up -d grafana
   ```

`sudo grafana-alerting setup` confere o mount e o token a qualquer momento. Enquanto o mount não existir, o deploy recusa aplicar.

### Usar

Sem argumentos, `sudo grafana-alerting` abre um menu. Comandos diretos:

| Comando | O que faz |
|---|---|
| `status` | O que está aplicado, o que mudou na branch desde então, container, mount e token |
| `deploy [--ref REF]` | Fetch → validação → pacote → **ensaio** → backup → aplicação → inibição → conferência |
| `deploy --skip-test` | Igual, **sem o ensaio** (aplicação direta) |
| `test [--ref REF]` | Só valida, empacota e ensaia; não toca na produção |
| `package [--ref REF]` | Só valida e gera o pacote |
| `apply PACOTE [--skip-test]` | Aplica um pacote existente (também serve para outro servidor) |
| `rollback` | Aplica de novo o pacote do deploy anterior |
| `list` | Pacotes e backups |
| `cleanup` | Remove o container e a rede temporários, se sobrarem |

`--yes` pula a confirmação. `--no-update` não atualiza a ferramenta antes de executar. `--ref` aceita branch, tag ou commit.

**Ensaio:** sobe um container `grafana-alerting-temp` com a **mesma imagem da produção**, numa rede própria `grafana-alerting-temp` (fora das redes do compose). Carrega o pacote, aplica a inibição, confere tudo com `scripts/alerting_check.py` e remove o container e a rede no fim, mesmo se falhar.

**Aplicação:**
1. Guarda `live/` em `packages/live-backup-*.tar.gz` e copia os arquivos novos.
2. Reinicia o container de produção, **só se** os arquivos mudaram.
3. Aplica a inibição pela API com `--prune`: o repositório é a fonte de todas as regras de inibição.
4. Confere a produção.

Se a produção não subir (o loop de crash do `restart: unless-stopped` é detectado em segundos) ou a conferência falhar, a ferramenta restaura o backup, reinicia de novo e reaplica a inibição do pacote anterior.

### Primeira aplicação (regras criadas na UI)

O provisionamento assume as regras atuais pelo UID: mesma pasta INFRA, mesmos grupos, consultas, limiares, `for` e estados. Elas passam a ser somente leitura na UI. Policies e contact points não são tocados. Efeitos esperados:

- Título e labels mudam a identidade do alerta: o que estiver disparando resolve e dispara de novo uma vez.
- Silences que filtram pelo título antigo (`alertname`) deixam de valer.

### Remover uma regra

Tirar a regra do arquivo **não** a apaga do Grafana. Mova o código para `status: retired` no catálogo e declare a exclusão num arquivo de `rules/` até ela sumir de todos os ambientes:

```yaml
apiVersion: 1
deleteRules:
  - orgId: 1
    uid: <uid>
```

## Validar

```bash
python scripts/validate_alerting.py
```

O CI (`.github/workflows/alerting.yml`) também sobe um Grafana 13.3.0, carrega o build, aplica a inibição e confere tudo com `scripts/alerting_check.py`.
