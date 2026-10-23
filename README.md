# gabcmt-dados

Dados internos do Gabinete do Des. Fed. Carlos Muta (Turma Regional de MS, TRF3). **Repositório privado.** Os arquivos contêm nomes de servidores e números de processos; não torne este repositório público nem copie os arquivos para o `gabcmt`, que é público.

## Estrutura

```
acervo/                        processos que estão no gabinete hoje (padrão 1.0)
  importado.json               dados dos sistemas (PJe 2G, BI Gab A, BI Sigilosos), um campo por informação
  operacional.json             só o que o gabinete decide: triagem, minuta, revisão, fluxo, pauta
  correspondencias.json        de-para de cada coluna antiga, regras calculadas e valores-padrão
trabalho.json                  configuração do Painel, servidores, sessões e calendário
produtividade/registros.json   registros de produtividade de todos os servidores
scripts/migrar_acervo.py       migração única de gabcmt/banco/acervo.json para acervo/
```

## Acervo (padrão 1.0)

O acervo é dividido em três camadas, ligadas pelo `id` (`acv-<20 dígitos do processo>`):

- **Importado** (`acervo/importado.json`): o que vem dos sistemas. As colunas repetidas entre PJe e BI viram um campo só (ex.: `classe_judicial` e `Classe` → `classe`); vale a primeira fonte com valor, na ordem de `correspondencias.json`, e o que diverge de verdade fica em `divergencias`. Cada processo diz de quais sistemas veio (`fontes`).
- **Regras** (não gravadas): seção competente, META, impedimento e suspeição, AJG, DPU, regularidade, etiquetas operacionais, ano, mês e status de julgamento são recalculados pelo Painel a cada abertura.
- **Operacional** (`acervo/operacional.json`): só processos com algo efetivamente decidido. Valores iniciais ("Não Verificado", "Não se Aplica"…) não são gravados. Blocos: `triagem`, `minuta`, `revisao`, `fluxo` (responsável e, em cada etapa — minutado, refinado, conferido, corrigido, liberado —, quem e quando; a produtividade sai dessas datas, sem lançamento separado), `pauta` (`sessaoInaugural`, gravada uma vez, e `historico`, um registro por sessão, com número prévio → item, situação e motivo) e `tecnico` (estado interno dos apps).

A triagem ganha `primeiroGrau` (`juizo`, `juiz`, `magistradoId` do cadastro `gabcmt/referencias/magistrados.json`), para buscar casos do mesmo juiz em 1º grau e marcar o impedimento do art. 144, II, do CPC. `dataEntradaTarefa` foi corrigida em 1.334 processos (dia e mês invertidos na importação do PJe; ver `correcoes`).

Até o Painel ser adaptado (etapa 3), ele continua lendo e gravando `gabcmt/banco/acervo.json`; estes arquivos foram gerados a partir dele pelo `scripts/migrar_acervo.py`, que confere, linha a linha, que nenhum valor decidido se perdeu.

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
