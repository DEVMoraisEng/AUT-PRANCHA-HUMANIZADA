"""
Motor de humanizacao.
Repinta a planta original sem alterar um milimetro da geometria:
  - piso por acabamento, com a malha no MODULO REAL (50x50 cm na escala do desenho)
  - paredes cheias na cor da marca
  - mobiliario e loucas do projeto redesenhados como BLOCO SOLIDO com sombra
  - etiquetas NAO sao gravadas na imagem: saem como texto vetorial no PDF

O acabamento NAO e adivinhado a partir do desenho: ele vem de uma tabela
explicita (REGRAS) que o usuario ve e pode sobrescrever ambiente a ambiente.
O padrao de quem nao esta na tabela e concreto - o mais conservador. E area
dentro da casa que o programa nao conseguiu identificar TAMBEM sai concreto:
buraco branco no meio da planta nao e resultado, e defeito.
"""
import re
import numpy as np
import pymupdf
from scipy import ndimage
from PIL import Image

# --- identidade Morais -------------------------------------------------------
NAVY = (44, 42, 90)
PETROL = (35, 94, 119)
MINT = (127, 207, 196)

CRUZ = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)

# Luminancia a partir da qual o pixel deixa de ser traco de desenho.
# Trocar "o que nao e cor de ambiente" por "o que e ESCURO" foi o que devolveu
# o mobiliario: a regra antiga apagava por cor e, ao dilatar a mascara de cor
# do ambiente em 1 px, comia a linha fina do movel inteira.
LUM_TRACO = 165.0
LUM_CHEIO = 40.0

# Teto de pixels da imagem de saida (ver desenhar()).
TETO_PIXEL = 15_000_000

# --- acabamentos -------------------------------------------------------------
# 'modulo' e em METROS: a malha e desenhada na escala real do desenho,
# nao num passo qualquer de pixel.
PALETAS = {
    "neutra": {
        "externo":    dict(cor=(233, 232, 228), junta=None,             tipo="liso",  modulo=None),
        "ceramica50": dict(cor=(238, 236, 232), junta=(214, 211, 205), tipo="malha", modulo=0.50),
        "concreto":   dict(cor=(224, 223, 219), junta=None,             tipo="liso",  modulo=None),
        "grama":      dict(cor=(219, 225, 212), junta=(198, 208, 190),  tipo="grama", modulo=None),
        "_copa":      (152, 164, 145),
    },
    "cor": {
        "externo":    dict(cor=(222, 221, 216), junta=None,             tipo="liso",  modulo=None),
        "ceramica50": dict(cor=(236, 228, 214), junta=(205, 192, 170),  tipo="malha", modulo=0.50),
        "concreto":   dict(cor=(212, 210, 204), junta=None,             tipo="liso",  modulo=None),
        "grama":      dict(cor=(190, 214, 166), junta=(172, 199, 145),  tipo="grama", modulo=None),
        "_copa":      (108, 146, 92),
    },
}

# --- mobiliario --------------------------------------------------------------
# Tom do bloco pelo TAMANHO da peca. O desenho do Revit e monocromatico: nao da
# para saber pela cor se aquilo e uma cama ou uma pia. Pelo tamanho da, e e o
# que um projetista faria a mao - cama e sofa em tom de estofado, mesa e
# eletro em tom de madeira clara, louca em branco.
MOVEIS = [
    (1.30, (206, 197, 186)),    # cama de casal, sofa, carro, bancada grande
    (0.30, (214, 200, 178)),    # mesa, poltrona, fogao, geladeira, armario
    (0.00, (246, 246, 243)),    # louca: vaso, pia, cuba, tanque
]
MOVEL_MIN_M2 = 0.020    # menor que isso e ruido de traco
MOVEL_MAX_M2 = 8.0      # maior que isso e o comodo, nao um movel

# --- acabamentos por ambiente ------------------------------------------------
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
        larg = max(1.0, 1.15 * esc)                # espessura da junta
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


def _luminancia(img):
    return 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]


def _malha_do_revit(orig):
    """A malha de piso que o Revit ja desenha (linhas finas esverdeadas).
    Sai do desenho: quem manda na malha e o acabamento definido na tabela."""
    r, g, b = (orig[:, :, i] for i in range(3))
    return (np.abs(g - b) <= 8) & ((g - r) > 12) & (g < 246)


def _anotacao(orig):
    """Linhas de anotacao do Revit (separador de ambiente em verde, eixo em azul).
    Sao marcacoes de modelagem, nao fazem parte da arquitetura -> saem.
    Marrom de movel tem o VERMELHO dominante e por isso fica."""
    mx = orig.max(axis=2); mn = orig.min(axis=2)
    return ((mx - mn) > 45) & (np.argmax(orig, axis=2) != 0)


def _tapar_furos(m, area_max):
    """Fecha buraco pequeno dentro da mascara (frestas da hachura de parede)."""
    buracos = ndimage.binary_fill_holes(m) & ~m
    lab, n = ndimage.label(buracos)
    if not n:
        return m
    tam = np.bincount(lab.ravel())
    pequenos = np.zeros(n + 1, bool)
    pequenos[1:] = tam[1:] <= area_max
    return m | pequenos[lab]


def _recorte(P, margem_m=0.35):
    """Retangulo (em px do dpi de analise) que contem o desenho, com folga.
    Fora dele so ha papel branco."""
    img = P["img"]
    tinta = img.min(axis=2) < 248
    if not tinta.any():
        return 0, 0, img.shape[1], img.shape[0]
    ys = np.nonzero(tinta.any(axis=1))[0]
    xs = np.nonzero(tinta.any(axis=0))[0]
    mg = max(4, int(margem_m * float(np.sqrt(P["k"]))))
    return (max(0, int(xs.min()) - mg), max(0, int(ys.min()) - mg),
            min(img.shape[1], int(xs.max()) + mg + 1),
            min(img.shape[0], int(ys.max()) + mg + 1))


def _caixas_de_texto(page, dpi, ox, oy, folga=2):
    for w in page.get_text("words"):
        x0, y0, x1, y1 = [v * dpi / 72.0 for v in w[:4]]
        yield (max(0, int(y0 - oy) - folga), int(y1 - oy) + folga,
               max(0, int(x0 - ox) - folga), int(x1 - ox) + folga)


def _apagar_textos(alvo, page, dpi, ox, oy):
    """Tira da imagem o texto que ja estava no PDF do Revit. Ele volta depois,
    como texto vetorial, na hora de montar a prancha."""
    for y0, y1, x0, x1 in _caixas_de_texto(page, dpi, ox, oy):
        alvo[y0:y1, x0:x1] = 255


def _apagar_marca(mascara, page, dpi, ox, oy):
    """Mesma coisa, para mascara booleana."""
    for y0, y1, x0, x1 in _caixas_de_texto(page, dpi, ox, oy, folga=3):
        mascara[y0:y1, x0:x1] = False


def _tinta_do_desenho(orig, page, dpi, cores_ambiente, paredes, interior,
                      ox=0, oy=0):
    """O traco do projeto: mobiliario, louca, esquadria, escada.

    Fica so o que e ESCURO depois de tirar parede, cor de ambiente, malha de
    piso, anotacao de modelagem e o texto antigo. A regra e por luminancia
    justamente porque a linha do movel e preta e a cor do ambiente nunca e:
    assim a linha sobrevive inteira, com o antialias e tudo.
    """
    limpo = orig.copy()
    limpo[paredes] = 255
    limpo[_malha_do_revit(orig) & interior] = 255
    anot = _anotacao(orig)
    for rgb in cores_ambiente:
        anot &= ~_quase(orig, rgb, 34)
    limpo[ndimage.binary_dilation(anot, np.ones((3, 3)))] = 255
    # cor de ambiente e FUNDO, nao desenho. Apagada no valor exato (com folga
    # so para o antialias) e SEM dilatar: dilatar aqui apaga a linha do movel.
    for rgb in cores_ambiente:
        limpo[_quase(orig, rgb, 30)] = 255
    _apagar_textos(limpo, page, dpi, ox, oy)

    lum = _luminancia(limpo)
    return np.clip((LUM_TRACO - lum) / (LUM_TRACO - LUM_CHEIO), 0.0, 1.0)


# O contorno de mobiliario que o Revit desenha e CINZA (acromatico). A malha
# de piso do mesmo desenho e uma linha COLORIDA - um tom mais escuro da propria
# cor do ambiente. E essa a diferenca que separa uma coisa da outra, e nao o
# tamanho: num quarto de 3 m a linha da malha e a cabeceira da cama tem
# exatamente o mesmo comprimento (ja tentei por comprimento e apaguei a cama).
MOVEL_SAT_MAX = 38.0     # o quanto o traco de movel pode fugir do cinza
MOVEL_LUM_MAX = 205.0    # e o quanto ele pode ser claro


def _pegada_do_movel(orig, interior, px_por_m):
    """A pegada de cada peca de mobiliario, em pixel.

    O Revit desenha o movel como CONTORNO cinza por cima do ambiente pintado -
    a cama nao e um bloco cheio, e um retangulo vazado. Aqui esse contorno
    vira bloco: costura-se a linha, tapa-se o miolo e o resultado e a pegada
    da peca. Posicao e tamanho continuam sendo os do projeto.

    Arco de porta e linha de cota tambem sao cinza, mas nao cercam nada e nao
    tem corpo: nao viram bloco.
    """
    sat = orig.max(axis=2) - orig.min(axis=2)
    lum = _luminancia(orig)
    contorno = interior & (sat <= MOVEL_SAT_MAX) & (lum <= MOVEL_LUM_MAX)
    if not contorno.any():
        return contorno

    fio = max(3, int(round(0.045 * px_por_m))) | 1
    pegada = ndimage.binary_fill_holes(
        ndimage.binary_closing(contorno, np.ones((fio, fio))))

    # so fica o que tem CORPO: raspa 3,5 cm de cada lado e reconstroi o que
    # sobreviveu. Linha solta some, movel volta inteiro.
    raio = max(1, int(round(0.035 * px_por_m)))
    nucleo = ndimage.binary_erosion(pegada, np.ones((2 * raio + 1,) * 2))
    if not nucleo.any():
        return np.zeros_like(contorno)
    return ndimage.binary_propagation(nucleo, mask=pegada)


def _blocos_de_movel(orig, interior, px_por_m, esc):
    """Transforma o movel DESENHADO em bloco solido de biblioteca.

    Nao inventa movel nenhum: pegada, posicao e tamanho continuam sendo os do
    projeto. O que muda e o acabamento com que a peca e desenhada - tom de
    material pelo tamanho da peca, um leve volume e sombra de contato no piso.
    """
    H, W = interior.shape
    corpo = np.zeros((H, W, 3), np.float32)
    marca = np.zeros((H, W), bool)

    pegada = _pegada_do_movel(orig, interior, px_por_m)
    if not pegada.any():
        return corpo, marca

    m2 = px_por_m ** 2
    lab, n = ndimage.label(pegada)
    if not n:
        return corpo, marca
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        m = lab[sl] == i
        # buraco dentro da propria peca (o vao de uma pia, o miolo de um
        # armario) e peca, nao piso. So que tapar buraco nao pode dobrar a
        # peca: quando dobra, aquilo era um anel, nao um movel.
        cheia = ndimage.binary_fill_holes(m)
        if cheia.sum() <= m.sum() * 2.0:
            m = cheia
        area = m.sum() / m2
        if area < MOVEL_MIN_M2 or area > MOVEL_MAX_M2:
            continue
        cor = np.array(MOVEIS[-1][1], np.float32)
        for corte, tom in MOVEIS:
            if area >= corte:
                cor = np.array(tom, np.float32)
                break
        # leve volume: a peca fica mais clara no proprio centro
        d = ndimage.distance_transform_edt(m).astype(np.float32)
        vol = np.clip(d / max(2.0, 7.0 * esc), 0, 1)[:, :, None]
        alvo = corpo[sl]
        alvo[m] = (cor * (0.93 + 0.09 * vol))[m]
        corpo[sl] = alvo
        marca[sl] |= m

    return corpo, marca


def _sombra(marca, interior, esc):
    """Sombra de contato no piso, deslocada para baixo/direita."""
    if not marca.any():
        return None
    d = max(1, int(2.0 * esc))
    s = np.roll(np.roll(marca.astype(np.float32), d, axis=0), d, axis=1)
    s = ndimage.gaussian_filter(s, 1.9 * esc)
    return np.clip(s * 1.25, 0, 1) * (~marca) * interior


def desenhar(P, dpi_saida=300, paleta="neutra", override=None):
    """P vem de pipeline.preparar() ou cores.preparar().

    Devolve (PIL.Image, etiquetas). As etiquetas NAO sao gravadas na imagem:
    elas voltam como coordenada em pixel para o PDF escrever texto vetorial.
    Gravar o nome na imagem era a origem do "nome borrado": a imagem ainda
    seria reamostrada para caber na folha, e o texto ia junto.
    """
    page = P["page"]
    esc = dpi_saida / P["dpi"]
    px_por_m = float(np.sqrt(P["k"])) * esc

    # So o desenho vai para a alta resolucao. A folha A4 inteira a 600 dpi sao
    # 35 milhoes de pixels e o navegador nao aguenta; o desenho sozinho e uma
    # fracao disso. Recortar aqui e o que permite dobrar a resolucao de saida
    # sem estourar a memoria - e a margem branca ia ser cortada mais adiante
    # de qualquer jeito.
    cx0, cy0, cx1, cy1 = _recorte(P)
    # trava de memoria: o navegador nao aguenta um array float de mais de uns
    # 15 milhoes de pixels vezes as copias que o desenho usa. Planta gigante
    # perde um pouco de dpi em vez de derrubar a aba.
    px = (cx1 - cx0) * (cy1 - cy0) * (dpi_saida / P["dpi"]) ** 2
    if px > TETO_PIXEL:
        dpi_saida = max(P["dpi"], int(dpi_saida * (TETO_PIXEL / px) ** 0.5))
        esc = dpi_saida / P["dpi"]
        px_por_m = float(np.sqrt(P["k"])) * esc
    clip = pymupdf.Rect(cx0 * 72.0 / P["dpi"], cy0 * 72.0 / P["dpi"],
                        cx1 * 72.0 / P["dpi"], cy1 * 72.0 / P["dpi"])
    pix = page.get_pixmap(dpi=dpi_saida, colorspace=pymupdf.csRGB, clip=clip)
    orig = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).astype(np.float32)
    H, W = orig.shape[:2]
    ox, oy = float(pix.x), float(pix.y)      # origem do recorte, em px de saida

    def _corta(a):
        return a[cy0:cy1, cx0:cx1]

    seg = np.array(Image.fromarray(_corta(P["seg"]).astype(np.int32), "I")
                   .resize((W, H), Image.NEAREST))
    lote = np.array(Image.fromarray(_corta(P["lote"]).astype(np.uint8) * 255)
                    .resize((W, H), Image.NEAREST)) > 127
    r, g, b = (orig[:, :, i] for i in range(3))
    paredes = (r > 140) & (r - g > 55) & (r - b > 55)
    # planta pintada pelo Revit: cor de ambiente nao e parede, por mais
    # avermelhada que seja. Sem isto um ambiente rosa vira barreira.
    cores_ambiente = P.get("cores_ambiente", [])
    for rgb in cores_ambiente:
        paredes &= ~_quase(orig, rgb, 60)
    lado = max(3, int(5 * esc) | 1)
    par = ndimage.binary_closing(paredes, np.ones((lado, lado)))
    par = _tapar_furos(par, max(64, int(0.02 * P["k"] * esc * esc)))

    # a segmentacao vem de uma grade mais grossa; na resolucao de saida sobram
    # frestas. Cada fresta vai para o ambiente vizinho, respeitando parede e
    # soleira (o tampao de vao) para nao vazar de um comodo para o outro.
    tapa = np.array(Image.fromarray(_corta(P["tapa"]).astype(np.uint8) * 255)
                    .resize((W, H), Image.NEAREST)) > 127
    barreira = par | tapa
    livre = lote & ~barreira
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
    resto = tapa & ~par & (seg == 0)
    if resto.any():
        _, (iy, ix) = ndimage.distance_transform_edt(seg == 0, return_indices=True)
        seg[resto] = seg[iy[resto], ix[resto]]

    seg = _piso_nao_atravessa_parede(seg, livre)

    # ---------- 1. piso -------------------------------------------------------
    saida = np.full((H, W, 3), 255.0, np.float32)
    cache = {}

    def tex(mat, seed=0):
        if mat not in cache:
            cache[mat] = textura((H, W), mat, px_por_m, seed=seed,
                                 paleta=paleta, esc=esc)
        return cache[mat]

    for i, a in enumerate(P["amb"], 1):
        mask = seg == i
        if not mask.any():
            continue
        # ambiente que nao passou na conferencia nao ganha o acabamento dele,
        # mas TAMBEM nao fica branco: leva concreto, o piso mais conservador.
        mat = material_de(a["nome"], override) if a.get("confiavel", True) else PADRAO
        saida[mask] = tex(mat, seed=i)[mask]

    # ---------- 1b. o que sobrou DENTRO da casa tambem e piso -----------------
    # Era aqui que aparecia o buraco branco: trecho fechado por parede que o
    # programa nao conseguiu casar com nenhum ambiente da ficha. Sem nome ele
    # nao ganha etiqueta nem entra no quadro de areas - mas ganha concreto,
    # porque piso branco no meio da planta le como erro de impressao.
    # O envelope e o que as PAREDES cercam, nao so o que os ambientes cobrem:
    # um comodo que a ficha nao trouxe fica fora do 'lote' e saia branco no
    # meio da casa. Fechando pelas paredes ele entra e leva concreto.
    envelope = ndimage.binary_fill_holes(lote | par)
    sobra = envelope & ~par & (seg == 0)
    if sobra.any():
        saida[sobra] = tex(PADRAO)[sobra]
    interior = (seg > 0) | sobra

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
    tinta = _tinta_do_desenho(orig, page, dpi_saida, cores_ambiente, paredes,
                              interior, ox, oy)
    tinta[copa] = 0.0

    # ---------- 4. mobiliario como bloco solido -------------------------------
    # Jardim nao tem movel: o que ha ali e a pontilhado de grama, que fechado
    # vira uma mancha solida e ja saiu como "bloco de movel" cobrindo o
    # canteiro inteiro. Area de grama fica de fora da busca.
    dentro_limpo = interior & ~par & ~copa & ~verde_amb
    _apagar_marca(dentro_limpo, page, dpi_saida, ox, oy)   # texto nao e movel
    corpo, marca = _blocos_de_movel(orig, dentro_limpo, px_por_m, esc)
    s = _sombra(marca, interior, esc)
    if s is not None:
        saida = saida * (1 - 0.16 * s)[:, :, None]
    if marca.any():
        saida = np.where(marca[:, :, None], corpo, saida)

    # ---------- 5. o traco por cima do bloco ---------------------------------
    # Traco fino fica firme; bloco chapado preto entra mais leve, senao a peca
    # vira mancha preta na folha.
    cheio = ndimage.binary_dilation(
        ndimage.binary_erosion(tinta > 0.55, np.ones((int(5 * esc) | 1,) * 2)),
        np.ones((int(7 * esc) | 1,) * 2))
    peso = np.where(cheio, 0.42, 0.88)
    escurece = np.clip(tinta * peso, 0, 1)[:, :, None]
    alvo = np.minimum(saida, np.array([70.0, 68.0, 82.0]))
    saida = np.where(interior[:, :, None],
                     saida * (1 - escurece) + alvo * escurece, saida)

    # ---------- 6. o que esta FORA da casa fica como esta ---------------------
    # Calcada, rua, cota, arvore de fachada: sem ambiente com nome e area o
    # programa NAO repinta e NAO apaga. Copia o desenho como veio.
    fora = ~interior & ~par
    if fora.any():
        original = orig.copy()
        original[paredes] = 255
        original[ndimage.binary_dilation(_anotacao(orig), np.ones((3, 3)))] = 255
        _apagar_textos(original, page, dpi_saida, ox, oy)
        saida[fora] = original[fora]

    # ---------- 7. paredes ----------------------------------------------------
    saida[par] = NAVY

    img = Image.fromarray(np.clip(saida, 0, 255).astype(np.uint8))
    ocupado = (tinta > 0.20) | marca | par
    etiquetas = posicionar(P, dpi_saida, seg, interior & ~ocupado, px_por_m)
    return img, etiquetas


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


# --------------------------------------------------------------------------- #
#  Etiquetas
# --------------------------------------------------------------------------- #
def posicionar(P, dpi, seg, livre, px_por_m, passo=4):
    """Acha ONDE cabe a etiqueta de cada ambiente. Nao desenha nada.

    Devolve, por ambiente: o ponto (em pixel da imagem gerada), a largura util
    ali e a altura util. Quem escreve e o PDF, em texto vetorial - por isso
    aqui nao se escolhe fonte nem tamanho, so lugar.

    'Onde cabe' = ponto do ambiente mais distante de parede, movel e das
    etiquetas ja colocadas. Os ambientes sao atendidos do maior para o menor,
    entao o comodo grande escolhe primeiro e o pequeno se ajeita no que sobrou.
    """
    peq_seg = seg[::passo, ::passo]
    ocupado = ~livre[::passo, ::passo]
    saida = []

    for idx, a in sorted(enumerate(P["amb"], 1), key=lambda t: -t[1]["area"]):
        nome = (a.get("rotulo") or a["nome"] or "").upper().strip()
        if not nome or nome.startswith("AMBIENTE"):
            continue
        if a["area"] and a["area"] < 0.8:
            continue

        m = (peq_seg == idx) & ~ocupado
        if not m.any():                     # comodo tomado por movel: usa o miolo
            m = (peq_seg == idx)
        if not m.any():
            continue
        d = ndimage.distance_transform_edt(m)
        yy, xx = np.unravel_index(int(np.argmax(d)), d.shape)
        raio = float(d[yy, xx])
        e, dd = _vao_horizontal(m, yy, xx)
        vao = (dd - e + 1) * passo
        cx = (e + dd + 1) / 2.0 * passo
        cy = float(yy * passo)

        # reserva o espaco: a proxima etiqueta nao pode cair em cima desta
        rh = max(4, int(raio))
        rw = max(4, int(vao / passo / 2))
        ocupado[max(0, yy - rh):yy + rh + 1, max(0, xx - rw):xx + rw + 1] = True

        saida.append({
            "idx": idx,
            "nome": nome,
            "area": float(a["area"] or 0.0),
            "x": float(cx),
            "y": float(cy),
            "vao": float(vao),
            "raio": float(raio * passo),
            "confiavel": bool(a.get("confiavel", True)),
        })
    return saida


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


def quebrar(texto, maximo=2):
    """Parte o nome no espaco mais proximo do meio."""
    pos = [i for i, c in enumerate(texto) if c == " "]
    if not pos or maximo < 2:
        return [texto]
    meio = len(texto) / 2
    i = min(pos, key=lambda p: abs(p - meio))
    return [texto[:i], texto[i + 1:]]
