# gabcmt-dados

Dados internos do Gabinete do Des. Fed. Carlos Muta (Turma Regional de MS, TRF3). **Repositório privado.** Os arquivos contêm nomes de servidores e números de processos; não torne este repositório público nem copie os arquivos para o `gabcmt`, que é público.

## Estrutura

```
produtividade/registros.json   registros de produtividade de todos os servidores
```

## produtividade/registros.json

O arquivo é gravado pelo app (botão **Git**, na barra da lista de processos do Controle de Pauta). Cada envio vira um commit e é **mesclado por `id`**: registros novos entram, os que mudaram no app são atualizados e nada é apagado. Os critérios são os mesmos do histórico de produção do perfil e do Excel por servidor.

| Campo | Conteúdo |
|---|---|
| `atualizadoEm`, `enviadoPor` | Data/hora do último envio e quem estava conectado no app |
| `tipos` | Códigos dos tipos de registro e seus rótulos |
| `totais` | Quantidade de registros e de servidores |
| `resumoPorServidor` | Por servidor: total e quantidade por tipo |
| `registros[]` | Um item por registro (abaixo) |

Campos de cada registro:

| Campo | Conteúdo |
|---|---|
| `id` | Identificador estável (servidor, tipo, data, processo, sessão e item) |
| `servidor`, `servidorId` | Nome e id do servidor no cadastro da equipe |
| `data` | Data do trabalho (AAAA-MM-DD) |
| `tipo`, `tipoRotulo` | `minuta`, `mono`, `despacho`, `diligencia`, `pendencia`, `complementar`, `correcao` ou `revisao` |
| `processo`, `sessao`, `item`, `classe`, `assunto` | Dados do processo, quando houver |
| `natureza` | Tipo da decisão, etapa da correção ou observação do lançamento |
| `quantidade` | 1, salvo na produção complementar |
| `primeiroEnvio`, `ultimaColeta` | Quando o registro entrou no arquivo e quando foi visto pela última vez |

## Acesso do app

O botão usa um token *fine-grained* do GitHub com acesso só a este repositório e a permissão **Contents: Read and write**. O token fica salvo apenas no navegador de quem configurou.
