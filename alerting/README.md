# Alerting como código

Regras, notificações e inibição do Grafana Alerting (Grafana 13.3.0). O Git é a fonte da verdade. Os contact points são a exceção: ficam na UI para os destinatários poderem mudar sem PR.

| Pasta | Conteúdo | Como chega ao Grafana | Editável na UI |
|---|---|---|---|
| `rules/` | Grupos e regras de alerta, um arquivo por área (`hypervisor.yaml`) | Provisionamento por arquivo | Não |
| `notifications/` | Árvore de policies, mute timings, templates | Provisionamento por arquivo | Não |
| `inhibition/` | Regras de inibição (`InhibitionRule`) | App Platform API (`scripts/alerting_inhibition.py`) | Não existe UI |
| — | Contact points (`email-infra`) | Criados na UI | Sim |

Contratos em `contracts/alerts/`:

- `labels.yml`: labels obrigatórios (`alert_code`, `severity`, `area`, `scope`), chaves de dependência (`dep_hypervisor`, `dep_vm`) e chave canônica.
- `codes.yml`: catálogo de códigos (código → UID, título, severidade, scope, runbook).
- `notifications.yml`: nomes dos contact points e quem gerencia cada um.

## Padrão das regras

- **Título:** `[HYP-101] Host Hyper-V sem métricas`. O código é permanente; o UID da regra também nunca muda.
- **Labels:**

  ```yaml
  labels:
    alert_code: HYP-111
    severity: critical        # critical | warning | info
    area: hypervisor          # domínio do alvo (define o prefixo do código)
    scope: vm                 # hypervisor | vm | guest | service | collection
    dep_hypervisor: '{{ with $$labels.instance }}{{ . | reReplaceAll "-" "_" | toLower }}{{ end }}'
    dep_vm: '{{ with $$labels.vm }}{{ . | reReplaceAll "-" "_" | toLower }}{{ end }}'
  ```

- **Annotations:** `summary`, `description` e, quando o procedimento existir no Outline, `runbook_url` (igual ao `runbook` do catálogo). O painel vinculado vem de `__dashboardUid__` e `__panelId__` (texto).
- **Faixas de código:** 1xx disponibilidade, 2xx capacidade, 3xx saúde da coleta, 4xx desempenho.

### Armadilhas do provisionamento (verificadas no 13.3.0)

| Situação | Efeito | Regra |
|---|---|---|
| `$labels` num **label** | O provisionamento interpola variáveis de ambiente: vira `{{ .x }}` e quebra | Em labels escrever `$$labels`. Em annotations e consultas, `$labels` (não são interpoladas) |
| Template de label com label ausente (`DatasourceNoData`) | Erro de template | Envolver em `{{ with $$labels.x }}...{{ end }}` |
| Campos `dashboardUid`/`panelId` na regra | Ignorados sem aviso | Usar as annotations `__dashboardUid__`/`__panelId__` |
| Subpastas em `provisioning/alerting/` | Ignoradas (só aviso no log) | Por isso o build achata os arquivos |
| Contact point com `receivers: []` | Ignorado sem aviso | Não declarar contact point vazio |
| Policy com contact point inexistente | **O Grafana não sobe** (no restart) | Criar `email-infra` na UI antes da primeira aplicação |
| Arquivo de provisionamento sem permissão de leitura | **O Grafana não sobe** | O build grava com `0644` |
| Inibição: label ausente e vazio são iguais no `equal` | Um `DatasourceNoData` (chave vazia) inibiria todo alvo sem chave | Exigir `dep_* =~ .+` nos dois lados (o validador cobra) |

## Inibição

A chave de dependência é a mesma em métricas, alertas e tags do Uptime Kuma: minúsculas, `-` vira `_` (`SRV-WEB` → `srv_web`, `srv-host01` → `srv_host01`). O Kuma remove `-` dos valores de tag, e a inibição compara por igualdade exata.

| Regra (`inhibition/hypervisor.yaml`) | Origem | Inibe | `equal` |
|---|---|---|---|
| `hyp-host-indisponivel` | `HYP-101`, `HYP-102` (host sem métricas / sem heartbeat) | `scope` vm, guest, service, collection | `dep_hypervisor` |
| `hyp-vm-indisponivel` | `HYP-110`–`HYP-113` (VM fora ou em estado crítico) | `scope` guest, service | `dep_vm` |

Com o host fora, as métricas das VMs somem e os alertas de VM não disparam. Por isso convidados e serviços também levam `dep_hypervisor`, e só o alerta do host os inibe. Para um serviço do Uptime Kuma ser inibido, o monitor precisa das tags `vm` e `hypervisor` já na chave canônica (tag nova exige reiniciar o Kuma).

Alertas inibidos continuam visíveis no Grafana (estado `suppressed`); só a notificação deixa de sair. A API é `v1beta1` e pode mudar.

## Notificações

- `critical` e `warning` vão para `email-infra` com tempos diferentes. `info` usa o mute timing `sempre`: o alerta aparece no Grafana e nunca notifica.
- Agrupamento: `area` + `dep_hypervisor` (a queda de um host vira um e-mail só).
- Template `infra`: no contact point (UI), assunto `{{ template "infra.subject" . }}` e mensagem `{{ template "infra.message" . }}`.
- Manutenção pontual: silence na UI (não vai para o Git).

## Aplicar (manual)

Pré-requisitos, uma vez só: o contact point `email-infra` criado na UI e um token de service account com papel Admin.

1. Valide:

   ```bash
   python scripts/validate_alerting.py
   ```

2. Copie para a pasta de provisionamento (achata as subpastas e adiciona o prefixo):

   ```bash
   python scripts/alerting_build.py --out /opt/observability/grafana/provisioning/alerting
   ```

3. Recarregue sem reiniciar. Com erro, o reload falha mas o Grafana continua de pé; num restart, o Grafana não subiria:

   ```bash
   curl -X POST -H "Authorization: Bearer $GRAFANA_TOKEN" "$GRAFANA_URL/api/admin/provisioning/alerting/reload"
   ```

4. Inibição: mostre a diferença e depois aplique:

   ```bash
   python scripts/alerting_inhibition.py
   ```

   ```bash
   python scripts/alerting_inhibition.py --apply
   ```

`GRAFANA_URL` e `GRAFANA_TOKEN` vêm do ambiente. `--prune` apaga do Grafana as regras de inibição que não estão no repositório.

### Remover uma regra

Tirar a regra do arquivo **não** a apaga do Grafana. Mova o código para `status: retired` no catálogo e declare a exclusão num arquivo de `rules/` até ela sumir de todos os ambientes:

```yaml
apiVersion: 1
deleteRules:
  - orgId: 1
    uid: <uid>
```

### Primeira aplicação (migração das regras criadas na UI)

As regras atuais foram criadas na UI. O provisionamento as assume pelo UID (mesmas consultas, limiares, `for` e estados) e elas passam a ser somente leitura na UI. Efeitos esperados:

- Título e labels mudam a identidade do alerta: o que estiver disparando resolve e dispara de novo uma vez.
- Silences que filtram pelo título antigo (`alertname`) deixam de valer.
- A árvore de policies é substituída inteira.

## Validar

```bash
python scripts/validate_alerting.py
```

O CI (`.github/workflows/alerting.yml`) também sobe um Grafana 13.3.0, carrega o build, aplica a inibição e confere tudo com `scripts/alerting_check.py`.
