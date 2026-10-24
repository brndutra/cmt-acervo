"""Consultas por número CNJ ou órgão julgador, sem dependências externas."""

import json
import os
import re
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://api-publica.datajud.cnj.jus.br"
PASTA = Path(__file__).resolve().parent
POR_PAGINA = 20
LIMITE_RESULTADOS = 10000
TRIBUNAIS = {
    **{f"trf{i}": f"TRF{i} · Justiça Federal da {i}ª Região" for i in range(1, 7)},
    **{f"tj{uf}": f"TJ{uf.upper()} · {nome}" for uf, nome in [
        ("ac", "Acre"), ("al", "Alagoas"), ("am", "Amazonas"), ("ap", "Amapá"),
        ("ba", "Bahia"), ("ce", "Ceará"), ("dft", "Distrito Federal e Territórios"),
        ("es", "Espírito Santo"), ("go", "Goiás"), ("ma", "Maranhão"),
        ("mg", "Minas Gerais"), ("ms", "Mato Grosso do Sul"), ("mt", "Mato Grosso"),
        ("pa", "Pará"), ("pb", "Paraíba"), ("pe", "Pernambuco"), ("pi", "Piauí"),
        ("pr", "Paraná"), ("rj", "Rio de Janeiro"), ("rn", "Rio Grande do Norte"),
        ("ro", "Rondônia"), ("rr", "Roraima"), ("rs", "Rio Grande do Sul"),
        ("sc", "Santa Catarina"), ("se", "Sergipe"), ("sp", "São Paulo"),
        ("to", "Tocantins"),
    ]},
    "stj": "STJ · Superior Tribunal de Justiça",
    "tst": "TST · Tribunal Superior do Trabalho",
    "tse": "TSE · Tribunal Superior Eleitoral",
    "stm": "STM · Superior Tribunal Militar",
}


class ErroConsulta(Exception):
    def __init__(self, mensagem, status=400, *, transitorio=False):
        super().__init__(mensagem)
        self.status = status
        self.transitorio = transitorio


def carregar_chave():
    """A variável do processo prevalece; o .env nunca é servido pelo app."""
    chave = os.environ.get("DATAJUD_API_KEY", "").strip()
    if not chave and (PASTA / ".env").is_file():
        for linha in (PASTA / ".env").read_text(encoding="utf-8").splitlines():
            nome, separador, valor = linha.partition("=")
            if separador and nome.strip() == "DATAJUD_API_KEY":
                chave = valor.strip().strip("\"'")
                break
    if chave.startswith("APIKey "):
        chave = chave[7:].strip()
    return chave


def normalizar_numero(numero):
    if not isinstance(numero, str):
        raise ErroConsulta("Informe o número CNJ como texto, com 20 dígitos.")
    numero = numero.strip()
    if not re.fullmatch(r"[0-9.\-\s]+", numero):
        raise ErroConsulta("Use somente os dígitos do número CNJ, com ou sem pontuação.")
    numero = re.sub(r"[.\-\s]", "", numero)
    if len(numero) != 20:
        raise ErroConsulta("O número CNJ deve ter exatamente 20 dígitos.")
    return numero


class ClienteDatajud:
    def __init__(self, chave=None, intervalo=1.0):
        self.chave = chave
        self.intervalo = intervalo
        self._lock = threading.Lock()
        self._ultimo_inicio = 0.0

    def consultar(self, numero=None, tribunal="trf3", *, orgao=None, tipo_orgao="codigo", pagina=1):
        if type(pagina) is not int or not 1 <= pagina <= LIMITE_RESULTADOS // POR_PAGINA:
            raise ErroConsulta("Página inválida.")
        if orgao is None:
            numero = normalizar_numero(numero)
            if pagina != 1:
                raise ErroConsulta("A consulta por número não usa paginação.")
            criterio = {"tipo": "processo", "valor": numero}
            corpo = {"size": 100, "track_total_hits": True,
                     "query": {"match": {"numeroProcesso": numero}}}
        else:
            if numero is not None:
                raise ErroConsulta("Escolha a busca por número ou por órgão julgador.")
            if not isinstance(orgao, str) or tipo_orgao not in ("codigo", "nome"):
                raise ErroConsulta("Informe o código ou o nome do órgão julgador.")
            orgao = orgao.strip()
            if tipo_orgao == "codigo":
                if not re.fullmatch(r"[0-9]{1,12}", orgao) or int(orgao) <= 0:
                    raise ErroConsulta("O código do órgão julgador deve ser um número inteiro positivo.")
                orgao = str(int(orgao))
                query = {"term": {"orgaoJulgador.codigo": int(orgao)}}
            else:
                if not 3 <= len(orgao) <= 180:
                    raise ErroConsulta("Informe o nome do órgão julgador com 3 a 180 caracteres.")
                query = {"match_phrase": {"orgaoJulgador.nome": orgao}}
            criterio = {"tipo": f"orgao_{tipo_orgao}", "valor": orgao}
            corpo = {"size": POR_PAGINA, "from": (pagina - 1) * POR_PAGINA,
                     "track_total_hits": True, "query": query,
                     "sort": [{"dataAjuizamento": {"order": "desc", "missing": "_last"}}]}
        dados = self._executar(corpo, tribunal)
        tribunal = tribunal.lower()
        hits = dados["hits"]["hits"]
        total = dados["hits"]["total"]
        quantidade = total if isinstance(total, int) else total["value"]
        paginacao = None
        if orgao is not None:
            fim = (pagina - 1) * POR_PAGINA + len(hits)
            paginacao = {"pagina": pagina, "porPagina": POR_PAGINA, "total": quantidade,
                         "mais": bool(hits) and fim < min(quantidade, LIMITE_RESULTADOS),
                         "limiteAtingido": quantidade > LIMITE_RESULTADOS,
                         "limite": LIMITE_RESULTADOS}
        return {"numeroProcesso": numero, "tribunal": tribunal, "criterio": criterio,
                "paginacao": paginacao,
                "consultadoEm": datetime.now(timezone.utc).isoformat(), "dados": dados}

    def _executar(self, corpo, tribunal):
        if not isinstance(tribunal, str) or tribunal.lower() not in TRIBUNAIS:
            raise ErroConsulta("Selecione um dos tribunais disponíveis.")
        tribunal = tribunal.lower()
        chave = self.chave if self.chave is not None else carregar_chave()
        if not chave:
            raise ErroConsulta("Configure DATAJUD_API_KEY no arquivo datajud/.env e tente novamente.", 503)
        if not re.fullmatch(r"[A-Za-z0-9+/=_\-]+", chave):
            raise ErroConsulta("A configuração DATAJUD_API_KEY contém caracteres inválidos.", 503)

        # Uma requisição/segundo por instância; o coletor controla as retentativas.
        # O limite compartilhado da chave também depende de outros usuários.
        with self._lock:
            espera = self.intervalo - (time.monotonic() - self._ultimo_inicio)
            if espera > 0:
                time.sleep(espera)
            self._ultimo_inicio = time.monotonic()

        requisicao = Request(
            f"{BASE_URL}/api_publica_{tribunal}/_search",
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Authorization": f"APIKey {chave}",
                     "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(requisicao, timeout=30) as resposta:
                dados = json.load(resposta)
        except HTTPError as erro:
            mensagens = {
                401: "O DataJud recusou a chave. Confira a chave pública vigente na Wiki do CNJ.",
                403: "Acesso negado. Confira a chave pública e a liberação do domínio na rede.",
                404: "O endpoint desse tribunal não está disponível no DataJud.",
                429: "O DataJud limitou as consultas. Aguarde antes de tentar novamente.",
            }
            raise ErroConsulta(mensagens.get(erro.code, f"O DataJud respondeu com erro HTTP {erro.code}. Tente mais tarde."),
                               429 if erro.code == 429 else 502,
                               transitorio=erro.code == 429 or erro.code >= 500) from None
        except (TimeoutError, socket.timeout):
            raise ErroConsulta("O DataJud demorou a responder. Tente novamente mais tarde.", 504, transitorio=True) from None
        except URLError:
            raise ErroConsulta("Não foi possível conectar ao DataJud. Confira a rede e a liberação de api-publica.datajud.cnj.jus.br.", 502) from None
        except (ValueError, UnicodeDecodeError):
            raise ErroConsulta("O DataJud devolveu uma resposta que não é JSON válido.", 502) from None

        if not isinstance(dados, dict) or not isinstance(dados.get("hits"), dict):
            raise ErroConsulta("O DataJud devolveu uma resposta sem os resultados esperados.", 502)
        hits = dados["hits"].get("hits")
        total = dados["hits"].get("total")
        if (not isinstance(hits, list)
                or any(not isinstance(h, dict) or not isinstance(h.get("_source"), dict) for h in hits)
                or not (isinstance(total, int) or isinstance(total, dict) and isinstance(total.get("value"), int))):
            raise ErroConsulta("O DataJud devolveu resultados em formato inesperado.", 502)
        if dados.get("timed_out") or dados.get("_shards", {}).get("failed", 0):
            raise ErroConsulta("O DataJud retornou uma consulta incompleta. Tente novamente mais tarde.", 502)
        return dados

    def coletar_orgao(self, orgao, tribunal="trf3", tamanho=200, progresso=None):
        """Leitura completa por cursor; nunca declara completa uma página parcial."""
        if not isinstance(orgao, str) or not re.fullmatch(r"[0-9]{1,12}", orgao) or int(orgao) <= 0:
            raise ErroConsulta("Código do órgão julgador inválido.")
        if type(tamanho) is not int or not 1 <= tamanho <= 500:
            raise ErroConsulta("Tamanho do lote inválido.")
        cursor, esperado, vistos, registros = None, None, set(), []
        while True:
            corpo = {"size": tamanho, "track_total_hits": True,
                     "query": {"term": {"orgaoJulgador.codigo": int(orgao)}},
                     "sort": [{"id.keyword": "asc"}]}
            if cursor is not None:
                corpo["search_after"] = cursor
            for tentativa in range(3):
                try:
                    dados = self._executar(corpo, tribunal)
                    break
                except ErroConsulta as erro:
                    if not erro.transitorio or tentativa == 2:
                        raise
                    time.sleep(30 if erro.status == 429 else 2 ** (tentativa + 1))
            total = dados["hits"]["total"]
            if isinstance(total, dict) and total.get("relation", "eq") != "eq":
                raise ErroConsulta("O DataJud não informou o total exato; a sincronização foi interrompida.", 502)
            quantidade = total if isinstance(total, int) else total["value"]
            if esperado is None:
                esperado = quantidade
            if quantidade != esperado:
                raise ErroConsulta("O total mudou durante a coleta. Nenhuma atualização será aplicada; tente mais tarde.", 502)
            hits = dados["hits"]["hits"]
            for hit in hits:
                identidade = hit.get("_id")
                fonte = hit["_source"]
                if (not identidade or identidade in vistos
                        or str(fonte.get("orgaoJulgador", {}).get("codigo")) != str(int(orgao))):
                    raise ErroConsulta("A coleta contém registro duplicado ou fora do órgão. Nenhuma atualização será aplicada.", 502)
                normalizar_numero(fonte.get("numeroProcesso"))
                vistos.add(identidade)
                registros.append(fonte)
            if progresso:
                progresso(len(registros), esperado)
            if len(registros) == esperado:
                break
            if not hits or len(registros) > esperado or not hits[-1].get("sort") or hits[-1]["sort"] == cursor:
                raise ErroConsulta("A coleta não alcançou todos os registros. Nenhuma atualização será aplicada.", 502)
            cursor = hits[-1]["sort"]
        return {"tribunal": tribunal.lower(), "orgao": str(int(orgao)), "total": esperado,
                "registros": registros, "completo": True,
                "consultadoEm": datetime.now(timezone.utc).isoformat()}
