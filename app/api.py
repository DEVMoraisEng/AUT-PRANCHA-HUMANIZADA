"""
Ponte entre a pagina web e o programa.

A pagina chama `ler(...)` para descobrir os ambientes e o acabamento sugerido,
mostra isso numa tabela para a pessoa conferir/corrigir, e depois chama
`gerar(...)` com o que ela escolheu. Assim o acabamento nao e um palpite
escondido: e uma escolha visivel, feita por quem conhece a casa.

Roda igual no computador e dentro do navegador (Pyodide).
"""
import base64, io, json, os

import pipeline, humanizar, prancha, timbrado, nomes

TMP = "/tmp/prancha"


def _preparar_arquivos(planta_b64, fachada_b64, timbrado_b64=None):
    os.makedirs(TMP, exist_ok=True)
    cam = {}
    for chave, dados, nome in (("planta", planta_b64, "PLANTA.pdf"),
                               ("fachada", fachada_b64, "3D.pdf"),
                               ("timbrado", timbrado_b64, "TIMBRADO.docx")):
        if not dados:
            continue
        p = os.path.join(TMP, nome)
        with open(p, "wb") as f:
            f.write(base64.b64decode(dados))
        cam[chave] = p
    return cam


def ler(planta_b64, pagina=0, escala=None):
    """Passo 1: le a planta e devolve os ambientes com o acabamento sugerido.
    Nao gera PDF nenhum - e rapido e serve para a pessoa conferir antes."""
    cam = _preparar_arquivos(planta_b64, None)
    P = pipeline.preparar(cam["planta"], beta=250.0, pagina=pagina,
                          escala_fixa=escala)
    ambientes = []
    for a in P["amb"]:
        ambientes.append({
            "nome": a["nome"],
            "rotulo": a["rotulo"],
            "area": round(a["area"], 2),
            "medido": round(a["medido"], 2),
            "erro": round(a["erro"], 1),
            "acabamento": humanizar.material_de(a["nome"]),
        })
    return json.dumps({
        "escala": round(P["escala"]),
        "confiavel": bool(P["confiavel"]),
        "paginas": 1,
        "ambientes": ambientes,
        "acabamentos": ["ceramica50", "concreto", "grama"],
    }, ensure_ascii=False)


def gerar(planta_b64, fachada_b64, titulo="CASA", lote=None, moveis="traco",
          paleta="neutra", pagina=0, escala=None, pisos=None, apelidos=None,
          timbrado_b64=None):
    """Passo 2: monta a prancha e devolve o PDF em base64.
    `pisos` e `apelidos` sao os ajustes que a pessoa fez na tabela."""
    cam = _preparar_arquivos(planta_b64, fachada_b64, timbrado_b64)
    fundo = timbrado.extrair(cam["timbrado"]) if "timbrado" in cam else \
        timbrado.extrair(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "Modelo_papel_timbrado__Morais_Engenharia.docx"))

    P = pipeline.preparar(cam["planta"], beta=250.0, pagina=pagina,
                          apelidos=apelidos or {}, escala_fixa=escala)
    img = humanizar.desenhar(P, dpi_saida=300, reducao=0.62, paleta=paleta,
                             override=pisos or {}, moveis=moveis)
    saida = os.path.join(TMP, "PRANCHA.pdf")
    prancha.montar(img, cam["fachada"], P["amb"], titulo, saida,
                   area_lote=lote, timbrado=fundo)

    with open(saida, "rb") as f:
        pdf = base64.b64encode(f.read()).decode()

    conferencia = [{
        "nome": a["nome"], "rotulo": a["rotulo"],
        "area": round(a["area"], 2), "medido": round(a["medido"], 2),
        "erro": round(a["erro"], 1),
        "acabamento": humanizar.material_de(a["nome"], pisos or {}),
    } for a in P["amb"]]

    return json.dumps({
        "pdf": pdf,
        "escala": round(P["escala"]),
        "confiavel": bool(P["confiavel"]),
        "conferencia": conferencia,
        "caracteristicas": prancha.resumo(P["amb"]),
        "areas": {k: round(v, 2) for k, v in prancha.areas(P["amb"], lote).items()},
    }, ensure_ascii=False)
