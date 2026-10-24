#!/usr/bin/env python3
"""Reconcilia o DataJud com o acervo PRIVADO. Simulação é o padrão."""

import argparse
import copy
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from cliente import ClienteDatajud, ErroConsulta, carregar_chave

FONTE = "DataJud"
COLUNAS = {
    "classe": "Classe", "classeCodigo": "Código_Classe", "assunto": "Assunto_Principal",
    "assuntoCodigo": "Código_Assunto_Principal", "assuntos": "Assuntos_do_Processo",
    "ultimaMovimentacao": "Última_Movimentação", "dataUltimaMovimentacao": "Data_Última_Movimentação",
    "datajudSituacao": "DataJud · Situação", "datajudConferencia": "DataJud · Conferir",
    "datajudMotivo": "DataJud · Observação", "datajudDataAjuizamento": "DataJud · Ajuizamento",
}


def canon(valor):
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def numero(valor):
    return re.sub(r"\D", "", str(valor or ""))


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto).lower()) if not unicodedata.combining(c))


def data_iso(valor):
    if valor in (None, ""):
        return ""
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(valor / 1000, timezone.utc).isoformat()
    texto = str(valor).strip()
    for formato in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(texto, formato).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return d.replace(tzinfo=d.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()
    except ValueError:
        return ""


def sigiloso(registro):
    return normalizar(registro.get("constaAcervoSigiloso", "")) in ("sim", "s", "1", "true") or normalizar(
        registro.get("nivelSigilo", "")) not in ("", "0", "nao", "publico", "nan")


def lista(valor):
    return valor if isinstance(valor, list) else [valor] if valor else []


def resumo_fontes(fontes):
    ordenadas = sorted(fontes, key=lambda f: (data_iso(f.get("dataHoraUltimaAtualizacao")), str(f.get("id", ""))))
    principal = ordenadas[-1]
    movimentos = [m for f in fontes for m in lista(f.get("movimentos")) if isinstance(m, dict)]
    ultimo = max(movimentos, key=lambda m: data_iso(m.get("dataHora")), default={})
    assuntos = {str(a.get("codigo", "")) + ":" + str(a.get("nome", "")): a
                for f in fontes for a in lista(f.get("assuntos")) if isinstance(a, dict)}
    resumo = {"numeroProcesso": numero(principal["numeroProcesso"]), "tribunal": principal.get("tribunal", ""),
              "graus": sorted({str(f.get("grau", "")) for f in fontes}),
              "classe": principal.get("classe", {}), "orgaoJulgador": principal.get("orgaoJulgador", {}),
              "assuntos": [assuntos[k] for k in sorted(assuntos)],
              "dataAjuizamento": principal.get("dataAjuizamento", ""),
              "dataAtualizacao": principal.get("dataHoraUltimaAtualizacao", ""),
              "ultimaMovimentacao": ultimo, "quantidadeRegistros": len(fontes)}
    # Exclui apenas carimbos da infraestrutura; reordenação de arrays não é alteração.
    campos = ("numeroProcesso", "grau", "classe", "orgaoJulgador", "assuntos", "sistema", "formato", "dataAjuizamento", "movimentos")
    conteudos = []
    for fonte in fontes:
        c = {k: fonte[k] for k in campos if k in fonte}
        for k in ("assuntos", "movimentos"):
            if k in c:
                c[k] = sorted(lista(c[k]), key=canon)
        conteudos.append(c)
    resumo["hash"] = hashlib.sha256(canon(sorted(conteudos, key=canon)).encode()).hexdigest()
    return resumo


def alertas(resumo):
    """Indícios acompanhados de evidência; não declara tutela vigente ou prazo."""
    evidencias = [resumo.get("classe", {}).get("nome", "")]
    evidencias += [a.get("nome", "") for a in resumo.get("assuntos", [])]
    evidencias += [resumo.get("ultimaMovimentacao", {}).get("nome", "")]
    regras = [("tutela_liminar", "Indício de tutela ou liminar", 30, r"\b(tutelas?|liminar(?:es)?)\b"),
              ("saude", "Questão de saúde", 20, r"\b(saude|medicamentos?|tratamentos? medicos?|cirurgias?|internacao|internacoes|hospital|hospitais|leitos?|sus)\b"),
              ("concurso", "Concurso público: conferir datas", 10, r"\b(concursos? publicos?|nomeacao|posse em cargo|exames? psicotecnicos?)\b")]
    avisos = []
    for tipo, titulo, peso, padrao in regras:
        provas = sorted({e for e in evidencias if re.search(padrao, normalizar(e))})
        if provas:
            avisos.append({"tipo": tipo, "titulo": titulo, "prioridade": peso, "evidencias": provas,
                           "observacao": "Indício nos metadados. Conferir os autos; não confirma concessão ou prazo vigente."})
    return avisos


def possivel_motivo(resumo):
    movimento = resumo.get("ultimaMovimentacao", {})
    nome = movimento.get("nome", "")
    t = normalizar(nome)
    for padrao, motivo in [(r"desarquiv|reativ", "Há indicação de reativação na última movimentação."),
                           (r"arquiv", "Possível arquivamento, conforme a última movimentação."),
                           (r"baixa", "Possível baixa, conforme a última movimentação."),
                           (r"remet|remessa|redistribu", "Possível remessa ou redistribuição, conforme a última movimentação.")]:
        if re.search(padrao, t):
            return {"descricao": motivo, "evidencia": movimento, "confirmado": False}
    return {"descricao": "Motivo não determinado. Conferir a última movimentação e o sistema do tribunal.",
            "evidencia": movimento, "confirmado": False}


def atualizar_registro(registro, resumo, categoria, agora):
    r = copy.deepcopy(registro)
    if FONTE not in r.setdefault("fontes", []):
        r["fontes"].append(FONTE)
    classe = resumo.get("classe", {})
    assuntos = resumo.get("assuntos", [])
    principal = assuntos[0] if assuntos else {}
    preencher = {"classe": classe.get("nome", ""), "classeCodigo": str(classe.get("codigo", "")),
                 "assunto": principal.get("nome", ""), "assuntoCodigo": str(principal.get("codigo", "")),
                 "assuntos": "; ".join(a.get("nome", "") for a in assuntos)}
    for campo, valor in preencher.items():
        if not r.get(campo) and valor:
            r[campo] = valor
    movimento = resumo.get("ultimaMovimentacao", {})
    data_nova = data_iso(movimento.get("dataHora"))
    data_antiga = data_iso(r.get("dataUltimaMovimentacao"))
    # Uma movimentação sem data não substitui dados do PJe/BI.
    if data_nova and (not r.get("ultimaMovimentacao") or data_antiga and data_nova > data_antiga):
        r["ultimaMovimentacao"] = movimento.get("nome", "")
        r["dataUltimaMovimentacao"] = data_nova
    r["datajudDataAjuizamento"] = str(resumo.get("dataAjuizamento", ""))
    r["datajudSituacao"] = "Localizado no órgão consultado"
    if categoria == "novos":
        r["datajudConferencia"] = "Sim"
        r["datajudMotivo"] = "Primeira identificação no DataJud; conferir ingresso efetivo e situação no gabinete."
    elif r.get("datajudSituacao") != registro.get("datajudSituacao"):
        r["datajudMotivo"] = "Presença observada no DataJud; não equivale a uma tarefa ou conclusão no PJe."
    if r != registro:
        r["atualizadoEm"] = agora
    return r


def reconciliar(importado, correspondencias, anterior, coleta):
    if not coleta.get("completo") or coleta.get("total") != len(coleta.get("registros", [])):
        raise ErroConsulta("Coleta incompleta: nenhuma atualização será aplicada.")
    agora = coleta["consultadoEm"]
    data_referencia = datetime.fromisoformat(agora.replace("Z", "+00:00")).astimezone(ZoneInfo("America/Cuiaba")).date().isoformat()
    imp, corr = copy.deepcopy(importado), copy.deepcopy(correspondencias)
    registros = imp["registros"]
    atuais = {numero(r["processo"]): r for r in registros}
    if len(atuais) != len(registros):
        raise ErroConsulta("Há números duplicados no acervo. Resolva antes de sincronizar.")
    anteriores = (anterior or {}).get("processos", {})
    if anterior and (anterior.get("tribunal"), anterior.get("orgao")) != (coleta["tribunal"], coleta["orgao"]):
        raise ErroConsulta("A configuração do órgão mudou; use outro estado para não gerar ausências falsas.")
    agrupados = {}
    for fonte in coleta["registros"]:
        n = numero(fonte["numeroProcesso"])
        if len(n) != 20:
            raise ErroConsulta("Número inválido na coleta.")
        if sigiloso(atuais.get(n, {})) or sigiloso(fonte):
            continue
        agrupados.setdefault(n, []).append(fonte)
    estado = {"schema": "gabcmt.datajud.estado", "tribunal": coleta["tribunal"], "orgao": coleta["orgao"],
              "consultadoEm": agora, "processos": copy.deepcopy(anteriores)}
    itens, sem_cobertura = [], []
    for n, fontes in agrupados.items():
        resumo = resumo_fontes(fontes)
        anterior_proc = anteriores.get(n)
        if n not in atuais and not anterior_proc:
            categoria = "novos"
        elif anterior_proc:
            categoria = "iguais" if anterior_proc["resumo"]["hash"] == resumo["hash"] and anterior_proc.get("presente") else "atualizados"
        else:
            # Primeira leitura estabelece a linha de base; não inventa alteração passada.
            categoria = "iguais"
        registro = atuais.get(n, {"id": "acv-" + n, "processo": n, "fontes": []})
        atualizado = atualizar_registro(registro, resumo, categoria, agora)
        campos_importados = ("ultimaMovimentacao", "dataUltimaMovimentacao", "classe", "classeCodigo", "assunto", "assuntoCodigo", "assuntos")
        mudancas = [{"campo": k, "anterior": registro.get(k), "atual": atualizado.get(k)}
                    for k in campos_importados if atualizado.get(k) != registro.get(k)]
        if categoria == "iguais" and mudancas:
            categoria = "atualizados"
        if anterior_proc and anterior_proc["resumo"]["hash"] != resumo["hash"]:
            for k in ("classe", "assuntos", "orgaoJulgador", "graus", "ultimaMovimentacao"):
                antigo = anterior_proc["resumo"].get(k)
                if canon(antigo) != canon(resumo.get(k)):
                    mudancas.append({"campo": f"DataJud.{k}", "anterior": antigo, "atual": resumo.get(k)})
        atuais[n] = atualizado
        avisos = alertas(resumo)
        itens.append({"categoria": categoria, "processo": n, "primeiraLeitura": not bool(anterior_proc),
                      "resumo": resumo, "mudancas": mudancas, "avisos": avisos,
                      "prioridade": max((a["prioridade"] for a in avisos), default=0),
                      "prazo": "Não informado pelo DataJud. Conferir os autos.",
                      "observacao": "Primeira identificação na fonte; não comprova distribuição ou ingresso hoje." if categoria == "novos" else ""})
        estado["processos"][n] = {"presente": True, "ultimoEncontro": agora, "resumo": resumo}
    for n, antigo in anteriores.items():
        if n in agrupados or sigiloso(atuais.get(n, {})):
            continue
        motivo = possivel_motivo(antigo["resumo"])
        itens.append({"categoria": "ausentes", "processo": n, "resumo": antigo["resumo"],
                      "ultimaPresenca": antigo["ultimoEncontro"], "motivoPossivel": motivo,
                      "avisos": [], "prioridade": 0,
                      "observacao": "Ausente da consulta ao órgão; não foi excluído do acervo."})
        estado["processos"][n]["presente"] = False
        if n in atuais:
            r = atuais[n]
            mensagem = "Ausente da consulta ao órgão — conferir"
            if r.get("datajudSituacao") != mensagem:
                r.update(datajudSituacao=mensagem, datajudConferencia="Sim", datajudMotivo=motivo["descricao"], atualizadoEm=agora)
    for n, registro in atuais.items():
        if n not in agrupados and n not in anteriores:
            sem_cobertura.append({"processo": n, "motivo": "Sigiloso: fora da cobertura da API pública." if sigiloso(registro)
                                  else "Ainda não localizado neste órgão pela API pública; sem conclusão sobre sua situação."})
    itens.sort(key=lambda i: (-i["prioridade"], {"novos": 0, "atualizados": 1, "ausentes": 2, "iguais": 3}[i["categoria"]], i["processo"]))
    # Mantém a ordem do acervo existente; novos registros são acrescentados ao fim.
    imp["registros"] = list(atuais.values())
    imp["atualizadoEm"] = agora
    for campo, coluna in COLUNAS.items():
        fontes = corr.setdefault("importado", {}).setdefault(campo, [])
        entrada = {"sistema": FONTE, "coluna": coluna}
        if entrada not in fontes:
            fontes.append(entrada)
    if FONTE not in corr.setdefault("sistemas", []):
        corr["sistemas"].append(FONTE)
    contagens = {cat: sum(i["categoria"] == cat for i in itens) for cat in ("novos", "atualizados", "iguais", "ausentes")}
    relatorio = {"schema": "gabcmt.datajud.relatorio", "consultadoEm": agora, "dataReferencia": data_referencia,
                 "fuso": "America/Cuiaba", "tribunal": coleta["tribunal"],
                 "orgao": coleta["orgao"], "coletaCompleta": True, "primeiraExecucao": not bool(anterior),
                 "totalRegistrosDatajud": coleta["total"], "totais": {**contagens, "semCobertura": len(sem_cobertura)},
                 "avisos": [i for i in itens if i["avisos"]], "processos": itens, "semCobertura": sem_cobertura,
                 "observacao": "Indícios dos metadados; não confirma prazo, tutela vigente ou ingresso efetivo. Ausências não removem processos."}
    return imp, corr, estado, relatorio


def gravar_json(caminho, valor, preservar_indentacao=False):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    indentacao = 2
    if preservar_indentacao and caminho.exists():
        with caminho.open(encoding="utf-8") as existente:
            estilo = re.search(r'\n([ \t]+)"', existente.read(4096))
        if estilo:
            indentacao = estilo.group(1)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(json.dumps(valor, ensure_ascii=False, indent=indentacao) + "\n", encoding="utf-8")
    os.replace(temporario, caminho)


def chave_publica_wiki():
    with urlopen("https://datajud-wiki.cnj.jus.br/api-publica/acesso", timeout=30) as r:
        texto = html.unescape(re.sub(r"<[^>]+>", " ", r.read().decode("utf-8")))
    chaves = set(re.findall(r"APIKey\s+([A-Za-z0-9+/=_-]{20,})", texto))
    if len(chaves) != 1:
        raise ErroConsulta("Não foi possível identificar uma única chave pública na Wiki. Configure DATAJUD_API_KEY.")
    return chaves.pop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acervo", type=Path, required=True, help="Checkout do repositório PRIVADO gabcmt-dados")
    parser.add_argument("--tribunal", default="trf3")
    parser.add_argument("--orgao", default="91624")
    parser.add_argument("--lote", type=int, default=200, help="Registros por chamada, de 1 a 500")
    parser.add_argument("--aplicar", action="store_true", help="Grava no checkout local. Não faz commit nem push.")
    parser.add_argument("--saida", type=Path, help="Na simulação, salva o relatório somente neste diretório privado")
    parser.add_argument("--chave-wiki", action="store_true", help="Obtém a chave pública na Wiki se não houver configuração local")
    args = parser.parse_args()
    pasta = args.acervo.resolve() / "acervo"
    imp = json.loads((pasta / "importado.json").read_text())
    corr = json.loads((pasta / "correspondencias.json").read_text())
    if imp.get("schema") != "gabcmt.acervo.importado":
        raise ErroConsulta("O diretório não contém o acervo esperado.")
    caminho_estado = pasta / "datajud" / "estado.json"
    anterior = json.loads(caminho_estado.read_text()) if caminho_estado.exists() else None
    chave = carregar_chave()
    if not chave and args.chave_wiki:
        chave = chave_publica_wiki()
    coleta = ClienteDatajud(chave=chave).coletar_orgao(
        args.orgao, args.tribunal, tamanho=args.lote,
        progresso=lambda n, total: print(f"DataJud: {n}/{total} registros recebidos.", file=sys.stderr, flush=True))
    novo, corr, estado, relatorio = reconciliar(imp, corr, anterior, coleta)
    if args.aplicar:
        gravar_json(pasta / "importado.json", novo, preservar_indentacao=True)
        gravar_json(pasta / "correspondencias.json", corr, preservar_indentacao=True)
        gravar_json(caminho_estado, estado)
        gravar_json(pasta / "datajud" / "ultimo-relatorio.json", relatorio)
        gravar_json(pasta / "datajud" / "relatorios" / (relatorio["dataReferencia"] + ".json"), relatorio)
    elif args.saida:
        gravar_json(args.saida / "relatorio.json", relatorio)
    print(json.dumps({"aplicadoLocalmente": args.aplicar, "coletaCompleta": True,
                      "registrosDatajud": coleta["total"], "totais": relatorio["totais"]}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ErroConsulta, OSError, ValueError) as erro:
        # Sem dump de respostas, tokens ou registros privados nos logs do runner.
        print("Sincronização interrompida:", str(erro) if isinstance(erro, ErroConsulta) else type(erro).__name__)
        raise SystemExit(1)
