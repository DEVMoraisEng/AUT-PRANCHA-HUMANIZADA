"""
Monta a prancha final em PDF sobre o papel timbrado da Morais:
titulo + planta humanizada + fachada 3D + descricao + quadro de areas.
Todo numero que aparece aqui vem da planta. Nada e estimado.
"""
import re, io
import os

_AQUI = os.path.dirname(os.path.abspath(__file__))


def fonte_arquivo(negrito=False):
    """Acha a fonte: primeiro a que vem junto com o programa (funciona no
    navegador, onde nao existe /usr/share/fonts), depois a do sistema."""
    nome = "DejaVuSans-Bold.ttf" if negrito else "DejaVuSans.ttf"
    for cam in (os.path.join(_AQUI, "fontes", nome),
                os.path.join("/usr/share/fonts/truetype/dejavu", nome)):
        if os.path.exists(cam):
            return cam
    return nome

import numpy as np
import pymupdf
from PIL import Image
import fachada

NAVY = (44/255, 42/255, 90/255)
PETROL = (35/255, 94/255, 119/255)
MINT = (127/255, 207/255, 196/255)
CINZA = (0.42, 0.42, 0.45)

REG = fonte_arquivo(False)
BOLD = fonte_arquivo(True)

# "IMPERMEÁVEL" contem "PERMEÁVEL": o (?<!IM) evita o falso positivo
DESCOBERTO = r"(?<!IM)PERME[AÁ]VEL|GRAMA|QUINTAL"


def resumo(amb):
    """Le os nomes dos ambientes e monta a descricao da casa. So contagem."""
    nomes = [a["nome"].upper() for a in amb]
    quartos = sum(bool(re.search(r"QUARTO|DORM", n)) for n in nomes)
    suites = sum(bool(re.search(r"SU[IÍ]TE", n)) for n in nomes)
    banhos = sum(bool(re.search(r"BANHO|WC|LAVABO", n)) for n in nomes)
    linhas = []
    tot = quartos + suites
    if tot:
        t = f"{tot} QUARTO" + ("S" if tot > 1 else "")
        if suites:
            t += f" SENDO {suites} SUÍTE" + ("S" if suites > 1 else "")
        linhas.append(t)
    if banhos:
        linhas.append(f"{banhos} BANHEIRO" + ("S" if banhos > 1 else ""))
    if any("GOURMET" in n and "GARAGEM" in n for n in nomes):
        linhas.append("GARAGEM COM ÁREA GOURMET INTEGRADA")
    elif any("GOURMET" in n for n in nomes):
        linhas.append("ÁREA GOURMET")
    if any(re.search(r"JD\.|JARDIM", n) for n in nomes):
        linhas.append("JARDIM DE INVERNO")
    if any(re.search(r"SERVI[CÇ]O", n) for n in nomes):
        linhas.append("ÁREA DE SERVIÇO INDEPENDENTE")
    return linhas


def areas(amb, area_lote=None, area_construida=None, area_quintal=None):
    """Por padrao soma o que esta escrito na planta. Mas construida, quintal e
    lote sao numeros de projeto/matricula: se a pessoa informar, vale o que ela
    informou - a soma dos ambientes nem sempre bate com a area legal."""
    quintal = sum(a["area"] for a in amb if re.search(DESCOBERTO, a["nome"].upper()))
    construida = sum(a["area"] for a in amb) - quintal
    if area_construida:
        construida = float(area_construida)
    if area_quintal:
        quintal = float(area_quintal)
    saida = {"ÁREA CONSTRUÍDA": construida, "ÁREA DE QUINTAL": quintal}
    if area_lote:
        saida["ÁREA DO LOTE"] = float(area_lote)
    else:
        saida["SOMA DAS ÁREAS"] = construida + quintal
    return saida


def _bytes(img, largura_alvo=None, q=90):
    """Reamostra para a resolucao que a folha realmente usa e grava em JPEG.
    Sem isso o PDF final fica com dezenas de MB por nada."""
    if largura_alvo and img.width > largura_alvo:
        h = int(img.height * largura_alvo / img.width)
        img = img.resize((largura_alvo, h), Image.LANCZOS)
    b = io.BytesIO()
    img.convert("RGB").save(b, "JPEG", quality=q, subsampling=0, optimize=True)
    return b.getvalue()


def recortar(img, margem=18):
    a = np.array(img.convert("RGB"))
    nz = np.where(a.min(axis=2) < 248)
    y0, y1 = nz[0].min(), nz[0].max(); x0, x1 = nz[1].min(), nz[1].max()
    return img.crop((max(0, x0-margem), max(0, y0-margem),
                     min(img.width, x1+margem), min(img.height, y1+margem)))


def _cabe(w, h, cx, cy):
    """maior escala que cabe na caixa, mantendo proporcao"""
    k = min(cx / w, cy / h)
    return w * k, h * k


def _num(v):
    return f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") + " m²"


def _dir(pg, x_dir, y, txt, fonte, tam, cor):
    w = pymupdf.get_text_length(txt, fontname="helv", fontsize=tam)
    pg.insert_text((x_dir - w, y), txt, fontname=fonte, fontsize=tam, color=cor)


def _fundo_timbrado(pg, timbrado, pecas=None):
    """Pinta o papel timbrado. Se as pecas (logo e onda) estiverem disponiveis,
    monta a folha com elas em resolucao cheia; senao cai para a imagem de
    pagina inteira, que e mais fraca."""
    W, H = pg.rect.width, pg.rect.height
    if pecas and "logo" in pecas and "onda" in pecas:
        from timbrado import POS_LOGO, POS_REGUA_Y, POS_REGUA_X, POS_ONDA_Y
        pg.draw_rect(pg.rect, color=None, fill=(1, 1, 1))
        x0, y0, x1, y1 = POS_LOGO
        pg.insert_image(pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H),
                        filename=pecas["logo"], keep_proportion=True)
        pg.draw_line(pymupdf.Point(POS_REGUA_X * W, POS_REGUA_Y * H),
                     pymupdf.Point(W, POS_REGUA_Y * H), color=NAVY, width=1.1)
        pg.insert_image(pymupdf.Rect(0, POS_ONDA_Y * H, W, H),
                        filename=pecas["onda"], keep_proportion=False)
    else:
        pg.insert_image(pg.rect, stream=_bytes(Image.open(timbrado), 1414, q=94))


def montar(planta_img, tres_d_pdf, amb, titulo, saida, area_lote=None,
           timbrado="tb/word/media/image1.jpeg", humanizar_3d=True,
           area_construida=None, area_quintal=None, pecas=None):
    doc = pymupdf.open()
    pg = doc.new_page(width=595.276, height=841.89)
    W = pg.rect.width
    _fundo_timbrado(pg, timbrado, pecas)
    pg.insert_font(fontname="DJ", fontfile=REG)
    pg.insert_font(fontname="DJB", fontfile=BOLD)

    ML, MR = 52, 52
    topo = 203.0
    RODAPE = 655.0                       # onde a onda do timbrado comeca

    # ---------------- titulo (opcional) --------------------------------------
    # Sem titulo a prancha nao fica com um espaco vazio no topo: o conteudo sobe.
    if titulo and titulo.strip():
        pg.insert_text((ML, topo), titulo.upper(), fontname="DJB", fontsize=17, color=NAVY)
        pg.draw_line(pymupdf.Point(ML, topo + 9), pymupdf.Point(ML + 46, topo + 9),
                     color=MINT, width=2.6)
        pg.insert_text((ML, topo + 23), "PROJETO ARQUITETÔNICO  ·  PLANTA HUMANIZADA",
                       fontname="DJ", fontsize=7, color=CINZA)
        y = topo + 38
    else:
        y = topo - 12
    colL = ML
    largL = 258.0
    colR = ML + largL + 22
    largR = W - MR - colR

    # ---------------- planta humanizada -------------------------------------
    pl = recortar(planta_img)
    pw, ph = _cabe(pl.width, pl.height, largL, RODAPE - (y + 13))
    cx = colL + (largL - pw) / 2
    pg.insert_text((colL, y + 5), "PLANTA BAIXA HUMANIZADA", fontname="DJB",
                   fontsize=7.6, color=PETROL)
    pg.insert_image(pymupdf.Rect(cx, y + 13, cx + pw, y + 13 + ph),
                    stream=_bytes(pl, int(pw / 72 * 400)))

    # ---------------- fachada 3D --------------------------------------------
    if humanizar_3d:
        im3 = fachada.humanizar(tres_d_pdf, dpi=300)
    else:
        p3 = pymupdf.open(tres_d_pdf)[0].get_pixmap(dpi=260, colorspace=pymupdf.csRGB)
        im3 = recortar(Image.frombytes("RGB", (p3.width, p3.height), p3.samples), margem=8)
    tw, th = _cabe(im3.width, im3.height, largR, 178)
    tx = colR + (largR - tw) / 2
    pg.insert_text((colR, y + 5), "PERSPECTIVA / FACHADA", fontname="DJB",
                   fontsize=7.6, color=PETROL)
    pg.insert_image(pymupdf.Rect(tx, y + 13, tx + tw, y + 13 + th),
                    stream=_bytes(im3, int(tw / 72 * 400)))

    # ---------------- caracteristicas ---------------------------------------
    yd = y + 13 + th + 18
    pg.insert_text((colR, yd), "CARACTERÍSTICAS", fontname="DJB", fontsize=7.6, color=PETROL)
    yd += 12
    for ln in resumo(amb):
        pg.draw_circle(pymupdf.Point(colR + 2.4, yd - 2.6), 1.6, color=None, fill=MINT)
        pg.insert_text((colR + 10, yd), ln, fontname="DJ", fontsize=7,
                       color=(0.18, 0.18, 0.22))
        yd += 11.6

    # ---------------- resumo de areas ---------------------------------------
    yq = yd + 8
    A = areas(amb, area_lote, area_construida, area_quintal)
    alt = 15.0
    pg.draw_rect(pymupdf.Rect(colR, yq, colR + largR, yq + alt * len(A) + 7),
                 color=None, fill=(0.960, 0.969, 0.973))
    yy = yq + 12
    for i, (nome, v) in enumerate(A.items()):
        ult = i == len(A) - 1
        f = "DJB" if ult else "DJ"
        pg.insert_text((colR + 8, yy), nome, fontname=f, fontsize=6.8,
                       color=NAVY if ult else (0.25, 0.25, 0.3))
        _dir(pg, colR + largR - 8, yy, _num(v), f, 7.2, NAVY)
        if not ult:
            pg.draw_line(pymupdf.Point(colR + 8, yy + 4.6),
                         pymupdf.Point(colR + largR - 8, yy + 4.6),
                         color=(0.88, 0.90, 0.92), width=0.5)
        yy += alt
    yy += 14

    # ---------------- quadro de ambientes (2 colunas) ------------------------
    pg.insert_text((colR, yy), "QUADRO DE AMBIENTES", fontname="DJB", fontsize=7.6, color=PETROL)
    yy += 11
    itens = [a for a in sorted(amb, key=lambda a: -a["area"])
             if not a["nome"].upper().startswith("AMBIENTE")]
    linhas = (len(itens) + 1) // 2
    cw = largR / 2
    for n, a in enumerate(itens):
        col, lin = divmod(n, linhas)
        x = colR + col * cw
        ly = yy + lin * 8.8
        rot = a.get("rotulo") or a["nome"]
        nome = rot if len(rot) <= 21 else rot[:20] + "."
        pg.insert_text((x, ly), nome, fontname="DJ", fontsize=5.5, color=(0.32, 0.32, 0.36))
        _dir(pg, x + cw - 8, ly, f"{a['area']:.2f}".replace(".", ",") + " m²",
             "DJ", 5.5, PETROL)

    doc.save(saida)
    return saida
