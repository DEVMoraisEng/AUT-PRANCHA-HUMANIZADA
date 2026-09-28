"""
Humanizacao da perspectiva 3D.

Dois modos:
  - "viva" (padrao desde 28/09): cor de material em cima do 3D do Revit -
    parede em branco quente, telhado no tom escolhido (o do Revit, ceramica
    ou grafite), vidro azulado, o que ja tinha cor (tijolo, madeira) mais
    vivo, e ceu em degrade atras. Mesma geometria, sem redesenhar nada.
  - "sobria": o tratamento antigo (rampa de cinza quente).
Se a pessoa mandar um RENDER em imagem (JPG/PNG do Enscape, Lumion, Revit
Realista...), ele entra como veio - so recorta a margem e acerta o contraste.
Render de verdade sempre ganha de recolorir o 3D chapado.

O 3D sai do Revit com material chapado, preto duro e fundo branco. Aqui ele
recebe tratamento de apresentacao SEM redesenhar nada:
  - os tons do Revit passam por uma rampa quente e sobria (nada de cor forte)
  - o preto vira grafite: o volume para de pesar na folha
  - o fundo branco vira um degrade suave, com sombra de contato embaixo da casa
A geometria e exatamente a mesma que saiu do modelo.
"""
import numpy as np
import pymupdf
from scipy import ndimage
from PIL import Image

# rampa de tons: luminancia do Revit -> cinza quente de apresentacao
RAMPA = [
    (0,   (84, 82, 88)),
    (40,  (104, 101, 105)),
    (90,  (137, 134, 134)),
    (140, (174, 171, 167)),
    (195, (215, 212, 206)),
    (235, (238, 236, 230)),
    (255, (252, 251, 248)),
]

FUNDO_TOPO = (245, 244, 242)
FUNDO_BASE = (255, 255, 255)


def _lut():
    xs = np.array([p[0] for p in RAMPA], np.float32)
    out = np.zeros((256, 3), np.float32)
    for c in range(3):
        ys = np.array([p[1][c] for p in RAMPA], np.float32)
        out[:, c] = np.interp(np.arange(256), xs, ys)
    return out


def _renderizar(pdf, largura_alvo=2400, dpi_min=150, dpi_max=520):
    """Rende a pagina na resolucao em que a CASA - nao a folha - fica grande.

    O Revit exporta a perspectiva no meio de uma folha A4: em 300 dpi fixos a
    casa costuma ocupar um terco da imagem e chega na prancha com menos da
    metade da resolucao util. Aqui a primeira passada so mede onde a casa
    esta; a segunda rende so o necessario para ela sair com ~2400 px de
    largura. Mesma geometria, o dobro de nitidez, sem estourar memoria.
    """
    doc = pymupdf.open(pdf)
    pg = doc[0]
    pix = pg.get_pixmap(dpi=90, colorspace=pymupdf.csRGB)
    peq = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    tinta = peq.min(axis=2) <= 246
    if tinta.any():
        xs = np.nonzero(tinta.any(axis=0))[0]
        larg_obj = max(1, int(xs.max() - xs.min() + 1))
        dpi = 90.0 * largura_alvo / larg_obj
    else:
        dpi = 300.0
    dpi = float(min(dpi_max, max(dpi_min, dpi)))
    pix = pg.get_pixmap(dpi=int(round(dpi)), colorspace=pymupdf.csRGB)
    return np.frombuffer(pix.samples, np.uint8).reshape(
        pix.height, pix.width, 3).astype(np.float32)


def humanizar(pdf, dpi=300, dessaturacao=0.75, sombra=0.30, margem=26, luz=0.06):
    img = _renderizar(pdf)
    margem = int(margem * img.shape[1] / 2480.0) or 1

    # ---- 1. separa o objeto do fundo ---------------------------------------
    claro = img.min(axis=2) > 246
    lab, _ = ndimage.label(claro)
    borda = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    borda.discard(0)
    fundo = np.isin(lab, list(borda))
    objeto = ~fundo
    if not objeto.any():
        return Image.fromarray(img.astype(np.uint8))

    ys, xs = np.nonzero(objeto)
    baixo = int(margem * 3.2)          # espaco para a sombra de contato aparecer
    y0, y1 = max(0, ys.min() - margem), min(img.shape[0], ys.max() + baixo)
    x0, x1 = max(0, xs.min() - margem), min(img.shape[1], xs.max() + margem)

    # ---- 2. rampa de tons ---------------------------------------------------
    lut = _lut()
    lum = (0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2])
    novo = lut[np.clip(lum, 0, 255).astype(np.uint8)]

    # o que tinha cor (vidro, tijolo, grama) guarda um resto de matiz
    sat = (img.max(axis=2) - img.min(axis=2))[:, :, None]
    peso = np.clip(sat / 70.0, 0, 1) * (1 - dessaturacao)
    matiz = novo + (img - lum[:, :, None])
    novo = novo * (1 - peso) + matiz * peso

    # ---- 3. fundo em degrade + sombra de contato ---------------------------
    H, W = img.shape[:2]
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    grad = (np.array(FUNDO_TOPO, np.float32) * (1 - t)
            + np.array(FUNDO_BASE, np.float32) * t)
    tela = np.repeat(grad, W, axis=1)

    silhueta = ndimage.binary_fill_holes(objeto).astype(np.float32)
    desl = int(0.020 * (ys.max() - ys.min()))
    s = np.roll(silhueta, desl, axis=0)
    s = ndimage.gaussian_filter(s, sigma=0.030 * (ys.max() - ys.min()))
    s = np.clip(s * 1.6, 0, 1) * fundo
    tela *= (1 - sombra * s)[:, :, None]

    # luz de estudio: leve gradiente diagonal, so para o volume nao ficar chapado
    gy = np.linspace(-1, 1, H, dtype=np.float32)[:, None]
    gx = np.linspace(-1, 1, W, dtype=np.float32)[None, :]
    ganho = 1.0 + luz * (-(gy * 0.75 + gx * 0.55) / 1.3)
    novo = novo * ganho[:, :, None]

    saida = np.where(objeto[:, :, None], novo, tela)[y0:y1, x0:x1]
    obj = objeto[y0:y1, x0:x1]

    # o fundo se dissolve em branco nas bordas: colado no timbrado nao aparece
    # emenda de retangulo, so a sombra sob a casa
    h, w = saida.shape[:2]
    fy = np.clip(np.minimum(np.arange(h), h - 1 - np.arange(h)) / (0.12 * h), 0, 1)[:, None]
    fx = np.clip(np.minimum(np.arange(w), w - 1 - np.arange(w)) / (0.12 * w), 0, 1)[None, :]
    f = fy * fx
    f = f * f * (3 - 2 * f)
    f = np.where(obj, 1.0, f)[:, :, None]
    saida = 255.0 - (255.0 - saida) * f
    return Image.fromarray(np.clip(saida, 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------- #
#  Modo "viva"
# --------------------------------------------------------------------------- #
TELHADOS = {
    # tom escuro -> tom claro da telha, interpolado pela luminancia do Revit
    "revit":    ((88, 92, 102),  (176, 180, 188)),     # o cinza do modelo, so mais rico
    "ceramica": ((118, 54, 34),  (214, 126, 84)),
    "grafite":  ((48, 50, 56),   (128, 132, 140)),
}
PAREDE_SOMBRA = (190, 180, 166)
PAREDE_LUZ = (253, 250, 243)
VIDRO = (58, 88, 118)
CEU_TOPO = (150, 196, 232)
CEU_BASE = (236, 244, 250)


def _misturar(a, b, t):
    a = np.array(a, np.float32); b = np.array(b, np.float32)
    t = np.clip(t, 0, 1)[..., None]
    return a * (1 - t) + b * t


def humanizar_viva(pdf, telhado="revit", margem=26):
    img = _renderizar(pdf)
    margem = int(margem * img.shape[1] / 2480.0) or 1

    claro = img.min(axis=2) > 246
    lab, _ = ndimage.label(claro)
    borda = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]])))
    borda.discard(0)
    fundo = np.isin(lab, list(borda))
    objeto = ~fundo
    if not objeto.any():
        return Image.fromarray(img.astype(np.uint8))

    ys, xs = np.nonzero(objeto)
    alt_obj = ys.max() - ys.min()
    baixo = int(margem * 3.2)
    y0, y1 = max(0, ys.min() - margem), min(img.shape[0], ys.max() + baixo)
    x0, x1 = max(0, xs.min() - margem), min(img.shape[1], xs.max() + margem)
    img = img[y0:y1, x0:x1]
    objeto = objeto[y0:y1, x0:x1]
    fundo = ~objeto
    H, W = img.shape[:2]

    lum = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    sat = img.max(axis=2) - img.min(axis=2)
    cromatico = (sat > 24) & objeto
    neutro = objeto & ~cromatico

    novo = img.copy()
    # ---- parede: branco quente, a sombra do Revit vira sombra quente ------
    parede = neutro & (lum >= 168)
    t = (lum - 168) / (250 - 168)
    novo[parede] = _misturar(PAREDE_SOMBRA, PAREDE_LUZ, t)[parede]
    # ---- telhado: faixa media, em manchas grandes (telha tem desenho, por
    # isso a mancha e fechada antes de medir) --------------------------------
    meio = neutro & (lum >= 62) & (lum < 168)
    k = max(3, int(0.004 * W)) | 1
    grande = ndimage.binary_closing(meio, np.ones((k, k)))
    # linha de contorno tambem cai na faixa media e, fechada, gruda no
    # telhado: abre com um elemento maior que a espessura da linha
    ka = max(3, int(0.007 * W)) | 1
    grande = ndimage.binary_opening(grande, np.ones((ka, ka)))
    labm, nm = ndimage.label(grande)
    if nm:
        tam = np.bincount(labm.ravel())
        minimo = 0.004 * objeto.sum()
        ok = np.zeros(nm + 1, bool)
        ok[1:] = tam[1:] >= minimo
        # telhado e mancha "gorda"; meio-fio, pilar e rufo sao compridos e
        # finos - pela espessura eles ficam de fora
        gordura = 0.022 * W
        for i, sl in enumerate(ndimage.find_objects(labm), 1):
            if sl is None or not ok[i]:
                continue
            m = np.pad(labm[sl] == i, 1)
            if 2.0 * ndimage.distance_transform_edt(m).max() < gordura:
                ok[i] = False
        telha = ok[labm] & meio
        telha = ndimage.binary_dilation(ok[labm], np.ones((3, 3))) & meio
    else:
        telha = np.zeros_like(meio)
    escuro, claro_t = TELHADOS.get(telhado, TELHADOS["revit"])
    t = (lum - 62) / (168 - 62)
    novo[telha] = _misturar(escuro, claro_t, t)[telha]
    # o que sobrou na faixa media (esquadria, peitoril, vao pequeno)
    resto = meio & ~telha
    novo[resto] = (img[resto] * 0.85 + np.array((120, 112, 104), np.float32) * 0.15)
    # ---- vao escuro = vidro -------------------------------------------------
    vidro = neutro & (lum < 62)
    t = lum / 62.0
    novo[vidro] = _misturar((34, 52, 72), VIDRO, t)[vidro]
    # ---- o que ja tinha cor fica mais vivo ---------------------------------
    if cromatico.any():
        l3 = lum[:, :, None]
        viv = l3 + (img - l3) * 1.45
        novo[cromatico] = viv[cromatico]
    # linha de contorno do Revit (preto duro) vira grafite
    contorno = objeto & (lum < 30)
    novo[contorno] = (58, 58, 64)

    # ---- ceu + sombra de apoio ---------------------------------------------
    tt = np.linspace(0, 1, H, dtype=np.float32)[:, None]
    ceu = _misturar(CEU_TOPO, CEU_BASE, np.repeat(tt ** 0.8, W, axis=1))
    silhueta = ndimage.binary_fill_holes(objeto).astype(np.float32)
    desl = int(0.020 * alt_obj)
    s = np.roll(silhueta, desl, axis=0)
    s = ndimage.gaussian_filter(s, sigma=0.030 * alt_obj)
    s = np.clip(s * 1.6, 0, 1) * fundo
    ceu *= (1 - 0.28 * s)[:, :, None]

    # luz de fim de tarde: leve ganho quente de cima a esquerda
    gy = np.linspace(-1, 1, H, dtype=np.float32)[:, None]
    gx = np.linspace(-1, 1, W, dtype=np.float32)[None, :]
    ganho = 1.0 + 0.06 * (-(gy * 0.75 + gx * 0.55) / 1.3)
    novo = novo * ganho[:, :, None]

    saida = np.where(objeto[:, :, None], novo, ceu)
    # dissolve nas bordas: colado no timbrado nao aparece emenda
    fy = np.clip(np.minimum(np.arange(H), H - 1 - np.arange(H)) / (0.14 * H), 0, 1)[:, None]
    fx = np.clip(np.minimum(np.arange(W), W - 1 - np.arange(W)) / (0.14 * W), 0, 1)[None, :]
    f = fy * fx
    f = f * f * (3 - 2 * f)
    f = np.where(objeto, 1.0, f)[:, :, None]
    saida = 255.0 - (255.0 - saida) * f
    return Image.fromarray(np.clip(saida, 0, 255).astype(np.uint8))


_IMAGEM = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8")


def _eh_imagem(caminho):
    try:
        with open(caminho, "rb") as f:
            cab = f.read(4)
    except (IOError, OSError):
        return False
    return any(cab.startswith(m) for m in _IMAGEM)


def render_pronto(caminho):
    """Render que ja vem pronto (JPG/PNG): recorta a margem branca e acerta
    o contraste de leve. Nao recolore nada."""
    from PIL import ImageOps, ImageEnhance
    im = Image.open(caminho).convert("RGB")
    a = np.asarray(im)
    tinta = a.min(axis=2) < 246
    if tinta.any():
        ys = np.nonzero(tinta.any(axis=1))[0]; xs = np.nonzero(tinta.any(axis=0))[0]
        im = im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    if max(im.size) > 3000:
        k = 3000.0 / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    im = ImageOps.autocontrast(im, cutoff=0.4)
    return ImageEnhance.Color(im).enhance(1.06)


def carregar(caminho, humanizar=True, estilo="viva", telhado="revit"):
    """Porta de entrada da prancha: decide o que fazer com o arquivo do 3D."""
    if _eh_imagem(caminho):
        return render_pronto(caminho)
    if not humanizar:
        p3 = pymupdf.open(caminho)[0].get_pixmap(dpi=260, colorspace=pymupdf.csRGB)
        im = Image.frombytes("RGB", (p3.width, p3.height), p3.samples)
        a = np.asarray(im)
        tinta = a.min(axis=2) < 248
        if tinta.any():
            ys = np.nonzero(tinta.any(axis=1))[0]; xs = np.nonzero(tinta.any(axis=0))[0]
            im = im.crop((max(0, xs.min() - 8), max(0, ys.min() - 8),
                          min(im.width, xs.max() + 8), min(im.height, ys.max() + 8)))
        return im
    if estilo == "sobria":
        return humanizar_fn(caminho, dpi=300)
    return humanizar_viva(caminho, telhado=telhado)


humanizar_fn = humanizar
