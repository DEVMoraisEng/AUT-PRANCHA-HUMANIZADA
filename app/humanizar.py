"""
Motor de humanizacao.
Repinta a planta original sem alterar um milimetro da geometria:
  - piso por acabamento, com a malha no MODULO REAL (50x50 cm na escala do desenho)
  - paredes cheias na cor da marca
  - todo o mobiliario e as loucas do projeto preservados
  - etiquetas redesenhadas com a tipografia da empresa

O acabamento NAO e adivinhado a partir do desenho: ele vem de uma tabela
explicita (REGRAS) que o usuario ve e pode sobrescrever ambiente a ambiente.
O padrao de quem nao esta na tabela e concreto - o mais conservador.
"""
import re
import numpy as np
import pymupdf
from scipy import ndimage
from skimage.morphology import remove_small_holes
from PIL import Image, ImageDraw, ImageFont

# --- identidade Morais -------------------------------------------------------
NAVY = (44, 42, 90)
PETROL = (35, 94, 119)
MINT = (127, 207, 196)

CRUZ = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)

# --- acabamentos -------------------------------------------------------------
# 'modulo' e em METROS: a malha e desenhada na escala real do desenho,
# nao num passo qualquer de pixel.
PALETAS = {
    "neutra": {
        "externo":    dict(cor=(233, 232, 228), junta=None,             tipo="liso",  modulo=None),
        "ceramica50": dict(cor=(238, 236, 232), junta=(215, 212, 206), tipo="malha", modulo=0.50),
        "concreto":   dict(cor=(227, 226, 221), junta=None,             tipo="liso",  modulo=None),
        "grama":      dict(cor=(219, 225, 212), junta=(198, 208, 190),  tipo="grama", modulo=None),
        "_copa":      (152, 164, 145),
    },
    "cor": {
        "externo":    dict(cor=(222, 221, 216), junta=None,             tipo="liso",  modulo=None),
        "ceramica50": dict(cor=(236, 228, 214), junta=(207, 194, 172),  tipo="malha", modulo=0.50),
        "concreto":   dict(cor=(214, 212, 206), junta=None,             tipo="liso",  modulo=None),
        "grama":      dict(cor=(190, 214, 166), junta=(172, 199, 145),  tipo="grama", modulo=None),
        "_copa":      (108, 146, 92),
    },
}

# Tabela de acabamento. Quem nao casa com nenhuma linha vira CONCRETO.
REGRAS = [
    # "descoberto/descoberta" vence tudo: nao tem revestimento
    (r"DESCOBERT|CAL[CÇ]ADA|GARAGEM|IMPERME[AÁ]VEL", "concreto"),
    (r"(?<!IM)PERME[AÁ]VEL|GRAMA|QUINTAL", "grama"),
    (r"SU[IÍ]TE|QUARTO|DORM|SALA|ESTAR|JANTAR|COZINHA|COPA|"
     r"BANHO|WC|LAVABO|SANIT|SERVI[CÇ]O|LAVAND|HALL|CIRCULA", "ceramica50"),
]
PADRAO = "concreto"


def material_de(nome, override=None):
    n = nome.upper().strip()
    if override:
        for chave, mat in override.items():
            if chave.upper().strip() == n:
                return mat
    for padrao, mat in REGRAS:
        if re.search(padrao, n):
            return mat
    return PADRAO


def tabela_acabamentos(amb, override=None):
    return [(a["nome"], material_de(a["nome"], override)) for a in amb]


def textura(shape, mat, px_por_m, seed=0, paleta="neutra", esc=1.0):
    """Textura do acabamento desenhada em TODA a folha, em coordenada global.
    Como a malha e unica para a planta inteira, ela nao quebra na divisa
    entre um comodo e outro."""
    H, W = shape
    m = PALETAS[paleta][mat]
    base = np.zeros((H, W, 3), np.float32)
    base[:] = m["cor"]

    if m["tipo"] == "liso":
        return base

    if m["tipo"] == "malha":
        p = m["modulo"] * px_por_m                 # 50 cm na escala do desenho
        larg = max(1.0, 1.3 * esc)                 # espessura da junta
        yy = np.arange(H)[:, None].astype(np.float32)
        xx = np.arange(W)[None, :].astype(np.float32)
        marca = ((yy % p) < larg) | ((xx % p) < larg)
    else:                                          # grama: ruido organico
        rng = np.random.default_rng(seed)
        r = rng.random((H // 4 + 1, W // 4 + 1))
        r = np.kron(r, np.ones((4, 4)))[:H, :W]
        r = ndimage.gaussian_filter(r, 1.2)
        marca = r > 0.72

    base[marca] = np.array(m["junta"], np.float32)
    return base


def _quase(img, rgb, tol=3):
    return np.abs(img - np.array(rgb, np.float32)).max(axis=2) <= tol


def _malha_do_revit(orig):
    """A malha de piso que o Revit ja desenha (linhas finas esverdeadas).
    Sai do desenho: quem manda na malha e o acabamento definido na tabela."""
    r, g, b = (orig[:, :, i] for i in range(3))
    return (np.abs(g - b) <= 8) & ((g - r) > 12) & (g < 246)


def _chapado(orig, regioes, fracao=0.35):
    """Preenchimento chapado de PISO do desenho tecnico.

    Nao da para achar pelo tamanho: uma cama tambem e grande. O que distingue
    o piso e ser o FUNDO do ambiente - a cor que ocupa a maior parte dele.
    Entao, dentro de cada ambiente, a cor dominante (se dominar de verdade)
    e tratada como piso e sai; movel, louca e bancada sao minoria e ficam.
    """
    saida = np.zeros(orig.shape[:2], bool)
    q = (orig / 6).astype(np.int16)                 # tolera o antialias
    chave = (q[:, :, 0].astype(np.int32) << 16) | (q[:, :, 1].astype(np.int32) << 8) | q[:, :, 2]
    partes = []
    for reg in regioes:                             # cada mancha separada
        lab, n = ndimage.label(reg)
        for i in range(1, n + 1):
            partes.append(lab == i)
    for m in partes:
        n = int(m.sum())
        if n < 400:
            continue
        v, c = np.unique(chave[m], return_counts=True)
        j = int(np.argmax(c))
        if c[j] / n < fracao:
            continue                          # nao ha fundo dominante: nao mexe
        alvo = m & (chave == v[j])
        cor = orig[alvo].mean(axis=0)
        if cor.max() - cor.min() > 16 or cor.mean() < 105:
            continue                          # so fundo neutro e claro
        saida |= alvo
    return saida


def _anotacao(orig):
    """Linhas de anotacao do Revit (separador de ambiente em verde, eixo em azul).
    Sao marcacoes de modelagem, nao fazem parte da arquitetura -> saem.
    Marrom de movel tem o VERMELHO dominante e por isso fica."""
    mx = orig.max(axis=2); mn = orig.min(axis=2)
    return ((mx - mn) > 45) & (np.argmax(orig, axis=2) != 0)


def desenhar(P, dpi_saida=300, reducao=0.56, paleta="neutra", override=None,
             fundo_transparente=True):
    """P vem de pipeline.preparar(). Devolve PIL.Image da planta humanizada.

    fundo_transparente: a margem em branco ao redor do desenho (o resto da
    folha A4 que o Revit exporta em volta da casa) sai com alfa=0 em vez de
    branco solido - assim, colada no timbrado, aparece a textura do papel
    em vez de uma caixa branca. O que E desenho (piso, parede, e qualquer
    traco que ja existia fora dos ambientes nomeados, tipo calcada ou
    tracejado de divisa) continua opaco.
    """
    page = P["page"]
    esc = dpi_saida / P["dpi"]
    px_por_m = float(np.sqrt(P["k"])) * esc
    pix = page.get_pixmap(dpi=dpi_saida, colorspace=pymupdf.csRGB)
    orig = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32)
    H, W = orig.shape[:2]

    seg = np.array(Image.fromarray(P["seg"].astype(np.int32), "I").resize((W, H), Image.NEAREST))
    lote = np.array(Image.fromarray(P["lote"].astype(np.uint8) * 255)
                    .resize((W, H), Image.NEAREST)) > 127
    r, g, b = (orig[:, :, i] for i in range(3))
    paredes = (r > 140) & (r - g > 55) & (r - b > 55)
    # planta pintada pelo Revit: cor de ambiente nao e parede, por mais
    # avermelhada que seja. Sem isto um ambiente rosa vira barreira.
    for rgb in P.get("cores_ambiente", []):
        paredes &= ~_quase(orig, rgb, 60)
    lado = max(3, int(5 * esc) | 1)
    par = ndimage.binary_closing(paredes, np.ones((lado, lado)))
    par = remove_small_holes(par, area_threshold=max(64, int(0.02 * P["k"] * esc * esc)))

    # a segmentacao vem de uma grade mais grossa; na resolucao de saida sobram
    # frestas. Cada fresta vai para o ambiente vizinho, respeitando parede e
    # soleira (o tampao de vao) para nao vazar de um comodo para o outro.
    tapa = np.array(Image.fromarray(P["tapa"].astype(np.uint8) * 255)
                    .resize((W, H), Image.NEAREST)) > 127
    barreira = par | tapa
    livre = lote & ~barreira
    # so fecha a fresta de reamostragem (poucos pixels). NAO sai enchendo o
    # vazio: area sem ambiente nomeado tem que continuar sem dono.
    for _ in range(int(2 * esc) + 3):
        falta = livre & (seg == 0)
        if not falta.any():
            break
        cand = ndimage.grey_dilation(seg, footprint=CRUZ)
        novo = falta & (cand > 0)
        if not novo.any():
            break
        seg[novo] = cand[novo]

    # a soleira em si (o tampao) fica dividida ao meio entre os dois ambientes:
    # e ali que o acabamento troca, como num projeto desenhado a mao.
    # ATENCAO: so a soleira. Ja perdi uma escada inteira por preencher
    # "todo o vazio do lote" com o ambiente mais proximo.
    resto = tapa & ~par & (seg == 0)
    if resto.any():
        _, (iy, ix) = ndimage.distance_transform_edt(seg == 0, return_indices=True)
        seg[resto] = seg[iy[resto], ix[resto]]

    seg = _piso_nao_atravessa_parede(seg, livre)

    # ---------- 1. piso -------------------------------------------------------
    saida = np.full((H, W, 3), 255.0, np.float32)
    cache = {}
    for i, a in enumerate(P["amb"], 1):
        if not a.get("confiavel", True):
            seg[seg == i] = 0        # reprovado na conferencia: fica o original
            continue
        mask = seg == i
        if not mask.any():
            continue
        mat = material_de(a["nome"], override)
        if mat not in cache:
            cache[mat] = textura((H, W), mat, px_por_m, seed=i, paleta=paleta, esc=esc)
        saida[mask] = cache[mat][mask]
    interior = seg > 0

    # ---------- 2. vegetacao: os circulos de arvore que ja existem no projeto --
    arvore = _quase(orig, (127, 127, 127), 2)
    verde_amb = np.zeros((H, W), bool)
    for i, a in enumerate(P["amb"], 1):
        if a.get("confiavel", True) and material_de(a["nome"], override) == "grama":
            verde_amb |= (seg == i)
    copa = ndimage.binary_opening(
        arvore & ndimage.binary_dilation(verde_amb, np.ones((5, 5))), np.ones((9, 9)))
    if copa.any():
        rng = np.random.default_rng(11)
        ruido = ndimage.gaussian_filter(rng.random((H, W)).astype(np.float32), 2.0)
        cv = PALETAS[paleta]["_copa"]
        verde = np.stack([np.full((H, W), float(c)) for c in cv], -1)
        verde += (ruido[:, :, None] - 0.5) * 34
        dc = ndimage.distance_transform_edt(copa).astype(np.float32)
        vol = np.clip(dc / (34 * esc), 0, 1)[:, :, None]
        saida[copa] = (verde * (0.82 + 0.26 * vol))[copa]

    # ---------- 3. traco original (mobiliario, loucas, esquadrias) -------------
    traco = orig.copy()
    traco[paredes] = 255                       # hachura vermelha de parede
    traco[_malha_do_revit(orig) & interior] = 255   # malha de piso do Revit
    anot = _anotacao(orig)
    for rgb in P.get("cores_ambiente", []):
        anot &= ~_quase(orig, rgb, 34)
    traco[ndimage.binary_dilation(anot, np.ones((3, 3)))] = 255
    regioes = [seg == i for i in range(1, len(P["amb"]) + 1)]
    traco[ndimage.binary_dilation(_chapado(orig, regioes), np.ones((3, 3)))] = 255
    # planta que veio pintada pelo Revit: a cor do ambiente e fundo, nao desenho
    for rgb in P.get("cores_ambiente", []):
        traco[ndimage.binary_dilation(_quase(orig, rgb, 30), np.ones((3, 3)))] = 255
    traco[copa] = 255
    for w in page.get_text("words"):           # texto antigo
        x0, y0, x1, y1 = [int(v * dpi_saida / 72.0) for v in w[:4]]
        traco[max(0, y0-2):y1+2, max(0, x0-2):x1+2] = 255

    tinta = 1.0 - traco / 255.0
    # traco fino fica firme; bloco chapado preto entra mais leve
    cheio = ndimage.binary_dilation(
        ndimage.binary_erosion(tinta.max(axis=2) > 0.55, np.ones((int(5 * esc) | 1,) * 2)),
        np.ones((int(7 * esc) | 1,) * 2))
    tinta = np.clip(tinta * np.where(cheio, 0.50, 0.90)[:, :, None], 0, 1)
    saida = np.where(interior[:, :, None], saida * (1 - tinta), saida)

    # ---------- 4. o que o projeto NAO nomeia fica como esta ------------------
    # Escada, quintal, calcada, arvore, cota: se nao ha ambiente com nome e
    # area, o programa NAO repinta e NAO apaga. Copia o desenho como veio.
    # E a regra que impede o programa de "sumir" com o que ele nao entendeu.
    fora = ~interior & ~par
    tinta_fora = np.zeros((H, W), bool)
    if fora.any():
        original = orig.copy()
        original[paredes] = 255                          # a parede vira navy
        original[ndimage.binary_dilation(_anotacao(orig), np.ones((3, 3)))] = 255
        for w in page.get_text("words"):
            x0, y0, x1, y1 = [int(v * dpi_saida / 72.0) for v in w[:4]]
            original[max(0, y0-2):y1+2, max(0, x0-2):x1+2] = 255
        saida[fora] = original[fora]
        # so o que tem tinta de verdade (calcada, tracejado de divisa,
        # seta de norte...) conta como "desenho" pra opacidade - o resto do
        # "fora" e so a margem em branco da folha, sem nada desenhado.
        tinta_fora = fora & (original.min(axis=2) < 250)

    # ---------- 5. paredes ----------------------------------------------------
    saida[par] = NAVY

    if fundo_transparente:
        conteudo = (interior | par | tinta_fora).astype(np.float32)
        alfa = ndimage.gaussian_filter(conteudo, sigma=max(0.6, 0.5 * esc))
        alfa = np.clip(alfa * 255.0, 0, 255).astype(np.uint8)
        rgba = np.dstack([np.clip(saida, 0, 255).astype(np.uint8), alfa])
        img = Image.fromarray(rgba, "RGBA")
    else:
        img = Image.fromarray(np.clip(saida, 0, 255).astype(np.uint8), "RGB")
    livre = interior & ~par & (tinta.max(axis=2) < 0.35)
    return etiquetar(img, P, dpi_saida, seg, livre, px_por_m, reducao=reducao)


def _blocos_de_movel(saida, orig, tinta, interior, px_por_m, esc):
    """Trata o mobiliario que JA ESTA no projeto como bloco de biblioteca:
    preenche a pegada de cada peca com um tom da propria familia de cor dela
    (madeira, estofado, louca, eletro) e joga uma sombra no piso.

    Nao inventa movel nenhum: a pegada, a posicao e o tamanho continuam sendo
    os do desenho. O que muda e o acabamento com que a peca e desenhada.
    """
    ink = (tinta.max(axis=2) > 0.10) & interior
    ink = ndimage.binary_closing(ink, np.ones((max(3, int(3 * esc)),) * 2))
    pecas = ndimage.binary_fill_holes(ink) & interior
    lab, n = ndimage.label(pecas)
    if not n:
        return saida
    m2 = px_por_m ** 2
    corpo = np.zeros(saida.shape, np.float32)
    marca = np.zeros(saida.shape[:2], bool)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        m = (lab[sl] == i)
        area = m.sum() / m2
        if area < 0.05 or area > 7.0:          # poeira e cômodo inteiro ficam fora
            continue
        janela = orig[sl][m]
        cor = janela.mean(axis=0)
        quente = cor[0] - cor[2]
        if cor.mean() < 90:                    # peça escura (bancada, cabeceira)
            tom = np.array([96, 88, 82], np.float32)
        elif quente > 18:                      # madeira
            tom = np.array([196, 176, 148], np.float32)
        elif cor.mean() > 225:                 # louça / peça branca
            tom = np.array([246, 246, 244], np.float32)
        else:                                  # eletro / estofado neutro
            tom = np.array([206, 204, 200], np.float32)
        sub = np.zeros(m.shape, bool); sub[m] = True
        d = ndimage.distance_transform_edt(sub).astype(np.float32)
        vol = np.clip(d / (7 * esc), 0, 1)[:, :, None]
        alvo = corpo[sl]
        alvo[m] = (tom * (0.90 + 0.14 * vol))[m]
        corpo[sl] = alvo
        marca[sl] |= m
    if not marca.any():
        return saida
    desloc = max(1, int(2.2 * esc))
    s = np.roll(np.roll(marca.astype(np.float32), desloc, axis=0), desloc, axis=1)
    s = ndimage.gaussian_filter(s, 2.2 * esc)
    s = np.clip(s * 1.15, 0, 1) * (~marca) * interior
    saida = saida * (1 - 0.20 * s)[:, :, None]
    saida = np.where(marca[:, :, None], corpo, saida)
    return saida


def _piso_nao_atravessa_parede(seg, livre):
    """Regra de sanidade: o piso de um ambiente nao aparece do outro lado de
    uma parede. Se um pedaco de ambiente caiu numa celula que nao e a dele,
    aquele pedaco assume o piso de quem manda naquela celula.
    Ambientes integrados (sala/cozinha sem parede entre eles) dividem a mesma
    celula e por isso continuam intactos."""
    cel, n = ndimage.label(livre)
    if not n:
        return seg
    casa = {}
    for i in range(1, int(seg.max()) + 1):
        m = seg == i
        if not m.any():
            continue
        c, q = np.unique(cel[m], return_counts=True)
        ok = c > 0
        if ok.any():
            casa[i] = int(c[ok][np.argmax(q[ok])])
    dono = np.zeros(n + 1, np.int32)
    for c in range(1, n + 1):
        m = cel == c
        v, q = np.unique(seg[m], return_counts=True)
        ok = v > 0
        if ok.any():
            dono[c] = int(v[ok][np.argmax(q[ok])])
    out = seg.copy()
    for i, c in casa.items():
        fora = (seg == i) & (cel > 0) & (cel != c)
        if fora.any():
            out[fora] = dono[cel[fora]]
    return out


def _fonte(tam, negrito=True):
    from prancha import fonte_arquivo
    try:
        return ImageFont.truetype(fonte_arquivo(negrito), tam)
    except OSError:
        return ImageFont.load_default()


def etiquetar(img, P, dpi, seg, livre, px_por_m, reducao=0.56, passo=4):
    """Posiciona nome + area no ponto mais folgado de cada ambiente.

    'Mais folgado' = ponto do ambiente mais distante de parede, movel, louca e
    das etiquetas ja colocadas. Assim o texto nao precisa de tarja branca atras:
    ele cai em piso limpo e leva so um leve contorno claro.

    Duas garantias que nao existiam antes, as duas do mesmo defeito: quando a
    grade reduzida (passo 4px) nao achava vaga - caso comum em ambiente fino e
    comprido, tipo circulacao ou area permeavel, que pode sumir inteiro numa
    amostragem de 4 em 4 pixel - o ponto de apoio caia na MEDIA das
    coordenadas do ambiente. Num formato em L ou numa tira, a media cai fora
    da propria forma, e o texto nascia por cima do vizinho. Pior: esse mesmo
    caminho desligava o vao (`vao=1e9`), entao nem a largura do texto era
    contida - o nome saia do tamanho normal em cima de qualquer coisa.
    Agora o ponto de apoio, quando a grade falha, vem do proprio recorte do
    ambiente em resolucao cheia (garantido dentro da forma), e o vao nunca
    fica "infinito" - sempre mede o espaco real, mesmo no caminho de reserva.
    Ambiente muito mais alto que largo (tira vertical) escreve o rotulo
    deitado de lado em vez de espremido horizontalmente.
    """
    dr = ImageDraw.Draw(img, "RGBA")
    s = dpi / 72.0
    base = max(13.0, 0.19 * px_por_m)   # ~19 cm de altura na escala do desenho
    fn = _fonte(max(12, int(base)), True)
    fa = _fonte(max(11, int(base * 0.84)), False)

    peq_seg = seg[::passo, ::passo]
    ocupado = ~livre[::passo, ::passo]

    for idx, a in sorted(enumerate(P["amb"], 1), key=lambda t: -t[1]["area"]):
        nome = a.get("rotulo") or a["nome"].upper()
        # sem nome de venda nao ha o que escrever; e num vao de meio metro o
        # texto sairia ilegivel - melhor deixar o ambiente limpo.
        if not nome or nome.upper().startswith("AMBIENTE"):
            continue
        if a["area"] and a["area"] < 0.9:
            continue
        # reprovado na conferencia = sem piso pintado (ver passo 1 de
        # desenhar(): seg fica 0 ali). Sem area propria nenhum ponto de apoio
        # e confiavel - o rotulo cairia em qualquer lugar, inclusive fora do
        # desenho. Melhor nao escrever do que escrever errado.
        if not a.get("confiavel", True):
            continue
        t1, t2 = nome, f"{a['area']:.2f}".replace(".", ",") + " m²"

        m = (peq_seg == idx) & ~ocupado
        if m.any():
            d = ndimage.distance_transform_edt(m)
            yy, xx = np.unravel_index(int(np.argmax(d)), d.shape)
            cx, cy = float(xx * passo), float(yy * passo)
            e, d_ = _vao_horizontal(m, yy, xx)
            e_v, d_v = _vao_vertical(m, yy, xx)
            # extremos em unidade de PIXEL CHEIO (a grade e amostrada de
            # `passo` em `passo`) - deixa os dois caminhos (grade e recorte
            # cheio, no fallback abaixo) na mesma unidade dali pra frente.
            e, d_, e_v, d_v = e * passo, d_ * passo, e_v * passo, d_v * passo
            vao = d_ - e + passo
            vao_v = d_v - e_v + passo
        else:
            # grade reduzida nao achou vaga (ambiente fino sumiu na amostra
            # de 4px): busca o ponto mais distante de parede/movel/limite no
            # recorte em resolucao CHEIA do proprio ambiente - continua
            # garantidamente dentro da forma, seja qual for o formato. Ja
            # devolve tudo em pixel cheio, mesma unidade do caminho acima.
            cx, cy, e, d_, vao, e_v, d_v, vao_v = _ponto_de_apoio_cheio(
                seg, livre, idx, a, s)

        estreito = vao < vao_v * 0.62 and vao_v > vao * 1.6
        vao_texto = vao_v if estreito else vao

        fn_, fa_ = fn, fa
        larg = max(dr.textlength(t1, font=fn_), dr.textlength(t2, font=fa_))
        if larg > vao_texto * 0.92:
            k = max(0.55, vao_texto * 0.92 / max(larg, 1))
            fn_ = _fonte(max(8, int(fn.size * k)), True)
            fa_ = _fonte(max(7, int(fa.size * k)), False)
        linhas = [t1]
        if dr.textlength(t1, font=fn_) > vao_texto * 0.92:
            linhas = _quebrar(t1)
        gap = int(fn_.size * 0.22)
        h = fn_.size * len(linhas) + gap * len(linhas) + fa_.size
        larguras = [dr.textlength(t, font=fn_) for t in linhas]
        w2 = dr.textlength(t2, font=fa_)
        cw = max(larguras + [w2]) / 2 + fn_.size * 0.35
        ch = h / 2 + fn_.size * 0.30

        halo = max(2, int(fn_.size * 0.16))

        if estreito:
            # rotaciona 90°: desenha numa camada a parte (largura = altura do
            # texto deitado) e cola girada - assim o texto corre ao longo da
            # tira em vez de estourar a largura dela.
            cy = min(max(cy, e_v + cw), d_v - cw)
            camada = Image.new("RGBA", (int(2 * ch) + 4, int(2 * cw) + 4), (0, 0, 0, 0))
            dc = ImageDraw.Draw(camada, "RGBA")
            ty = camada.height / 2 - h / 2
            for t, w in zip(linhas, larguras):
                dc.text((camada.width / 2 - w / 2, ty), t, font=fn_, fill=NAVY,
                        stroke_width=halo, stroke_fill=(255, 255, 255, 220))
                ty += fn_.size + gap
            dc.text((camada.width / 2 - w2 / 2, ty), t2, font=fa_, fill=PETROL,
                    stroke_width=max(2, int(halo * 0.85)), stroke_fill=(255, 255, 255, 220))
            girada = camada.rotate(90, expand=True)
            px0 = int(cx - girada.width / 2)
            py0 = int(cy - girada.height / 2)
            img.paste(girada, (px0, py0), girada)

            ry0 = max(0, int((cy - cw) / passo)); ry1 = int((cy + cw) / passo) + 1
            rx0 = max(0, int((cx - ch) / passo)); rx1 = int((cx + ch) / passo) + 1
            ocupado[ry0:ry1, rx0:rx1] = True
            continue

        cx = min(max(cx, e + cw), d_ - cw)

        ry0 = max(0, int((cy - ch) / passo)); ry1 = int((cy + ch) / passo) + 1
        rx0 = max(0, int((cx - cw) / passo)); rx1 = int((cx + cw) / passo) + 1
        ocupado[ry0:ry1, rx0:rx1] = True

        ty = cy - h / 2
        for t, w in zip(linhas, larguras):
            dr.text((cx - w/2, ty), t, font=fn_, fill=NAVY,
                    stroke_width=halo, stroke_fill=(255, 255, 255, 220))
            ty += fn_.size + gap
        dr.text((cx - w2/2, ty), t2, font=fa_, fill=PETROL,
                stroke_width=max(2, int(halo * 0.85)), stroke_fill=(255, 255, 255, 220))
    return img


def _ponto_de_apoio_cheio(seg, livre, idx, a, s):
    """Ponto de apoio garantidamente DENTRO do ambiente, usado quando a
    grade reduzida (amostra de `passo` em `passo` px) nao achou vaga - caso
    tipico de ambiente fino, onde a amostragem pode saltar por cima da tira
    inteira. Trabalha na mascara em resolucao cheia, entao sempre acha um
    ponto valido enquanto o ambiente tiver algum piso livre. Devolve tudo em
    pixel cheio (mesma unidade do caminho pela grade, ja convertido la).
    """
    m_cheio = (seg == idx) & livre
    if not m_cheio.any():
        m_cheio = seg == idx
    if m_cheio.any():
        d = ndimage.distance_transform_edt(m_cheio)
        yy, xx = np.unravel_index(int(np.argmax(d)), d.shape)
        cx, cy = float(xx), float(yy)
        e, d_ = _vao_horizontal(m_cheio, yy, xx)
        e_v, d_v = _vao_vertical(m_cheio, yy, xx)
        return cx, cy, float(e), float(d_), d_ - e + 1, float(e_v), float(d_v), d_v - e_v + 1
    # ambiente sem nenhum piso livre identificavel (nunca deveria acontecer,
    # mas nao trava a prancha por isso): cai no centroide, com um vao minimo
    # em vez de "infinito" - o texto continua contido, so pode sair pequeno.
    cx, cy = a["x"] * s, a["y"] * s
    return cx, cy, cx - 40, cx + 40, 80, cy - 40, cy + 40, 80


def _vao_horizontal(mask, y, x):
    """Extremos livres (esquerda, direita) na horizontal em torno de (y, x)."""
    linha = mask[y]
    e = x
    while e > 0 and linha[e - 1]:
        e -= 1
    d = x
    n = len(linha)
    while d < n - 1 and linha[d + 1]:
        d += 1
    return e, d


def _vao_vertical(mask, y, x):
    """Extremos livres (cima, baixo) na vertical em torno de (y, x)."""
    coluna = mask[:, x]
    c = y
    while c > 0 and coluna[c - 1]:
        c -= 1
    b = y
    n = len(coluna)
    while b < n - 1 and coluna[b + 1]:
        b += 1
    return c, b


def _quebrar(texto):
    """Parte o nome no espaco mais proximo do meio."""
    pos = [i for i, c in enumerate(texto) if c == " "]
    if not pos:
        return [texto]
    meio = len(texto) / 2
    i = min(pos, key=lambda p: abs(p - meio))
    return [texto[:i], texto[i+1:]]
