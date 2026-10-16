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

try:
    import render
except Exception:          # sem o modulo novo o gerador continua no caminho antigo
    render = None

ACABAMENTOS_RENDER = ["porcelanato", "ceramica", "madeira", "pedra", "concreto", "pintado", "grama", "deck", "brita"]

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


def _preparar_v4(caminho, fv4, pagina, apelidos):
    """Ficha v4: nome, area e contorno ja vem do Revit - nao precisa ler a
    cor do PDF (que era o passo mais lento: ~1 min no navegador). O PDF so
    serve para os textos (churrasqueira escrita na planta)."""
    page = None
    try:
        import pymupdf
        page = pymupdf.open(caminho)[pagina]
    except Exception:
        pass
    amb = []
    for it in fv4.get("ambientes") or []:
        area = float(it.get("area") or 0.0)
        nome = it.get("nome") or "AMBIENTE"
        amb.append({"nome": nome, "area": area, "lido": area, "medido": area, "erro": 0.0,
                    "x": 0.0, "y": 0.0, "cor": it.get("cor"),
                    "rotulo": nomes.bonito(nome, apelidos or {}, True),
                    "confiavel": True, "celula_ok": True, "motivo": ""})
    if not amb:
        raise ValueError("A ficha (.json) nao tem ambientes. No Revit, coloque os ambientes "
                         "(Arquitetura > Ambiente) na planta e rode o Exportar Tudo de novo.")
    return dict(page=page, amb=amb, escala=float(fv4.get("escala") or 50), confiavel=True,
                dpi=150, via_ficha=True, render=True)


def _preparar(caminho, pagina, escala, apelidos, sem_numero, ficha_txt):
    """Escolhe o caminho: ficha de cores do Revit (exato) ou deducao.

    'sem_numero' e sempre verdadeiro: a prancha e peca de venda, e ali o
    ambiente e QUARTO, nao QUARTO 02. O parametro so continua existindo para
    nao quebrar quem chama a api por fora."""
    sem_numero = True
    if ficha_txt:
        return cores.preparar(caminho, ficha_txt, pagina=pagina,
                              apelidos=apelidos or {}, sem_numero=sem_numero)
    return pipeline.preparar(caminho, beta=250.0, pagina=pagina,
                             apelidos=apelidos or {}, escala_fixa=escala,
                             sem_numero=sem_numero)


def _ficha_v4(ficha_txt):
    """Ficha com paredes (v4) → dict; senão None (fica o caminho do PDF)."""
    if not ficha_txt or render is None:
        return None
    try:
        d = cores.carregar_ficha(ficha_txt) if not isinstance(ficha_txt, dict) else ficha_txt
    except Exception:
        try:
            d = json.loads(ficha_txt)
        except Exception:
            return None
    return d if d.get("paredes") else None


def _par_da_ficha(fv4, amb):
    """Liga cada ambiente lido (P['amb']) ao da ficha: mesmo nome sem numero
    e a area mais parecida (duas SUÍTES nao se trocam)."""
    import re as _re
    def chave(t):
        return _re.sub(r"[\s\d]+$", "", render.norm(t or "")).strip()
    livres = list(fv4.get("ambientes") or [])
    out = {}
    for a in amb:
        cand = [f for f in livres if chave(f.get("nome")) == chave(a["nome"])]
        if not cand:
            continue
        f = min(cand, key=lambda f: abs(float(f.get("area") or 0) - float(a["area"] or 0)))
        livres.remove(f)
        out[id(a)] = f
    return out


def _textos(P):
    """Todo texto escrito na planta - e dali que sai a churrasqueira, que no
    Revit quase nunca e ambiente."""
    try:
        if P.get("page") is None:
            return []
        return [w[4] for w in P["page"].get_text("words")]
    except Exception:
        return []


def descrever(nomes_json, churrasqueira=False):
    """Refaz a descricao quando a pessoa marca/desmarca a churrasqueira na tela.
    Recebe so os nomes dos ambientes (nao precisa ler o PDF de novo)."""
    amb = [{"nome": n, "area": 0.0} for n in json.loads(nomes_json)]
    return json.dumps(prancha.resumo(amb, bool(churrasqueira)), ensure_ascii=False)


def ler(planta_b64, pagina=0, escala=None, sem_numero=True, ficha=None):
    """Passo 1: le a planta e devolve os ambientes com o acabamento sugerido.
    Nao gera PDF nenhum - e rapido e serve para a pessoa conferir antes."""
    cam = _preparar_arquivos(planta_b64, None)
    fv4 = _ficha_v4(ficha)
    P = (_preparar_v4(cam["planta"], fv4, pagina, None) if fv4 else
         _preparar(cam["planta"], pagina, escala, None, sem_numero, ficha))
    pares = _par_da_ficha(fv4, P["amb"]) if fv4 else {}
    ambientes = []
    for a in P["amb"]:
        if fv4:
            f = pares.get(id(a)) or {}
            acab = render.material_do_ambiente(f or {"nome": a["nome"]}, fv4.get("pisos"))[0]
        else:
            acab = humanizar.material_de(a["nome"])
        ambientes.append({
            "nome": a["nome"],
            "rotulo": a["rotulo"],
            "area": round(a["area"], 2),
            "medido": round(a["medido"], 2),
            "erro": round(a["erro"], 1),
            "acabamento": acab,
            "confiavel": bool(a["confiavel"]),
            "motivo": a["motivo"],
        })
    if not ambientes:
        raise ValueError(
            "Nao encontrei nenhum ambiente com nome e area nessa planta. "
            "O arquivo precisa ser o PDF vetorial exportado do Revit, com os "
            "rotulos de ambiente visiveis - imagem escaneada ou print nao serve.")

    somas = prancha.areas(P["amb"])
    churras = prancha.tem_churrasqueira(P["amb"], _textos(P))
    return json.dumps({
        "churrasqueira": churras,
        "caracteristicas": prancha.resumo(P["amb"], churras),
        "escala": round(P["escala"]),
        "confiavel": bool(P["confiavel"]),
        "ambientes": ambientes,
        "acabamentos": ACABAMENTOS_RENDER if fv4 else ["ceramica50", "concreto", "grama"],
        "render": bool(fv4),
        "sugestao_construida": round(somas["ÁREA CONSTRUÍDA"], 2),
        "sugestao_quintal": round(somas["ÁREA DE QUINTAL"], 2),
        "via_ficha": bool(P.get("via_ficha")),
    }, ensure_ascii=False)


def gerar(planta_b64, fachada_b64, titulo="CASA", lote=None, pagina=0,
          escala=None, pisos=None, apelidos=None, timbrado_b64=None,
          construida=None, quintal=None, sem_numero=True, ficha=None,
          caracteristicas=None, churrasqueira=None, telhado="revit",
          estilo="viva"):
    """Passo 2: monta a prancha e devolve o PDF em base64.
    `pisos` e `apelidos` sao os ajustes que a pessoa fez na tabela;
    `caracteristicas` e a descricao como ela deixou na caixa de texto
    (lista de linhas, ou texto com uma linha por item)."""
    if isinstance(caracteristicas, str):
        caracteristicas = [l for l in caracteristicas.splitlines() if l.strip()]
    elif caracteristicas is not None:
        caracteristicas = [str(l) for l in caracteristicas]
    cam = _preparar_arquivos(planta_b64, fachada_b64, timbrado_b64)
    docx = cam.get("timbrado") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "Modelo_papel_timbrado__Morais_Engenharia.docx")
    fundo = timbrado.extrair(docx, os.path.join(TMP, "timbrado_fundo.jpg"))
    try:
        pecas = timbrado.pecas(docx, TMP)
    except Exception:
        pecas = None

    fv4 = _ficha_v4(ficha)
    P = (_preparar_v4(cam["planta"], fv4, pagina, apelidos) if fv4 else
         _preparar(cam["planta"], pagina, escala, apelidos, sem_numero, ficha))
    # A saida e rasterizada acima da resolucao de analise: abaixo dela o
    # desenho ja chegaria reduzido na folha, e reduzir traco fino e o que
    # produz o efeito de borrado. Como so o recorte do desenho vai para a
    # alta resolucao, isso cabe na memoria do navegador.
    dpi_saida = int(min(600, max(420, P["dpi"] * 1.25)))
    img = etiquetas = None
    if fv4:
        # ficha v4: a planta e DESENHADA a partir do Revit (paredes, portas,
        # moveis, pisos) com textura, luz e sombra — nao repintada do PDF
        try:
            pares = _par_da_ficha(fv4, P["amb"])
            over = {}
            for a in P["amb"]:
                f = pares.get(id(a))
                if f and pisos and a["nome"] in pisos:
                    over[f.get("nome")] = pisos[a["nome"]]
            img, ets, _tela = render.renderizar(fv4, rotulos=False, max_px=6000, override=over)
            rotulo = {}
            for a in P["amb"]:
                f = pares.get(id(a))
                if f:
                    rotulo[id(f)] = (a.get("rotulo") or a["nome"]).upper()
            por_nome = {}
            for f in fv4.get("ambientes") or []:
                por_nome.setdefault(f.get("nome"), []).append(f)
            etiquetas = []
            for e in ets:
                f = next((x for x in por_nome.get(e["nome"], []) if abs(float(x.get("area") or 0) -
                                                                       float(e.get("area") or 0)) < 0.01), None)
                nome = rotulo.get(id(f)) if f is not None else None
                if not nome or nome.startswith("AMBIENTE"):
                    continue
                if e.get("area") and e["area"] < 0.8:
                    continue
                etiquetas.append({"nome": nome, "area": float(e.get("area") or 0), "x": e["x"], "y": e["y"],
                                  "vao": max(4.0, 2 * e.get("meia_larg", e["folga_px"])),
                                  "raio": max(2.0, e.get("meia_alt", e["folga_px"])), "confiavel": True})
        except Exception as ex:          # qualquer falha: volta ao caminho do PDF
            print("render falhou, usando o PDF:", ex)
            img = etiquetas = None
    if img is None:
        if P.get("render"):          # o desenho novo falhou: volta a ler o PDF pela cor
            P = _preparar(cam["planta"], pagina, escala, apelidos, sem_numero, ficha)
            dpi_saida = int(min(600, max(420, P["dpi"] * 1.25)))
        img, etiquetas = humanizar.desenhar(P, dpi_saida=dpi_saida,
                                            paleta="viva" if estilo == "viva" else "neutra",
                                            override=pisos or {})
    textos = _textos(P)
    saida = os.path.join(TMP, "PRANCHA.pdf")
    prancha.montar(img, cam["fachada"], P["amb"], titulo, saida,
                   area_lote=lote, timbrado=fundo,
                   area_construida=construida, area_quintal=quintal,
                   pecas=pecas, etiquetas=etiquetas,
                   caracteristicas=caracteristicas or None,
                   churrasqueira=churrasqueira, textos=textos,
                   telhado=telhado or "revit")

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
        "caracteristicas": caracteristicas or prancha.resumo(P["amb"], churrasqueira, textos),
        "areas": {k: round(v, 2) for k, v in
                  prancha.areas(P["amb"], lote, construida, quintal).items()},
    }, ensure_ascii=False)
