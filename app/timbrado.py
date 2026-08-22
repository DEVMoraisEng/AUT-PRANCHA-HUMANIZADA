"""Extrai as pecas do papel timbrado direto do .docx da empresa."""
import zipfile, io, os
import numpy as np
from PIL import Image


def extrair(docx, destino="timbrado_fundo.jpg"):
    """Pega a maior imagem de pagina inteira dentro do .docx (A4)."""
    melhor, area = None, 0
    with zipfile.ZipFile(docx) as z:
        for nome in z.namelist():
            if not nome.startswith("word/media/"):
                continue
            dados = z.read(nome)
            try:
                im = Image.open(io.BytesIO(dados))
            except Exception:
                continue
            prop = im.width / im.height
            if abs(prop - 210 / 297) < 0.03 and im.width * im.height > area:
                melhor, area = dados, im.width * im.height
    if melhor is None:
        raise RuntimeError("nao achei a arte de pagina inteira no .docx")
    open(destino, "wb").write(melhor)
    return destino


# Onde cada peca fica na folha A4, medido no proprio timbrado do .docx.
# Fracao da largura/altura da pagina.
POS_LOGO = (0.3642, 0.0400, 0.6351, 0.1750)
POS_REGUA_Y = 0.2185
POS_REGUA_X = 0.0990
POS_ONDA_Y = 0.7965


def pecas(docx, destino="."):
    """Devolve {logo, onda} com os PNGs originais, em resolucao cheia.

    Montar a folha a partir das pecas em vez de usar o JPEG da pagina inteira:
    o JPEG tem 1414 px de largura (uns 170 dpi em A4) e o logo sai borrado na
    impressao. As pecas sao PNG e entram nitidas.
    """
    saida = {}
    with zipfile.ZipFile(docx) as z:
        cands = []
        for nome in z.namelist():
            if not nome.startswith("word/media/"):
                continue
            dados = z.read(nome)
            try:
                im = Image.open(io.BytesIO(dados))
            except Exception:
                continue
            cands.append((nome, dados, im.width / im.height, im.width * im.height))
    for nome, dados, prop, tam in cands:
        if 1.2 < prop < 1.7 and "logo" not in saida:          # logo: quase quadrado
            saida["logo"] = _gravar(dados, destino, "timbrado_logo.png", recortar=True)
        elif prop > 2.4 and "onda" not in saida:              # onda: bem deitada
            saida["onda"] = _gravar(dados, destino, "timbrado_onda.png", recortar=True)
    return saida


def _gravar(dados, destino, nome, recortar=False):
    """recortar: tira a moldura branca/transparente da peca. Sem isso a onda
    do rodape fica com uma faixa branca nas laterais e nao sangra ate a borda."""
    cam = os.path.join(destino, nome)
    if recortar:
        im = Image.open(io.BytesIO(dados)).convert("RGBA")
        a = np.array(im)
        cheio = (a[:, :, 3] > 128) & (a[:, :, :3].min(axis=2) < 235)
        if cheio.any():
            ys, xs = np.nonzero(cheio)
            # 2 px de folga para dentro: a borda antialiasada e quase branca e
            # deixaria uma listra clara quando a peca sangra ate o papel
            im = im.crop((int(xs.min()) + 2, int(ys.min()),
                          int(xs.max()) - 1, int(ys.max()) - 1))
        im.save(cam)
        return cam
    with open(cam, "wb") as f:
        f.write(dados)
    return cam
