# Alertas

Documentação operacional de cada alerta: o que significa, o que verificar e como resolver.
Padrão em [`contracts/alerts/docs.yml`](../../contracts/alerts/docs.yml). Arquivo gerado por `scripts/alert_docs.py`; não edite à mão.

## Hipervisor

| Código | Alerta | Severidade |
|---|---|---|
| [HYP-101](HYP-101.md) | Host Hyper-V sem métricas | critical |
| [HYP-102](HYP-102.md) | Heartbeat do host Hyper-V ausente | critical |
| [HYP-110](HYP-110.md) | VM em estado crítico no Hyper-V | critical |
| [HYP-111](HYP-111.md) | VM crítica desligada | critical |
| [HYP-112](HYP-112.md) | VM importante desligada | warning |
| [HYP-113](HYP-113.md) | VM informativa desligada | info |
| [HYP-114](HYP-114.md) | VM ligada sem resposta do convidado | warning |
| [HYP-130](HYP-130.md) | Host Hyper-V sem sincronizar o horário | warning |
| [HYP-131](HYP-131.md) | Host Hyper-V desligado inesperadamente | critical |
| [HYP-140](HYP-140.md) | Serviço do Hyper-V parado | critical |
| [HYP-201](HYP-201.md) | Armazenamento do host elevado | warning |
| [HYP-202](HYP-202.md) | Armazenamento do host crítico | critical |
| [HYP-203](HYP-203.md) | Disco de backup do host com pouco espaço | warning |
| [HYP-204](HYP-204.md) | Disco de backup do host quase cheio | critical |
| [HYP-205](HYP-205.md) | Memória do host Hyper-V esgotando | warning |
| [HYP-206](HYP-206.md) | CPU do host Hyper-V alta | warning |
| [HYP-207](HYP-207.md) | Checkpoint antigo | info |
| [HYP-301](HYP-301.md) | Falha no inventário do Hyper-V | warning |
| [HYP-302](HYP-302.md) | Inventário do Hyper-V desatualizado | warning |
| [HYP-303](HYP-303.md) | VM ligada sem monitoramento do convidado | info |
| [HYP-401](HYP-401.md) | Disco virtual lento | warning |

## Windows

| Código | Alerta | Severidade |
|---|---|---|
| [WIN-101](WIN-101.md) | Servidor Windows crítico sem métricas | critical |
| [WIN-102](WIN-102.md) | Servidor Windows sem métricas | warning |
| [WIN-110](WIN-110.md) | Serviço do Active Directory parado | critical |
| [WIN-111](WIN-111.md) | Serviço de aplicação parado | warning |
| [WIN-112](WIN-112.md) | Serviço em curadoria parado | info |
| [WIN-120](WIN-120.md) | Servidor Windows reiniciado | info |
| [WIN-130](WIN-130.md) | Servidor Windows sem sincronizar o horário | warning |
| [WIN-201](WIN-201.md) | Disco Windows acima de 90% | warning |
| [WIN-202](WIN-202.md) | Disco Windows acima de 95% | critical |
| [WIN-203](WIN-203.md) | Memória Windows alta | warning |
| [WIN-204](WIN-204.md) | Commit Windows alto | warning |
| [WIN-205](WIN-205.md) | CPU Windows alta | warning |

## Linux

| Código | Alerta | Severidade |
|---|---|---|
| [LIN-101](LIN-101.md) | Servidor Linux crítico sem métricas | critical |
| [LIN-102](LIN-102.md) | Servidor Linux sem métricas | warning |
| [LIN-110](LIN-110.md) | Unidade systemd em falha | warning |
| [LIN-120](LIN-120.md) | Servidor Linux reiniciado | info |
| [LIN-130](LIN-130.md) | OOM killer acionado | warning |
| [LIN-201](LIN-201.md) | Filesystem Linux acima de 85% | warning |
| [LIN-202](LIN-202.md) | Filesystem Linux acima de 95% | critical |
| [LIN-203](LIN-203.md) | Filesystem Linux enche em 24 horas | warning |
| [LIN-204](LIN-204.md) | Filesystem Linux somente leitura | critical |
| [LIN-205](LIN-205.md) | Inodes Linux acima de 90% | warning |
| [LIN-206](LIN-206.md) | Memória Linux esgotando | warning |
| [LIN-207](LIN-207.md) | Swap Linux quase cheio | warning |
| [LIN-208](LIN-208.md) | CPU Linux alta | warning |
| [LIN-209](LIN-209.md) | Relógio Linux desajustado | warning |

## Containers

| Código | Alerta | Severidade |
|---|---|---|
| [CTR-101](CTR-101.md) | Container sumiu | warning |
| [CTR-102](CTR-102.md) | Container unhealthy | warning |
| [CTR-103](CTR-103.md) | Container em loop de reinício | warning |
| [CTR-104](CTR-104.md) | Container morto por falta de memória | warning |
| [CTR-301](CTR-301.md) | Coleta de containers parada | warning |

## Serviços

| Código | Alerta | Severidade |
|---|---|---|
| [SVC-101](SVC-101.md) | Serviço crítico fora do ar | critical |
| [SVC-102](SVC-102.md) | Serviço fora do ar | warning |
| [SVC-110](SVC-110.md) | Serviço externo fora do ar | warning |
| [SVC-201](SVC-201.md) | Certificado vence em 14 dias | warning |
| [SVC-202](SVC-202.md) | Certificado vence em 3 dias | critical |
| [SVC-203](SVC-203.md) | Certificado inválido | critical |
| [SVC-301](SVC-301.md) | Uptime Kuma sem métricas | critical |

## Monitoramento

| Código | Alerta | Severidade |
|---|---|---|
| [MON-101](MON-101.md) | Prometheus com configuração inválida | warning |
| [MON-102](MON-102.md) | Prometheus com falha de compactação | warning |
