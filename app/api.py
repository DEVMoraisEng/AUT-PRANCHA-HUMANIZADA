"""
Ponte entre a pagina web e o programa.

A pagina chama `ler(...)` para descobrir os ambientes e o acabamento sugerido,
mostra isso numa tabela para a pessoa conferir/corrigir, e depois chama
`gerar(...)` com o que ela escolheu. Assim o acabamento nao e um palpite
escondido: e uma escolha visivel, feita por quem conhece a casa.

Roda igual no computador e dentro do navegador (Pyodide).
"""
import base64, io, json, os

import pipeline, humanizar, prancha, timbrado, nomes, cores

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


def _preparar(caminho, pagina, escala, apelidos, sem_numero, ficha_txt):
    """Escolhe o caminho: ficha de cores do Revit (exato) ou deducao."""
    if ficha_txt:
        return cores.preparar(caminho, ficha_txt, pagina=pagina,
                              apelidos=apelidos or {}, sem_numero=sem_numero)
    return pipeline.preparar(caminho, beta=250.0, pagina=pagina,
                             apelidos=apelidos or {}, escala_fixa=escala,
                             sem_numero=sem_numero)


def ler(planta_b64, pagina=0, escala=None, sem_numero=False, ficha=None):
    """Passo 1: le a planta e devolve os ambientes com o acabamento sugerido.
    Nao gera PDF nenhum - e rapido e serve para a pessoa conferir antes."""
    cam = _preparar_arquivos(planta_b64, None)
    P = _preparar(cam["planta"], pagina, escala, None, sem_numero, ficha)
    ambientes = []
    for a in P["amb"]:
        ambientes.append({
            "nome": a["nome"],
            "rotulo": a["rotulo"],
            "area": round(a["area"], 2),
            "medido": round(a["medido"], 2),
            "erro": round(a["erro"], 1),
            "acabamento": humanizar.material_de(a["nome"]),
            "confiavel": bool(a["confiavel"]),
            "motivo": a["motivo"],
        })
    if not ambientes:
        raise ValueError(
            "Nao encontrei nenhum ambiente com nome e area nessa planta. "
            "O arquivo precisa ser o PDF vetorial exportado do Revit, com os "
            "rotulos de ambiente visiveis - imagem escaneada ou print nao serve.")

    somas = prancha.areas(P["amb"])
    return json.dumps({
        "escala": round(P["escala"]),
        "confiavel": bool(P["confiavel"]),
        "ambientes": ambientes,
        "acabamentos": ["ceramica50", "concreto", "grama"],
        "sugestao_construida": round(somas["ÁREA CONSTRUÍDA"], 2),
        "sugestao_quintal": round(somas["ÁREA DE QUINTAL"], 2),
        "via_ficha": bool(P.get("via_ficha")),
    }, ensure_ascii=False)


def gerar(planta_b64, fachada_b64, titulo="CASA", lote=None, pagina=0,
          escala=None, pisos=None, apelidos=None, timbrado_b64=None,
          construida=None, quintal=None, sem_numero=False, ficha=None):
    """Passo 2: monta a prancha e devolve o PDF em base64.
    `pisos` e `apelidos` sao os ajustes que a pessoa fez na tabela."""
    cam = _preparar_arquivos(planta_b64, fachada_b64, timbrado_b64)
    docx = cam.get("timbrado") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Modelo_papel_timbrado__Morais_Engenharia.docx")
    fundo = timbrado.extrair(docx, os.path.join(TMP, "timbrado_fundo.jpg"))
    try:
        pecas = timbrado.pecas(docx, TMP)
    except Exception:
        pecas = None

    P = _preparar(cam["planta"], pagina, escala, apelidos, sem_numero, ficha)
    img = humanizar.desenhar(P, dpi_saida=300, reducao=0.62,
                             override=pisos or {})
    saida = os.path.join(TMP, "PRANCHA.pdf")
    prancha.montar(img, cam["fachada"], P["amb"], titulo, saida,
                   area_lote=lote, timbrado=fundo,
                   area_construida=construida, area_quintal=quintal, pecas=pecas)

    with open(saida, "rb") as f:
        pdf = base64.b64encode(f.read()).decode()

    conferencia = [{
        "nome": a["nome"], "rotulo": a["rotulo"],
        "area": round(a["area"], 2), "medido": round(a["medido"], 2),
        "erro": round(a["erro"], 1),
        "acabamento": humanizar.material_de(a["nome"], pisos or {}),
        "confiavel": bool(a["confiavel"]), "motivo": a["motivo"],
    } for a in P["amb"]]

    return json.dumps({
        "pdf": pdf,
        "escala": round(P["escala"]),
        "confiavel": bool(P["confiavel"]),
        "conferencia": conferencia,
        "via_ficha": bool(P.get("via_ficha")),
        "caracteristicas": prancha.resumo(P["amb"]),
        "areas": {k: round(v, 2) for k, v in
                  prancha.areas(P["amb"], lote, construida, quintal).items()},
    }, ensure_ascii=False)
