"""
Humanizacao da perspectiva 3D.

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


def humanizar(pdf, dpi=300, dessaturacao=0.75, sombra=0.30, margem=26, luz=0.06,
              fundo_transparente=True):
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
        modo = "RGBA" if fundo_transparente else "RGB"
        return Image.fromarray(img.astype(np.uint8)).convert(modo)

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

    # ---- 3. sombra de contato -------------------------------------------
    H, W = img.shape[:2]
    silhueta = ndimage.binary_fill_holes(objeto).astype(np.float32)
    desl = int(0.020 * (ys.max() - ys.min()))
    s = np.roll(silhueta, desl, axis=0)
    s = ndimage.gaussian_filter(s, sigma=0.030 * (ys.max() - ys.min()))
    s = np.clip(s * 1.6, 0, 1) * fundo

    # luz de estudio: leve gradiente diagonal, so para o volume nao ficar chapado
    gy = np.linspace(-1, 1, H, dtype=np.float32)[:, None]
    gx = np.linspace(-1, 1, W, dtype=np.float32)[None, :]
    ganho = 1.0 + luz * (-(gy * 0.75 + gx * 0.55) / 1.3)
    novo = novo * ganho[:, :, None]

    if fundo_transparente:
        # sem degrade nenhum: o volume fica opaco, a sombra de contato vira
        # uma mancha cinza translucida (alfa = intensidade da sombra), e o
        # resto e alfa=0 - cola direto no timbrado sem caixa branca por tras.
        alfa = np.clip(np.where(objeto, 1.0, sombra * s), 0, 1)
        alfa = ndimage.gaussian_filter(alfa, sigma=1.0)
        cinza_sombra = np.array([60, 58, 62], np.float32)
        cor = np.where(objeto[:, :, None], novo, cinza_sombra)
        saida = cor[y0:y1, x0:x1]
        alfa = alfa[y0:y1, x0:x1]
        rgba = np.dstack([np.clip(saida, 0, 255), np.clip(alfa * 255.0, 0, 255)])
        return Image.fromarray(rgba.astype(np.uint8), "RGBA")

    # ---- caminho antigo: fundo em degrade opaco, para quem preferir -------
    t = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
    grad = (np.array(FUNDO_TOPO, np.float32) * (1 - t)
            + np.array(FUNDO_BASE, np.float32) * t)
    tela = np.repeat(grad, W, axis=1)
    tela *= (1 - sombra * s)[:, :, None]

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
    return Image.fromarray(np.clip(saida, 0, 255).astype(np.uint8), "RGB")
