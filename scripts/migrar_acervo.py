#!/usr/bin/env python3
"""Migração única do acervo (gabcmt/banco/acervo.json, schema gabA.acervo v2) para o padrão 1.0.

Gera, neste repositório privado:
  acervo/correspondencias.json  de-para de cada coluna antiga: campo único do importado, regra calculada,
                                campo operacional, valor-padrão não gravado ou coluna descartada (com motivo)
  acervo/importado.json         dados dos sistemas (PJe 2G, BI Gab A, BI Sigilosos), um campo por informação
  acervo/operacional.json       só o que o gabinete decide: triagem, minuta, revisão, fluxo, pauta (histórico)
  trabalho.json                 configuração do Painel, servidores, sessões e calendário (produtividade
                                continua em produtividade/registros.json até o app migrar)

Uso: python3 scripts/migrar_acervo.py CAMINHO/gabcmt/banco/acervo.json [CAMINHO/gabcmt/banco]
A conferência no fim reconstrói cada linha antiga a partir dos arquivos novos e lista o que difere.
"""
import json, re, sys, collections
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
AGORA = "2026-10-23T00:00:00Z"

PJE, BI, SIG, APP = "PJe 2G", "BI · Gab A Acervo", "BI · Sigilosos", "Painel (cópia)"

# campo único → fontes, na ordem de prioridade (a primeira com valor prevalece)
IMPORTADO = {
    "classe": [(PJE, "classe_judicial"), (BI, "Classe")],
    "classeCodigo": [(BI, "Código_Classe")],
    "assunto": [(PJE, "assunto_judicial"), (BI, "Assunto_Principal"), (APP, "Assunto")],
    "assuntoCodigo": [(BI, "Código_Assunto_Principal")],
    "assuntos": [(BI, "Assuntos_do_Processo")],
    "poloAtivo": [(PJE, "polo_ativo"), (BI, "Polo_Ativo")],
    "poloPassivo": [(PJE, "polo_passivo"), (BI, "Polo_Passivo")],
    "advogadoPoloAtivo": [(PJE, "advogado_pa")],
    "advogadoPoloPassivo": [(PJE, "advogado_pp")],
    "dpu": [(PJE, "dpu")],
    "mpf": [(PJE, "mpf")],
    "valorCausa": [(PJE, "valor_causa"), (BI, "Valor_da_Causa")],
    "dataDistribuicao": [(PJE, "data_distribuição"), (BI, "Data_Distribuição")],
    "dataDistribuicao1g": [(PJE, "dt_distribuicao_1g")],
    "tarefaAtual": [(PJE, "tarefa_atual")],
    "dataEntradaTarefa": [(PJE, "data_entrada_tarefa")],
    "prioridade": [(PJE, "prioridade")],
    "etiquetas": [(PJE, "etiquetas"), (PJE, "Etiquetas"), (PJE, "Etiquetas Originais")],
    "ultimaMovimentacao": [(PJE, "ultimo_movimento"), (BI, "Última_Movimentação")],
    "dataUltimaMovimentacao": [(BI, "Data_Última_Movimentação")],
    "tempoUltimaMovimentacao": [(SIG, "Tempo_Última_Movimentação")],
    "descricaoUltimaMovimentacaoSigilo": [(SIG, "DES_ULTIMA_MOVIME_SIGILO")],
    "gabinete": [(BI, "Gabinete")],
    "secaoOrigem": [(BI, "Seção de Origem")],
    "conclusao": [(BI, "Conclusão?")],
    "dataConclusao": [(BI, "Data_Conclusão")],
    "pendentePrimeiroJulgamento": [(BI, "Pendente_1Julgamento"), (SIG, "SIN_PEND_1JULG")],
    "fase": [(BI, "Fase")],
    "dataFase": [(BI, "Data_Fase")],
    "complemento": [(BI, "Complemento")],
    "base": [(BI, "BASE")],
    "dataFim": [(BI, "DAT_FIM")],
    "kTempo": [(BI, "K_TEMPO")],
    "tempoTramitacao": [(BI, "Tempo_de_Tramitação")],
    "sinPrazoLegal": [(BI, "SIN_PRAZO_LEGAL")],
    "sinSecretaria": [(BI, "SIN_SECRETARIA")],
    "sinContadoria": [(BI, "SIN_CONTADORIA")],
    "sinRemessaExterna": [(BI, "SIN_REM_EXTERNA")],
    "sinUfor": [(BI, "SIN_UFOR")],
    "sinCls": [(SIG, "SIN_CLS")],
    "indicador": [(BI, "Indicador")],
    "constaAcervoGabA": [(BI, "Consta_Acervo_Gab_A")],
    "constaAcervoSigiloso": [(SIG, "Consta_Acervo_Sigiloso")],
    "nivelSigilo": [(SIG, "Nível_Sigilo")],
}

# recalculadas pelo Painel a cada abertura (recompute): não são gravadas
REGRAS = {
    "Seção Competente": "etiqueta de seção explícita (ex.: '1ª Seção'); senão, o colegiado/assunto (Painel.secaoCompetente)",
    "META": "metas do CNJ pelas etiquetas e pela data de distribuição (_autoMetaTags)",
    "Impedimento / Suspeição": "cadastro de magistrados × partes e advogados (_autoRestriction); padronizar nomes",
    "AJG": "presença da DPU ou etiqueta de gratuidade (_ajgAuto, _ajgFromDpu)",
    "DPU (controle)": "campo dpu do PJe (_dpuAuto)",
    "Regularidade": "texto montado a partir de representação, preparo e tempestividade (Painel.regularityNotes)",
    "Etiquetas Operacionais": "pendências da triagem (ex.: VERIFICAR REPRESENTAÇÃO), recalculadas",
    "Status de Julgamento": "situação do último registro do histórico de pauta",
    "Ano": "ano de dataDistribuicao",
    "Mês": "mês de dataDistribuicao",
    "Qtde": "sempre 1 quando há indicador (contagem do BI)",
    "Preparo = AJG": "preparo dispensado quando há AJG (_preparoAJG)",
}
REGRAS_TECNICAS = ["_ajgAuto", "_ajgFromDpu", "_dpuAuto", "_autoRestriction", "_magistradoRestricao", "_restricaoTipo",
                   "_autoMetaTags", "_metaTagSignature", "_restrictionTagSignature", "_preparoAJG", "_preparoBeforeAJG",
                   "_prontoParaCorrecao", "_julgadorColegiado"]

# valores iniciais: não são decisão de ninguém, portanto não são gravados
PADROES = {"Tempestividade": "Não Verificado", "Preparo": "Não Verificado", "Representação": "Não Verificado",
           "MPF": "Não se Aplica", "Mídias": "Não se Aplica", "Contrarrazões - Situação": "apresentadas"}

FLUXO = {"SERVIDOR": ("responsavel",), "MINUTADO": ("minutado", "data"), "CORRETOR": ("corrigido", "corretor"),
         "CORRIGIDO": ("corrigido", "data"), "LIBERADO": ("liberado", "data"), "Refinado": ("refinado",),
         "Conferido": ("conferido",)}
FLUXO_TEC = {"_minutadoBy": ("minutado", "servidor"), "_minutadoSession": ("minutado", "sessao"),
             "_minutadoMemberId": ("minutado", "membroId")}
PAUTA_COLS = ["Sessão original", "Sessão Destino", "Nova sessão de destino", "Motivo da remarcação", "Seleção Prévia",
              "JULGAMENTO", "Numeração Prévia", "Item (Pauta de Julgamento)", "Sessões", "Histórico"]
PAUTA_TEC = ["_pautaEntries", "_pautaHistory", "_pautaIncidents"]
# metadados da importação: vão para o importado
IMPORT_TEC = {"_pjeCreated": "criadoPelaImportacaoPje", "_pjeFields": "camposVindosDoPje", "_tagOriginalSource": "origemEtiquetas"}
DESCARTADAS = {"id": "recriado a partir do processo", "Processo": "igual a processo", "atualizado_em": "vai para o envelope",
               "_": "desmembrado em fluxo, pauta, regras e técnico"}

MINUTA_PREF = ("Sentença", "Voto", "Rel ", "Rel -", "Relatório", "Sub-ementa", "Contrarrazões", "Memoriais", "Outros",
               "Outras Peças", "Recorrente", "Recurso", "Pedido recursal", "Tópico-síntese", "Adequação Ementa",
               "Formatação Subementa", "Parecer", "MPF - Sem ID", "MPF - Info")


def vazio(v):
    return v in (None, "", [], {})


def igual(a, b):
    n = lambda x: re.sub(r"\s+", " ", str(x)).strip().casefold()
    return n(a) == n(b)


def data_iso(v):
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2})", str(v).strip())     # DAT_FIM vem como m/d/aa
    if m:
        return f"20{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(v).strip())
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else v


def bloco(col):
    if col.startswith("Rev - "):
        return "revisao"
    if col.startswith(MINUTA_PREF):
        return "minuta"
    return "triagem"


def montar_pauta(r, t):
    h = []
    for e in t.get("_pautaEntries") or []:
        st = (e.get("status") or "").strip().lower()
        h.append({"sessao": e.get("session", ""), "numeroPrevio": "", "item": e.get("item", ""),
                  "situacao": "pendente" if st in ("pendente", "") else st, "proximaSessao": e.get("next", ""),
                  "encerrado": bool(e.get("closed")), "motivo": ""})
    destino = r.get("Sessão Destino") or r.get("Nova sessão de destino") or ""
    if destino and not any(x["sessao"] == destino for x in h):
        h.append({"sessao": destino, "numeroPrevio": "", "item": "", "situacao": "selecionado", "proximaSessao": "",
                  "encerrado": False, "motivo": ""})
    alvo = next((x for x in h if x["sessao"] == destino), h[-1] if h else None)
    if alvo is not None:
        if (r.get("Seleção Prévia") or r.get("JULGAMENTO") or "").upper() == "SELECIONADO PREVIAMENTE":
            alvo["situacao"] = "selecionado"
        alvo["numeroPrevio"] = r.get("Numeração Prévia", "") or alvo["numeroPrevio"]
        alvo["item"] = r.get("Item (Pauta de Julgamento)", "") or alvo["item"]
        alvo["motivo"] = r.get("Motivo da remarcação", "") or alvo["motivo"]
    datas = [x["sessao"] for x in h] + ([r["Sessão original"]] if r.get("Sessão original") else [])
    ordena = lambda d: data_iso(d) if re.match(r"\d{2}/\d{2}/\d{4}", d) else d
    p = {}
    if datas:
        p["sessaoInaugural"] = min(datas, key=ordena)
    if h:
        p["historico"] = h
    for k in ("_pautaHistory", "_pautaIncidents"):
        if not vazio(t.get(k)):
            p["eventos" if k == "_pautaHistory" else "incidentes"] = t[k]
    for k in ("Sessões", "Histórico"):
        if not vazio(r.get(k)):
            p.setdefault("anotacoes", {})[k] = r[k]
    return p


def main(arq_acervo, pasta_banco):
    doc = json.loads(Path(arq_acervo).read_text(encoding="utf-8"))
    R = doc["registros"]

    # padrões técnicos: valor de '_' repetido em ≥90% das linhas onde aparece (molduras vazias do gerador de minuta)
    usados = set(REGRAS_TECNICAS) | set(FLUXO_TEC) | set(PAUTA_TEC) | set(IMPORT_TEC)
    freq = collections.defaultdict(collections.Counter)
    for r in R:
        for k, v in (r.get("_") or {}).items():
            if k not in usados and not vazio(v):
                freq[k][json.dumps(v, sort_keys=True, ensure_ascii=False)] += 1
    padroes_tec = {}
    for k, c in freq.items():
        val, n = c.most_common(1)[0]
        if n >= 0.9 * sum(c.values()):
            padroes_tec[k] = json.loads(val)

    cols_imp = {col for fontes in IMPORTADO.values() for _, col in fontes}
    reservadas = cols_imp | set(REGRAS) | set(FLUXO) | set(PAUTA_COLS) | set(DESCARTADAS) | {"processo"}

    importado, operacional = [], []
    divergentes = collections.Counter()
    blocos_vistos = collections.defaultdict(set)
    for r in R:
        proc = r["processo"]
        rid = r["id"]
        imp = {"id": rid, "processo": proc}
        fontes_reg, div = set(), {}
        for campo, fontes in IMPORTADO.items():
            vals = [(s, c, r.get(c)) for s, c in fontes if not vazio(r.get(c))]
            if not vals:
                continue
            s0, c0, v0 = vals[0]
            imp[campo] = data_iso(v0) if campo == "dataFim" else v0
            fontes_reg.update(s for s, _, _ in vals if s != APP)
            for s, c, v in vals[1:]:
                if not igual(v, v0) and s != APP:
                    div.setdefault(campo, {})[s] = v
                    divergentes[campo] += 1
        imp["fontes"] = sorted(fontes_reg)
        for k, campo in IMPORT_TEC.items():
            v = (r.get("_") or {}).get(k)
            if not vazio(v):
                imp[campo] = v
        if div:
            imp["divergencias"] = div
        importado.append(imp)

        t = r.get("_") or {}
        op = {}
        for col, v in r.items():
            if col in reservadas or vazio(v) or PADROES.get(col) == v:
                continue
            if col == "Preparo" and v == "AJG" and (r.get("_") or {}).get("_preparoAJG"):
                continue   # preparo dispensado pela AJG: regra
            b = bloco(col)
            op.setdefault(b, {})[col] = v
            blocos_vistos[b].add(col)
        fl = {}
        for col, caminho in FLUXO.items():
            if not vazio(r.get(col)):
                if len(caminho) == 1:
                    fl[caminho[0]] = r[col]
                else:
                    fl.setdefault(caminho[0], {})[caminho[1]] = r[col]
        for k, (a, b) in FLUXO_TEC.items():
            if not vazio(t.get(k)):
                fl.setdefault(a, {})[b] = t[k]
        if fl:
            op["fluxo"] = fl
        pa = montar_pauta(r, t)
        if pa:
            op["pauta"] = pa
        tec = {k: v for k, v in t.items()
               if k not in usados and not vazio(v) and not (k in padroes_tec and v == padroes_tec[k])}
        if tec:
            op["tecnico"] = tec
        if op:
            operacional.append({"id": rid, "processo": proc, **op})

    corr = {
        "schema": "gabcmt.acervo.correspondencias", "versao": "1.0.0", "atualizadoEm": AGORA,
        "sistemas": [PJE, BI, SIG],
        "regraDePrioridade": "Em cada campo vale a primeira fonte da lista que tiver valor; as demais, quando diferem "
                             "(além de maiúsculas e espaços), ficam em 'divergencias' do processo.",
        "importado": {campo: [{"sistema": s, "coluna": c} for s, c in f] for campo, f in IMPORTADO.items()},
        "metadadosDaImportacao": IMPORT_TEC,
        "regras": REGRAS,
        "regrasTecnicas": REGRAS_TECNICAS,
        "padroes": PADROES,
        "padroesTecnicos": padroes_tec,
        "operacional": {b: sorted(c) for b, c in blocos_vistos.items()},
        "fluxo": {**{c: ".".join(p) for c, p in FLUXO.items()}, **{c: ".".join(p) for c, p in FLUXO_TEC.items()}},
        "pauta": {"colunas": PAUTA_COLS + PAUTA_TEC,
                  "regra": "sessaoInaugural = primeira sessão em que o processo entrou (gravada uma vez); historico = um "
                           "registro por sessão (sessao, numeroPrevio → item, situacao, motivo, proximaSessao, encerrado); "
                           "sessão atual e status de julgamento saem do último registro"},
        "descartadas": {**DESCARTADAS, "JULGAMENTO": "duplicava 'Seleção Prévia' (mesmo valor nos mesmos processos)",
                        "Assunto": "cópia de assunto_judicial em 100% das linhas (fica como fonte de reserva)",
                        "Etiquetas / Etiquetas Originais": "iguais a etiquetas do PJe em 100% das linhas"},
        "observacoes": ["data_entrada_tarefa traz datas futuras (ex.: 2026-12-08) em parte das linhas: provável "
                        "inversão dia/mês na importação do PJe; conferir no importador do Painel."],
    }
    escrever("acervo/correspondencias.json", corr)
    escrever("acervo/importado.json", {"schema": "gabcmt.acervo.importado", "versao": "1.0.0", "atualizadoEm": AGORA,
                                       "origem": f"migração de gabcmt/banco/acervo.json ({doc.get('atualizado_em')})",
                                       "registros": importado})
    escrever("acervo/operacional.json", {"schema": "gabcmt.acervo.operacional", "versao": "1.0.0", "atualizadoEm": AGORA,
                                         "registros": operacional})

    # trabalho.json: configuração do Painel + controle de pauta (servidores, sessões, calendário)
    trab = {"schema": "gabcmt.trabalho", "versao": "1.0.0", "atualizadoEm": AGORA,
            "painel": doc.get("painel", {}),
            "produtividade": "continua em produtividade/registros.json até o Controle de Pauta migrar (etapa 3)"}
    if pasta_banco:
        for nome, chave in (("controle-pauta-servidores.json", "servidores"), ("controle-pauta-sessoes-calendario.json", "sessoes")):
            p = Path(pasta_banco) / nome
            if p.exists():
                trab[chave] = json.loads(p.read_text(encoding="utf-8"))
    escrever("trabalho.json", trab)

    # conferência: reconstrói a linha antiga (menos regras e padrões) e compara
    imp_id = {x["id"]: x for x in importado}
    op_id = {x["id"]: x for x in operacional}
    perdas = collections.Counter()
    for r in R:
        i, o = imp_id[r["id"]], op_id.get(r["id"], {})
        for col, v in r.items():
            if vazio(v) or col in REGRAS or col in DESCARTADAS or PADROES.get(col) == v or col == "processo":
                continue
            if col in cols_imp:
                campo = next(c for c, f in IMPORTADO.items() if any(cc == col for _, cc in f))
                ok = igual(i.get(campo), v) or igual(i.get("divergencias", {}).get(campo, {}).get(
                    next(s for s, cc in IMPORTADO[campo] if cc == col), None), v) or (campo == "dataFim")
            elif col in FLUXO:
                ok = json.dumps(o.get("fluxo", {}), ensure_ascii=False).find(json.dumps(v, ensure_ascii=False)) >= 0
            elif col == "Preparo" and v == "AJG" and (r.get("_") or {}).get("_preparoAJG"):
                ok = True
            elif col in ("Seleção Prévia",):
                ok = any(x["situacao"] == "selecionado" for x in o.get("pauta", {}).get("historico", []))
            elif col in PAUTA_COLS:
                ok = json.dumps(o.get("pauta", {}), ensure_ascii=False).find(json.dumps(v, ensure_ascii=False)) >= 0 \
                     or col == "JULGAMENTO"
            else:
                ok = o.get(bloco(col), {}).get(col) == v
            if not ok:
                perdas[col] += 1
    print(f"processos: {len(R)} | importado: {len(importado)} | operacional (com algo decidido): {len(operacional)}")
    print("campos com divergência entre sistemas:", dict(divergentes))
    print("padrões técnicos descartados:", list(padroes_tec))
    print("conferência — valores não reencontrados:", dict(perdas) or "nenhum")


def escrever(rel, obj):
    p = RAIZ / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
