"""
Motor de humanizacao.
Repinta a planta original sem alterar um milimetro da geometria:
  - piso por acabamento, com a malha no MODULO REAL (50x50 cm na escala do desenho)
  - estilo "viva" (padrao desde 28/09): porcelanato com variacao de tom por
    peca, grama de verdade, concreto com textura, paredes brancas com sombra
    projetada e sombreamento no pe da parede - o visual de planta renderizada
  - mobiliario e loucas do projeto renderizados com material (tecido, madeira,
    pedra, louca, pintura de carro), volume e sombra - na POSICAO e no TAMANHO
    do projeto; nada e inventado nem movido
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
# parede no estilo viva: topo branco + contorno grafite (planta renderizada)
PAREDE_TOPO = (246, 244, 240)
PAREDE_BORDA = (62, 60, 66)

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
    # Estilo planta renderizada (referencia: tipo-1-1400.jpg). Cores vivas mas
    # de material real: porcelanato greige, grama verde de jardim, concreto.
    "viva": {
        "externo":    dict(cor=(214, 208, 199), junta=None,             tipo="concreto",    modulo=None),
        "ceramica50": dict(cor=(224, 216, 204), junta=(199, 191, 180),  tipo="porcelanato", modulo=0.50),
        "concreto":   dict(cor=(192, 189, 183), junta=None,             tipo="concreto",    modulo=None),
        "grama":      dict(cor=(104, 148, 58),  junta=None,             tipo="gramado",     modulo=None),
        "_copa":      (62, 116, 44),
    },
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
MOVEL_MAX_M2 = 10.0     # maior que isso e o comodo, nao um movel (carro ~8 m2)

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


def _ruido(H, W, passo, seed=0):
    """Ruido suave em [-0.5, 0.5]: sorteia numa grade grossa e amplia.
    Ampliar e muito mais rapido que filtrar a imagem inteira - no navegador
    isso e a diferenca entre 1 s e 20 s numa planta de 15 Mpx."""
    rng = np.random.default_rng(seed)
    passo = max(1.0, float(passo))
    h = max(2, int(H / passo) + 2)
    w = max(2, int(W / passo) + 2)
    r = rng.random((h, w)).astype(np.float32)
    return np.array(Image.fromarray(r, "F").resize((W, H), Image.BICUBIC),
                    np.float32) - 0.5


def _fbm(H, W, passo, seed=0, oitavas=3):
    """Ruido em varias escalas (manchas grandes + detalhe)."""
    out = np.zeros((H, W), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(oitavas):
        out += amp * _ruido(H, W, passo / (2 ** o), seed + 17 * o)
        tot += amp
        amp *= 0.5
    return out / tot


def _desfoque(m, sigma):
    """Gaussiana rapida: acima de alguns pixels de raio, reduz, borra e volta.
    O resultado e o mesmo a olho e custa uma fracao."""
    m = m.astype(np.float32)
    if sigma <= 3:
        return ndimage.gaussian_filter(m, sigma)
    f = max(1, int(sigma / 2.5))
    H, W = m.shape
    peq = np.array(Image.fromarray(m, "F").resize(
        (max(1, W // f), max(1, H // f)), Image.BILINEAR), np.float32)
    peq = ndimage.gaussian_filter(peq, sigma / f)
    return np.array(Image.fromarray(peq, "F").resize((W, H), Image.BILINEAR),
                    np.float32)


def _deslocar(m, dy, dx):
    """Desloca uma mascara (sem dar a volta na borda, como o np.roll faz)."""
    out = np.zeros_like(m)
    H, W = m.shape[:2]
    ys, yd = (slice(0, H - dy), slice(dy, H)) if dy >= 0 else (slice(-dy, H), slice(0, H + dy))
    xs, xd = (slice(0, W - dx), slice(dx, W)) if dx >= 0 else (slice(-dx, W), slice(0, W + dx))
    out[yd, xd] = m[ys, xs]
    return out


def textura(shape, mat, px_por_m, seed=0, paleta="viva", esc=1.0):
    """Textura do acabamento desenhada em TODA a folha, em coordenada global.
    Como a malha e unica para a planta inteira, ela nao quebra na divisa
    entre um comodo e outro."""
    H, W = shape
    m = PALETAS[paleta][mat]
    cor = np.array(m["cor"], np.float32)
    base = np.empty((H, W, 3), np.float32)
    base[:] = cor

    if m["tipo"] == "liso":
        return base

    if m["tipo"] == "porcelanato":
        # peca de 50x50 na escala real, cada peca com um tom levemente
        # diferente (como porcelanato de verdade) + veio suave de marmore
        p = m["modulo"] * px_por_m
        rng = np.random.default_rng(seed + 101)
        ny, nx = int(H / p) + 2, int(W / p) + 2
        tom = (rng.random((ny, nx)).astype(np.float32) - 0.5) * 0.045
        iy = (np.arange(H) // p).astype(np.int32)
        ix = (np.arange(W) // p).astype(np.int32)
        var = tom[iy][:, ix]
        var += 0.030 * _fbm(H, W, 0.35 * px_por_m, seed + 7)
        base *= (1.0 + var)[:, :, None]
        larg = max(1.0, 0.006 * px_por_m)          # rejunte de ~3 mm (visual)
        yy = (np.arange(H, dtype=np.float32) % p)[:, None]
        xx = (np.arange(W, dtype=np.float32) % p)[None, :]
        marca = (yy < larg) | (xx < larg)
        base[marca] = np.array(m["junta"], np.float32)
        return base

    if m["tipo"] == "concreto":
        n = 0.035 * _fbm(H, W, 0.6 * px_por_m, seed + 3) \
            + 0.030 * _ruido(H, W, 1.2 * esc, seed + 5)
        base *= (1.0 + n)[:, :, None]
        return base

    if m["tipo"] == "gramado":
        # grama de jardim: manchas grandes (claro/escuro, puxando para o
        # amarelo) + folhinha fina. Verde vivo, sem virar verde de bandeira.
        grande = _fbm(H, W, 0.9 * px_por_m, seed + 11)
        fino = _ruido(H, W, 1.3 * esc, seed + 13) + 0.6 * _ruido(H, W, 3.0 * esc, seed + 19)
        base += grande[:, :, None] * np.array([34, 38, 12], np.float32)
        base += fino[:, :, None] * np.array([30, 40, 14], np.float32)
        return base

    # paletas antigas: malha simples ou grama pontilhada
    if m["tipo"] == "malha":
        p = m["modulo"] * px_por_m
        larg = max(1.0, 1.15 * esc)
        yy = np.arange(H)[:, None].astype(np.float32)
        xx = np.arange(W)[None, :].astype(np.float32)
        marca = ((yy % p) < larg) | ((xx % p) < larg)
    else:
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


def _miolos(fechado, interior, seg, par, px_por_m, fio):
    """Os miolos que o traco do movel cerca - contando a PAREDE como lado.

    Armario e bancada encostados na parede so tem tres lados desenhados; sem
    a parede o miolo nao fecha. So que com a parede o proprio COMODO tambem
    vira miolo. O que separa um do outro e o tamanho: miolo que ocupa mais da
    metade do comodo e o comodo (ou o que sobrou dele), nao um movel.
    Tambem fica de fora o quarto de circulo que o arco da porta faz com a
    parede - tem o tamanho de um criado-mudo, mas a forma entrega.
    """
    m2 = px_por_m ** 2
    perto = ndimage.binary_dilation(par, np.ones((fio, fio)))
    barreira = fechado | perto
    livre = interior & ~barreira
    lab, n = ndimage.label(livre)
    if not n:
        return fechado & interior
    tam_amb = np.bincount(seg.ravel())
    aceito = np.zeros(n + 1, bool)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        m = lab[sl] == i
        area_px = int(m.sum())
        area = area_px / m2
        if area > MOVEL_MAX_M2 or area < MOVEL_MIN_M2 * 0.5:
            continue
        ids = seg[sl][m]
        ids = ids[ids > 0]
        if len(ids) < 0.5 * area_px:
            continue            # fora de ambiente nomeado (escada, sobra): nao e movel
        dono = int(np.bincount(ids).argmax())
        if dono < len(tam_amb) and area_px > 0.55 * tam_amb[dono]:
            continue            # e o proprio comodo
        hh = (sl[0].stop - sl[0].start) / px_por_m
        ww = (sl[1].stop - sl[1].start) / px_por_m
        cheio = area / max(1e-6, hh * ww)
        aspecto = max(hh, ww) / max(1e-6, min(hh, ww))
        if 0.62 <= cheio <= 0.86 and aspecto < 1.35 and 0.50 <= max(hh, ww) <= 1.10:
            continue                                   # arco de porta
        # movel e retangulo (ou quase). Pedaco de piso que sobrou entre a
        # cama, o armario e a parede tem forma torta - e ele que nao entra.
        # Bancada em L entra pelo preenchimento de cor, logo abaixo.
        if cheio < 0.80 and area > 0.35:
            continue
        aceito[i] = True
    miolo = aceito[lab]
    # o contorno que cerca um miolo aceito faz parte da peca
    borda = ndimage.binary_dilation(miolo, np.ones((fio + 2, fio + 2))) & fechado
    return (miolo | borda) & interior


def _pegada_do_movel(orig, interior, px_por_m, cores_ambiente=None, lum_max=None,
                     seg=None, par=None):
    """A pegada de cada peca de mobiliario, em pixel.

    O Revit desenha o movel como CONTORNO cinza por cima do ambiente pintado -
    a cama nao e um bloco cheio, e um retangulo vazado. Aqui esse contorno
    vira bloco: costura-se a linha, tapa-se o miolo e o resultado e a pegada
    da peca. Posicao e tamanho continuam sendo os do projeto.

    Peca que o Revit PREENCHEU com cor (sofa marrom, bancada bege, TV azul)
    entra pelo preenchimento: nem sempre o contorno dela fecha - a bancada
    encostada na parede so tem tres lados desenhados.

    Arco de porta e linha de cota tambem sao cinza, mas nao cercam nada e nao
    tem corpo: nao viram bloco.
    """
    sat = orig.max(axis=2) - orig.min(axis=2)
    lum = _luminancia(orig)
    if lum_max is None:
        # sem ficha o piso e branco e a malha do Revit e CINZA: acima de ~190
        # ela entra como contorno, fecha com a parede e o "movel" vira a casa
        # inteira. Com ficha a malha e colorida e o limite antigo vale.
        lum_max = MOVEL_LUM_MAX if cores_ambiente else 188.0
    contorno = interior & (sat <= MOVEL_SAT_MAX) & (lum <= lum_max)
    if not contorno.any():
        return contorno

    fio = max(3, int(round(0.045 * px_por_m))) | 1
    fechado = ndimage.binary_closing(contorno, np.ones((fio, fio)))
    if seg is None or par is None:
        pegada = ndimage.binary_fill_holes(fechado)
    else:
        pegada = _miolos(fechado, interior, seg, par, px_por_m, fio)

    if cores_ambiente is not None:
        cheio = interior & (sat > 18) & (lum < 236)
        for rgb in cores_ambiente:
            cheio &= ~_quase(orig, rgb, 30)
        cheio &= ~_malha_do_revit(orig)
        # so o que tem corpo: linha colorida (malha, separador) tem 1-2 px
        r = max(1, int(round(0.03 * px_por_m)))
        cheio = ndimage.binary_opening(cheio, np.ones((2 * r + 1,) * 2))
        pegada |= cheio

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


# --------------------------------------------------------------------------- #
#  Mobiliario renderizado
# --------------------------------------------------------------------------- #
# Material de cada peca. So entra quando o Revit desenhou a peca SEM cor
# (contorno sobre branco). Peca que o Revit ja pintou (sofa marrom, armario
# escuro, bancada bege) fica com a cor do projeto - so ganha volume e textura.
MATERIAL = {
    #  classe      cor principal       cor secundaria      textura   altura(m) relevo
    "cama":     ((208, 194, 174), (247, 245, 240), "tecido",  0.07, 1.00),
    "estofado": ((188, 181, 171), (205, 198, 188), "tecido",  0.08, 1.00),
    "armario":  ((172, 128, 88),  (182, 138, 98),  "madeira", 0.16, 0.45),
    "mesa":     ((188, 146, 104), (214, 206, 196), "madeira", 0.06, 0.40),
    "bancada":  ((229, 226, 220), (236, 234, 230), "pedra",   0.11, 0.35),
    "louca":    ((250, 250, 248), (236, 238, 240), "liso",    0.05, 1.10),
    "carro":    ((190, 196, 204), (62, 72, 86),    "pintura", 0.14, 0.90),
    "pequeno":  ((208, 198, 186), (224, 216, 206), "liso",    0.05, 0.60),
}

_R_BANHO = r"BANH|WC|LAVABO|SANIT"
_R_MOLHADO = r"BANH|WC|LAVABO|SANIT|SERVI[CÇ]O|LAVAND|COZINHA|COPA|GOURMET"
_R_QUARTO = r"QUARTO|SU[IÍ]TE|DORM"
_R_SALA = r"SALA|ESTAR|TV|HOME"


def _classe(ambiente, area, curto, longo):
    """Que peca e essa? Pelo AMBIENTE onde ela esta + as MEDIDAS dela.
    O desenho do Revit nao diz se aquilo e cama ou mesa; o comodo e o
    tamanho dizem, e e o que um projetista faria a mao."""
    n = (ambiente or "").upper()
    if re.search(r"GARAGEM", n) and area >= 4.0:
        return "carro"
    if area < 0.34 and curto < 0.72:
        return "louca" if re.search(_R_MOLHADO, n) else "pequeno"
    if re.search(_R_QUARTO, n):
        if curto >= 0.78 and longo >= 1.70:
            return "cama"
        if curto <= 0.72 and longo >= 0.90:
            return "armario"
    if re.search(_R_SALA, n) and area >= 0.60 and curto >= 0.55:
        return "estofado"
    if re.search(_R_MOLHADO, n) and 0.35 <= curto <= 0.78:
        return "bancada"
    if curto <= 0.66 and longo >= 1.00:
        return "armario"
    return "mesa"


def _saturar(c, k):
    lum = (0.299 * c[..., 0] + 0.587 * c[..., 1] + 0.114 * c[..., 2])[..., None]
    return lum + (c - lum) * k


def _grao(h, w, horizontal, seed):
    """Veio de madeira: ruido esticado ao longo do comprimento da peca."""
    rng = np.random.default_rng(seed)
    if horizontal:
        r = rng.random((max(2, h // 2), max(2, w // 40))).astype(np.float32)
    else:
        r = rng.random((max(2, h // 40), max(2, w // 2))).astype(np.float32)
    return np.array(Image.fromarray(r, "F").resize((w, h), Image.BICUBIC),
                    np.float32) - 0.5


def _relevo(sub, raio, forca):
    """Volume de almofada: cada pedaco que o desenho fecha (travesseiro,
    assento, porta de armario) sobe no meio e cai na borda, com luz vindo
    de cima a esquerda. Devolve o fator multiplicador de brilho."""
    # a borda do recorte conta como borda da peca (sem o pad a peca que
    # encosta no limite do recorte fica sem chanfro daquele lado)
    d = ndimage.distance_transform_edt(np.pad(sub, 1))[1:-1, 1:-1].astype(np.float32)
    h = np.clip(d / max(1.0, raio), 0, 1)
    h = h * h * (3 - 2 * h)
    gy, gx = np.gradient(h)
    luz = -(gx * 0.70 + gy * 0.72) * raio * 0.28 * forca
    return (0.91 + 0.09 * h) * (1.0 + luz)


def _mobiliar(orig, saida, dentro, seg, par, amb, px_por_m, esc, cores_ambiente):
    """Renderiza cada peca de mobiliario do projeto.

    Pegada, posicao e tamanho continuam sendo EXATAMENTE os do projeto. O que
    muda e o acabamento: material pela classe da peca (ou a cor que o proprio
    Revit deu a ela), volume por pedaco fechado pelo desenho, as linhas do
    projeto viram costura/vinco, e a peca projeta sombra no piso.
    Devolve (saida, marca, sombra) - a sombra ja vem aplicada no piso.
    """
    H, W = dentro.shape
    marca = np.zeros((H, W), bool)
    pegada = _pegada_do_movel(orig, dentro, px_por_m, cores_ambiente=list(cores_ambiente),
                              seg=seg, par=par)
    if not pegada.any():
        return saida, marca

    m2 = px_por_m ** 2
    lum_all = _luminancia(orig)
    sat_all = orig.max(axis=2) - orig.min(axis=2)
    lab, n = ndimage.label(pegada)
    pecas = []
    sombra = np.zeros((H, W), np.float32)
    for i, sl in enumerate(ndimage.find_objects(lab), 1):
        if sl is None:
            continue
        m = lab[sl] == i
        cheia = ndimage.binary_fill_holes(m)
        if cheia.sum() <= m.sum() * 2.0:
            m = cheia
        area = m.sum() / m2
        if area < MOVEL_MIN_M2 or area > MOVEL_MAX_M2:
            continue
        hh = (sl[0].stop - sl[0].start) / px_por_m
        ww = (sl[1].stop - sl[1].start) / px_por_m
        # espessura real (a bancada em L tem caixa grande e 60 cm de tampo)
        esp = 2.0 * float(ndimage.distance_transform_edt(np.pad(m, 1)).max()) / px_por_m
        curto, longo = min(hh, ww, esp), max(hh, ww)
        ids = seg[sl][m]
        ids = ids[ids > 0]
        nome = amb[int(np.bincount(ids).argmax()) - 1]["nome"] if len(ids) else ""
        classe = _classe(nome, area, curto, longo)
        pecas.append((sl, m, classe, ww >= hh))
        # sombra: quanto mais alta a peca, mais longe a sombra cai
        alt = MATERIAL[classe][3]
        dy = int(round(alt * 0.9 * px_por_m)); dx = int(round(alt * 0.6 * px_por_m))
        # escreve a pegada ja deslocada, so no recorte (nada de folha inteira
        # por peca - no navegador isso estoura a memoria)
        y0, x0 = sl[0].start + dy, sl[1].start + dx
        y1, x1 = min(H, y0 + m.shape[0]), min(W, x0 + m.shape[1])
        if y0 < H and x0 < W:
            sombra[y0:y1, x0:x1] = np.maximum(sombra[y0:y1, x0:x1],
                                              m[:y1 - y0, :x1 - x0].astype(np.float32))
        marca[sl] |= m

    if not pecas:
        return saida, marca
    sombra = _desfoque(sombra, 0.035 * px_por_m) * (~marca)
    saida = saida * (1.0 - 0.30 * np.clip(sombra, 0, 1))[:, :, None]

    for k, (sl, m, classe, horizontal) in enumerate(pecas):
        cor1, cor2, tex, alt, forca = MATERIAL[classe]
        o = orig[sl]
        lum = lum_all[sl]; sat = sat_all[sl]
        h, w = m.shape
        # o traco do desenho dentro da peca (contorno, travesseiro, vinco)
        linha = m & (lum < 185) & (sat < 45)
        peso_linha = np.clip((190.0 - lum) / 120.0, 0, 1) * linha
        sub = m & ~ndimage.binary_dilation(linha, np.ones((3, 3)))
        # a peca ja veio pintada pelo Revit? (sofa marrom, armario escuro...)
        fundo = sub.copy()
        for rgb in cores_ambiente:
            fundo &= ~_quase(o, rgb, 30)
        pintado = fundo & ((sat > 20) | (lum < 150)) & ~((lum > 238) & (sat < 12))
        usa_revit = fundo.any() and pintado.sum() > 0.35 * max(1, fundo.sum())

        cor = np.empty((h, w, 3), np.float32)
        if usa_revit:
            base = ndimage.median_filter(o, size=(3, 3, 1)) if min(h, w) > 6 else o.copy()
            base = _saturar(base, 1.22)
            lb = _luminancia(base)
            # preto chapado pesa na folha: sobe para um tom de material escuro
            escuro = (lb < 95)[:, :, None]
            base = np.where(escuro, base * 0.55 + np.array(cor1, np.float32) * 0.45, base)
            # o que ficou branco dentro da peca (cuba, tampo) vira louca
            branco = ~pintado & m
            base[branco] = np.array((246, 246, 244), np.float32)
            cor[:] = base
            # peca "curinga" pintada de marrom pelo Revit e madeira
            hue_madeira = (o[..., 0] > o[..., 2] + 18) & (lum < 200)
            if classe in ("mesa", "pequeno", "armario") and hue_madeira[m].mean() > 0.5:
                tex = "madeira"
        else:
            cor[:] = np.array(cor1, np.float32)
            # pedacos pequenos da peca levam a cor secundaria: travesseiro na
            # cama, vidro no carro, tampo na mesa
            lb, nb = ndimage.label(sub)
            if nb > 1:
                tam = np.bincount(lb.ravel())
                limite = 0.22 * m.sum()
                secund = np.zeros(nb + 1, bool)
                secund[1:] = tam[1:] < limite
                if classe == "mesa":            # cadeiras em volta da mesa
                    secund[1:] = tam[1:] < 0.5 * tam[1:].max()
                cor[secund[lb]] = np.array(cor2, np.float32)

        # textura do material
        if tex == "madeira":
            g = _grao(h, w, horizontal, 300 + k)
            cor *= (1.0 + 0.10 * g)[:, :, None]
        elif tex == "tecido":
            cor *= (1.0 + 0.035 * _ruido(h, w, 1.5 * esc, 400 + k))[:, :, None]
        elif tex == "pedra":
            cor *= (1.0 + 0.05 * _ruido(h, w, 1.2 * esc, 500 + k)
                    + 0.03 * _ruido(h, w, 6 * esc, 600 + k))[:, :, None]
        elif tex == "pintura":
            t = np.linspace(-1, 1, w if horizontal else h, dtype=np.float32)
            faixa = np.exp(-((t - 0.1) ** 2) / 0.08)
            faixa = faixa[None, :] if horizontal else faixa[:, None]
            cor *= (0.92 + 0.16 * faixa)[:, :, None]

        # volume
        raio = max(2.0, 0.05 * px_por_m)
        cor *= _relevo(sub | (m & ~linha), raio, forca)[:, :, None]
        # o traco do projeto vira vinco/costura: mais escuro que o material,
        # nunca preto chapado
        cor *= (1.0 - 0.42 * peso_linha)[:, :, None]
        borda = m & ~ndimage.binary_erosion(m, np.ones((3, 3)))
        cor[borda] *= 0.72

        alvo = saida[sl]
        alvo[m] = np.clip(cor, 0, 255)[m]
        saida[sl] = alvo
    return saida, marca


def _sombra(marca, interior, esc):
    """Sombra de contato no piso, deslocada para baixo/direita."""
    if not marca.any():
        return None
    d = max(1, int(2.0 * esc))
    s = np.roll(np.roll(marca.astype(np.float32), d, axis=0), d, axis=1)
    s = ndimage.gaussian_filter(s, 1.9 * esc)
    return np.clip(s * 1.25, 0, 1) * (~marca) * interior


def desenhar(P, dpi_saida=300, paleta="viva", override=None):
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
    # Trecho fechado por parede que nao casou com nenhum ambiente: sem nome
    # ele nao ganha etiqueta - mas ganha concreto, porque piso branco no meio
    # da planta le como erro de impressao. O envelope e o que as PAREDES
    # cercam, nao so o que os ambientes cobrem.
    envelope = ndimage.binary_fill_holes(lote | par)
    sobra = envelope & ~par & (seg == 0)
    if sobra.any():
        saida[sobra] = tex(PADRAO)[sobra]
    interior = (seg > 0) | sobra
    cache.clear()

    viva = paleta == "viva"
    verde_amb = np.zeros((H, W), bool)
    for i, a in enumerate(P["amb"], 1):
        if a.get("confiavel", True) and material_de(a["nome"], override) == "grama":
            verde_amb |= (seg == i)

    # ---------- 1c. luz: o piso nao e chapado ---------------------------------
    # Luz ambiente irregular (manchas grandes e suaves) + sombreamento no pe
    # da parede. E o que tira a cara de "cor de preenchimento" do piso.
    if viva:
        luz = 1.0 + 0.055 * _fbm(H, W, 1.6 * px_por_m, 77, oitavas=2)
        pe = _desfoque(par, 0.05 * px_por_m)
        luz *= 1.0 - 0.30 * np.clip(pe * 1.6, 0, 1) * ~par
        # sombra projetada pela parede (sol alto, vindo de cima a esquerda)
        dy = int(round(0.11 * px_por_m)); dx = int(round(0.07 * px_por_m))
        proj = _desfoque(_deslocar(par.astype(np.float32), dy, dx), 0.045 * px_por_m)
        luz *= 1.0 - 0.26 * np.clip(proj, 0, 1) * ~par
        saida = np.where(interior[:, :, None], saida * luz[:, :, None], saida)
        del luz, pe, proj

    # ---------- 2. vegetacao: os circulos de arvore que ja existem no projeto --
    arvore = _quase(orig, (127, 127, 127), 2)
    copa = ndimage.binary_opening(
        arvore & ndimage.binary_dilation(verde_amb, np.ones((5, 5))), np.ones((9, 9)))
    if copa.any():
        cv = np.array(PALETAS[paleta]["_copa"], np.float32)
        if viva:
            # sombra da copa no gramado, depois folhagem com volume
            s = _desfoque(_deslocar(copa.astype(np.float32),
                                    int(0.35 * px_por_m), int(0.25 * px_por_m)),
                          0.12 * px_por_m)
            saida *= (1.0 - 0.35 * np.clip(s, 0, 1) * ~copa)[:, :, None]
            folha = _fbm(H, W, 0.10 * px_por_m, 23) + 0.5 * _ruido(H, W, 2.2 * esc, 29)
            verde = np.empty((H, W, 3), np.float32)
            verde[:] = cv
            verde += folha[:, :, None] * np.array([46, 62, 26], np.float32)
            verde *= _relevo(copa, 0.6 * px_por_m, 1.6)[:, :, None]
        else:
            rng = np.random.default_rng(11)
            ruido = ndimage.gaussian_filter(rng.random((H, W)).astype(np.float32), 2.0)
            verde = np.stack([np.full((H, W), float(c)) for c in cv], -1)
            verde += (ruido[:, :, None] - 0.5) * 34
            dc = ndimage.distance_transform_edt(copa).astype(np.float32)
            verde *= (0.82 + 0.26 * np.clip(dc / (34 * esc), 0, 1))[:, :, None]
        saida[copa] = verde[copa]
        del verde

    # ---------- 3. traco original (portas, janelas, loucas soltas) -------------
    tinta = _tinta_do_desenho(orig, page, dpi_saida, cores_ambiente, paredes,
                              interior, ox, oy)
    tinta[copa] = 0.0

    # ---------- 4. mobiliario -------------------------------------------------
    # Jardim nao tem movel: o que ha ali e pontilhado de grama, que fechado
    # vira mancha solida. Area de grama fica de fora da busca.
    dentro_limpo = interior & ~par & ~copa & ~verde_amb
    _apagar_marca(dentro_limpo, page, dpi_saida, ox, oy)   # texto nao e movel
    if viva:
        saida, marca = _mobiliar(orig, saida, dentro_limpo, seg, par, P["amb"],
                                 px_por_m, esc, cores_ambiente)
    else:
        corpo, marca = _blocos_de_movel(orig, dentro_limpo, px_por_m, esc)
        s = _sombra(marca, interior, esc)
        if s is not None:
            saida = saida * (1 - 0.16 * s)[:, :, None]
        if marca.any():
            saida = np.where(marca[:, :, None], corpo, saida)

    # ---------- 5. o traco por cima -------------------------------------------
    # No estilo viva o movel ja trouxe o proprio traco (virou vinco). Fora
    # dele sobram porta, janela e linha de louca solta: entram em grafite.
    cheio = ndimage.binary_dilation(
        ndimage.binary_erosion(tinta > 0.55, np.ones((int(5 * esc) | 1,) * 2)),
        np.ones((int(7 * esc) | 1,) * 2))
    peso = np.where(cheio, 0.42, 0.88 if not viva else 0.80)
    if viva:
        peso = np.where(marca, 0.0, peso)
    escurece = np.clip(tinta * peso, 0, 1)[:, :, None]
    alvo = np.minimum(saida, np.array([70.0, 68.0, 82.0] if not viva else [74.0, 70.0, 66.0]))
    saida = np.where(interior[:, :, None],
                     saida * (1 - escurece) + alvo * escurece, saida)
    del escurece, alvo

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
        del original

    # ---------- 7. paredes ----------------------------------------------------
    if viva:
        # parede branca com contorno grafite: o padrao de planta renderizada
        saida[par] = PAREDE_TOPO
        esp = max(1, int(round(0.8 * esc)))
        borda = par & ~ndimage.binary_erosion(par, np.ones((3, 3)), iterations=esp)
        saida[borda] = PAREDE_BORDA
    else:
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
