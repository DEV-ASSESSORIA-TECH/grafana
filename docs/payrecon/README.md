# PayRecon — dashboards de demonstração

> **DEMONSTRAÇÃO — DADOS SIMULADOS.** Todos os valores, datas, bandeiras e ocorrências são fictícios. Não há integração com Cielo nem Sicredi.

Prévia comercial do PayRecon para um posto de combustível fictício, com maquininhas Cielo e conta Sicredi. São três dashboards na pasta **PayRecon (demonstração)** do Grafana, com navegação própria (menu **PayRecon**, tag `payrecon`). Eles não entram no menu **Painéis** da infraestrutura.

| Dashboard | UID | Período padrão | Fonte (view do contrato) |
|---|---|---|---|
| PayRecon — Visão Geral | `payrecon-visao-geral` | 18/10 a 16/11/2026 (30 dias de vendas) | `vw_movimento_diario` |
| PayRecon — Agenda de Recebimentos | `payrecon-agenda` | 17/11 a 16/12/2026 (próximos 30 dias) | `vw_agenda_recebimentos` |
| PayRecon — Conciliação Financeira | `payrecon-conciliacao` | 18/10 a 17/11/2026 | `vw_conciliacao` |

Data-base da simulação: **17/11/2026, 08h00**. As classificações relativas ao tempo (a receber, aguardando, vencido) partem dela.

## Cenário simulado

- 60 dias de vendas (18/09 a 16/11/2026). A Visão Geral mostra os últimos 30. Os 30 anteriores existem para que o crédito D+30 do período exibido já tenha sido liquidado.
- Cerca de R$ 54 mil por dia, com mais movimento na sexta, no sábado, em dias de pagamento e em véspera de feriado. Mix aproximado: Pix 30%, débito 31%, crédito 39%.
- Bandeiras: Visa, Mastercard e Elo no débito e no crédito; Hipercard e American Express só no crédito. Pix não tem bandeira (`sem_bandeira`).
- Taxas (MDR) fictícias: Pix 0,75%, débito 1,09% a 1,39% e crédito 2,19% a 2,99%.
- Liquidação: Pix D+0, débito D+1 dia útil, crédito à vista D+30 corridos, passando para o próximo dia útil quando cai em fim de semana ou feriado. Os feriados de 02/11, 15/11 e 20/11 aparecem na agenda: o crédito de 21 a 23/10 cai todo em 23/11.
- Ocorrências da conciliação (o restante concilia exato):

| Data | Lançamento | Situação | O que demonstra |
|---|---|---|---|
| 28/10 | Débito Elo, −R$ 186,50 | Conciliado com ajuste | Cancelamento informado pela Cielo |
| 03/11 | Crédito Visa, −R$ 238,00 | Conciliado com ajuste | Aluguel de maquininhas descontado |
| 10/11 | Crédito Mastercard, −R$ 312,40 | Conciliado com ajuste | Chargeback |
| 29/10 | Débito Visa, +R$ 45,90 | Em análise | Recebido a maior, sem ajuste |
| 06/11 | Crédito Elo, −R$ 87,35 | Em análise | Recebido a menor, sem ajuste. Não é presumido como dívida da Cielo |
| 13/11 | Crédito Amex | Não localizado | Vencido e sem crédito identificado no Sicredi |
| 17/11 | Débito de 16/11 e crédito de 18/10 | Aguardando crédito | Vence na data-base, então ainda não é atraso |
| depois de 17/11 | — | A receber | Previsão: nunca entra como atraso nem como divergência |

Os três dashboards saem do mesmo gerador. Por isso os números batem entre si. Por exemplo, na Conciliação, *Previsto pela Cielo* − *Recebido no Sicredi* é igual a *Pendente* mais a soma das diferenças em análise.

## Como os dados chegam ao painel

Cada dashboard tem **uma** consulta de dados: o primeiro bloco do Resumo usa TestData (`csv_content`) com o CSV embutido. Todos os outros painéis reaproveitam esse resultado pelo datasource `-- Dashboard --` e aplicam transformações. As transformações fazem três coisas:

1. converter as colunas `data_*` para tempo;
2. filtrar pelo período do seletor (`${__from}`/`${__to}`) e pelas variáveis Modalidade, Bandeira e Situação;
3. agrupar e somar.

O cabeçalho do CSV é exatamente a lista de colunas da view correspondente em [`contracts/postgres/payrecon.yml`](../../contracts/postgres/payrecon.yml); `validate_dashboards.py` confere. Para a integração real, basta trocar essa única consulta por um `SELECT` na view do PostgreSQL (há uma receita no contrato). Os demais painéis continuam iguais.

Os blocos *Próximos 7/30 dias* da Agenda usam `dias_para_liquidacao` e por isso não dependem do seletor de período.

### Formatação brasileira

- Valores em reais usam a unidade `currency:financial:R$ `, que formata pelo idioma do navegador: `R$ 1.641.377,73` em navegador pt-BR. Percentuais usam `currency:financial:%:suffix`, que mostra `1,50%`.
- Datas de tabela usam `DD/MM/YYYY`, e o eixo dos gráficos diários usa `DD/MM`.
- Fuso dos dashboards: `America/Sao_Paulo`.
- **Para a apresentação, use um navegador em português (Brasil).** Em navegador em inglês, os valores aparecem como `R$ 1,641,377.73`.

## Aplicar no Grafana (TestData)

Os dashboards chegam pelo Git Sync depois do merge, como os demais. Para mostrar dados, eles precisam de um datasource **TestData** com UID **`testdata`**. O plugin já vem no Grafana; só falta criar o datasource. Escolha uma das opções:

**Provisionamento (recomendado)**: crie `provisioning/datasources/testdata.yaml` no servidor e reinicie o Grafana:

```yaml
apiVersion: 1
datasources:
  - name: TestData (PayRecon demo)
    type: grafana-testdata-datasource
    uid: testdata
    access: proxy
    isDefault: false
    editable: false
```

**Pela interface**: *Connections → Data sources → Add data source → TestData*. Salve e confira o UID no endereço da página (`/connections/datasources/edit/<uid>`). Se ele não for `testdata`, recrie pela API com `"uid": "testdata"`, porque a interface não permite editar o UID.

Ao remover a demonstração, apague `dashboards/payrecon/` e o datasource.

## Alterar a demonstração

Os JSON em `dashboards/payrecon/` são gerados. Edite `scripts/payrecon_demo.py` (cenário, cores, painéis) e rode:

```bash
python scripts/payrecon_demo.py
```

```bash
python scripts/payrecon_demo.py --check
```

O `--check` roda no CI e falha se o JSON estiver diferente do gerador.
