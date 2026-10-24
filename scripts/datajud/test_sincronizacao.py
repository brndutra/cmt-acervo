import copy
import io
import json
import unittest
from unittest.mock import patch

from cliente import ClienteDatajud, ErroConsulta
from sincronizar import alertas, reconciliar, resumo_fontes

N1, N2 = "00000000020244036000", "00000000120244036000"
AGORA = "2026-10-24T10:17:00+00:00"


def fonte(n=N1, nome="Juntada de petição", data="2026-10-20T12:00:00Z"):
    return {"id": n, "numeroProcesso": n, "tribunal": "TRF3", "grau": "G2", "nivelSigilo": 0,
            "orgaoJulgador": {"codigo": 91624, "nome": "Órgão de teste"},
            "classe": {"codigo": 1, "nome": "Classe de teste"},
            "assuntos": [{"codigo": 1, "nome": "Assunto de teste"}],
            "movimentos": [{"codigo": 1, "nome": nome, "dataHora": data}]}


def coleta(*fontes):
    return {"tribunal": "trf3", "orgao": "91624", "completo": True,
            "total": len(fontes), "registros": list(fontes), "consultadoEm": AGORA}


def banco(*numeros):
    return {"schema": "gabcmt.acervo.importado", "registros": [
        {"id": "acv-" + n, "processo": n, "fontes": ["PJe 2G"],
         "etiquetas": "Controle do gabinete", "tarefaAtual": "Minutar"} for n in numeros]}


class TestReconciliacao(unittest.TestCase):
    def test_novos_e_dados_operacionais_preservados(self):
        imp = banco(N1)
        original = copy.deepcopy(imp)
        novo, corr, estado, rel = reconciliar(imp, {}, None, coleta(fonte(), fonte(N2)))
        self.assertEqual(imp, original)
        existentes = {r["processo"]: r for r in novo["registros"]}
        self.assertEqual(existentes[N1]["etiquetas"], "Controle do gabinete")
        self.assertEqual(existentes[N1]["tarefaAtual"], "Minutar")
        self.assertNotIn("dataDistribuicao", existentes[N2])
        self.assertNotIn("constaAcervoGabA", existentes[N2])
        self.assertEqual(existentes[N2]["datajudConferencia"], "Sim")
        self.assertEqual(rel["totais"]["novos"], 1)
        self.assertTrue(estado["processos"][N2]["presente"])
        self.assertIn({"sistema": "DataJud", "coluna": "Classe"}, corr["importado"]["classe"])

    def test_reexecucao_iguais_e_mudanca_real(self):
        imp, corr, estado, _ = reconciliar(banco(), {}, None, coleta(fonte()))
        _, _, estado2, rel = reconciliar(imp, corr, estado, coleta(fonte()))
        self.assertEqual(rel["totais"]["iguais"], 1)
        _, _, _, rel = reconciliar(imp, corr, estado2, coleta(fonte(nome="Conclusos para decisão")))
        self.assertEqual(rel["totais"]["atualizados"], 1)

    def test_ausencia_preserva_processo_e_informa_evidencia(self):
        imp, corr, estado, _ = reconciliar(banco(), {}, None, coleta(fonte(nome="Arquivamento definitivo")))
        novo, _, estado, rel = reconciliar(imp, corr, estado, coleta())
        self.assertEqual(len(novo["registros"]), 1)
        self.assertEqual(rel["totais"]["ausentes"], 1)
        item = rel["processos"][0]
        self.assertIn("Possível arquivamento", item["motivoPossivel"]["descricao"])
        self.assertFalse(item["motivoPossivel"]["confirmado"])
        self.assertFalse(estado["processos"][N1]["presente"])

    def test_sem_cobertura_na_primeira_execucao_nao_e_ausencia(self):
        imp = banco(N1, N2)
        imp["registros"][1]["constaAcervoSigiloso"] = "Sim"
        _, _, _, rel = reconciliar(imp, {}, None, coleta())
        self.assertEqual(rel["totais"]["ausentes"], 0)
        self.assertEqual(rel["totais"]["semCobertura"], 2)

    def test_movimentacao_antiga_nao_sobrepoe_pje(self):
        imp = banco(N1)
        imp["registros"][0].update(ultimaMovimentacao="Movimento mais recente no PJe", dataUltimaMovimentacao="2026-10-23")
        novo, _, _, _ = reconciliar(imp, {}, None, coleta(fonte()))
        self.assertEqual(novo["registros"][0]["ultimaMovimentacao"], "Movimento mais recente no PJe")

    def test_urgencia_evidencia_sem_inventar_prazo(self):
        f = fonte(nome="Liminar indeferida")
        f["assuntos"] = [{"nome": "Saúde"}, {"nome": "Concurso público"}]
        avisos = alertas(resumo_fontes([f]))
        self.assertEqual({a["tipo"] for a in avisos}, {"tutela_liminar", "saude", "concurso"})
        self.assertIn("Liminar indeferida", avisos[0]["evidencias"])
        self.assertIn("não confirma", avisos[0]["observacao"])
        _, _, _, rel = reconciliar(banco(), {}, None, coleta(fonte(N2), f))
        self.assertEqual(rel["processos"][0]["processo"], N1)
        self.assertIn("Não informado", rel["processos"][0]["prazo"])

    def test_carimbos_e_ordem_nao_sao_mudanca_material(self):
        f, g = fonte(), fonte()
        f["@timestamp"] = "2026-10-20"
        g["@timestamp"] = "2026-10-21"
        g["dataHoraUltimaAtualizacao"] = "2026-10-21"
        self.assertEqual(resumo_fontes([f])["hash"], resumo_fontes([g])["hash"])

    def test_prioridades_com_plural_e_acentos(self):
        f = fonte(nome="Liminares")
        f["assuntos"] = [{"nome": "Fornecimento de Medicamentos"}, {"nome": "Concursos Públicos"}]
        avisos = alertas(resumo_fontes([f]))
        self.assertEqual({a["tipo"] for a in avisos}, {"tutela_liminar", "saude", "concurso"})

    def test_falha_parcial_ou_troca_orgao_nao_atualiza(self):
        parcial = coleta(fonte())
        parcial["completo"] = False
        with self.assertRaises(ErroConsulta):
            reconciliar(banco(), {}, None, parcial)
        _, _, estado, _ = reconciliar(banco(), {}, None, coleta(fonte()))
        outro = coleta(fonte())
        outro["orgao"] = "123"
        with self.assertRaises(ErroConsulta):
            reconciliar(banco(), {}, estado, outro)

    def test_dois_graus_geram_um_unico_processo(self):
        f, g = fonte(), fonte()
        g.update(id="outro-id", grau="G1")
        novo, _, _, rel = reconciliar(banco(), {}, None, coleta(f, g))
        self.assertEqual(len(novo["registros"]), 1)
        self.assertEqual(rel["processos"][0]["resumo"]["quantidadeRegistros"], 2)

    def test_data_do_relatorio_usa_cuiaba(self):
        c = coleta(fonte())
        c["consultadoEm"] = "2026-10-25T02:00:00+00:00"
        _, _, _, rel = reconciliar(banco(), {}, None, c)
        self.assertEqual(rel["dataReferencia"], "2026-10-24")


class TestColetaCompleta(unittest.TestCase):
    def pagina(self, numeros, total=3):
        return {"hits": {"total": {"value": total, "relation": "eq"}, "hits": [
            {"_id": n, "sort": [n], "_source": fonte(n)} for n in numeros]}}

    def test_cursor_e_total_exato(self):
        paginas = [self.pagina([N1], 2), self.pagina([N2], 2)]
        with patch("cliente.urlopen", side_effect=[io.BytesIO(json.dumps(p).encode()) for p in paginas]) as abrir:
            r = ClienteDatajud(chave="teste", intervalo=0).coletar_orgao("91624", tamanho=1)
        self.assertTrue(r["completo"])
        self.assertEqual(r["total"], 2)
        segundo = json.loads(abrir.call_args_list[1].args[0].data)
        self.assertEqual(segundo["search_after"], [N1])
        self.assertNotIn("from", segundo)

    def test_total_variavel_duplicatas_e_fim_prematuro(self):
        for segunda in [self.pagina([N2], 4), self.pagina([N1]), self.pagina([])]:
            with self.subTest(segunda=segunda), patch("cliente.urlopen", side_effect=[
                    io.BytesIO(json.dumps(self.pagina([N1])).encode()), io.BytesIO(json.dumps(segunda).encode())]):
                with self.assertRaises(ErroConsulta):
                    ClienteDatajud(chave="teste", intervalo=0).coletar_orgao("91624", tamanho=1)

    def test_timeout_transitorio_repete_pagina_sem_duplicar(self):
        with patch("cliente.urlopen", side_effect=[TimeoutError(), io.BytesIO(json.dumps(self.pagina([N1], 1)).encode())]) as abrir, patch("cliente.time.sleep"):
            r = ClienteDatajud(chave="teste", intervalo=0).coletar_orgao("91624")
        self.assertEqual(abrir.call_count, 2)
        self.assertEqual(len(r["registros"]), 1)


if __name__ == "__main__":
    unittest.main()
