"""
Monta a prancha final em PDF sobre o papel timbrado da Morais:
titulo + planta humanizada + fachada 3D + descricao + resumo de areas.
(O quadro de ambientes saiu em 28/09: as areas ja estao na planta.)
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
MINT_ESC = (78/255, 158/255, 150/255)
CINZA = (0.42, 0.42, 0.45)

REG = fonte_arquivo(False)
BOLD = fonte_arquivo(True)

# "IMPERMEÁVEL" contem "PERMEÁVEL": o (?<!IM) evita o falso positivo
DESCOBERTO = r"(?<!IM)PERME[AÁ]VEL|GRAMA|QUINTAL"


_CHURRAS = re.compile(r"CHURRASQ")


def tem_churrasqueira(amb, textos=()):
    """A churrasqueira raramente e AMBIENTE no Revit - costuma ser familia com
    texto/etiqueta. Procura nos nomes e em todo texto escrito na planta."""
    alvo = " ".join([a.get("nome", "") for a in amb] + list(textos or ())).upper()
    return bool(_CHURRAS.search(alvo))


def _plural(n, sing, plur):
    return f"{n} {sing if n == 1 else plur}"


def resumo(amb, churrasqueira=None, textos=()):
    """Descricao comercial da casa, montada SO a partir do que a planta tem.

    Cada linha sai de um nome de ambiente (ou de um texto da planta, no caso
    da churrasqueira). Nada e suposto: sem ambiente GOURMET nao sai "gourmet".
    A pessoa ainda revisa e edita tudo na tela antes de gerar.
    """
    nomes = [" ".join(a["nome"].upper().split()) for a in amb]
    if churrasqueira is None:
        churrasqueira = tem_churrasqueira(amb, textos)

    def tem(padrao, excluir=None):
        return any(re.search(padrao, n) and not (excluir and re.search(excluir, n))
                   for n in nomes)

    linhas = []
    # ---- quartos ------------------------------------------------------------
    suites = sum(bool(re.search(r"SU[IÍ]TE", n)) for n in nomes)
    quartos = sum(bool(re.search(r"QUARTO|DORM", n)) and not re.search(r"SU[IÍ]TE", n)
                  for n in nomes)
    tot = quartos + suites
    if tot and suites == tot:
        linhas.append(_plural(suites, "SUÍTE", "SUÍTES"))
    elif tot:
        t = _plural(tot, "QUARTO", "QUARTOS")
        if suites:
            t += " SENDO " + _plural(suites, "SUÍTE", "SUÍTES")
        linhas.append(t)
    # ---- banheiros ----------------------------------------------------------
    banhos = sum(bool(re.search(r"BANH|\bWC\b|SANIT", n)) for n in nomes)
    lavabos = sum(bool(re.search(r"LAVABO", n)) for n in nomes)
    if banhos:
        t = _plural(banhos, "BANHEIRO", "BANHEIROS")
        if lavabos:
            t += " + LAVABO"
        linhas.append(t)
    elif lavabos:
        linhas.append("LAVABO")
    # ---- social -------------------------------------------------------------
    if tem(r"SALA.*COZINHA|COZINHA.*SALA|COZINHA AMERICANA|CONCEITO ABERTO"):
        linhas.append("SALA E COZINHA INTEGRADAS")
    # ---- gourmet / churrasqueira --------------------------------------------
    com_ch = " COM CHURRASQUEIRA" if churrasqueira else ""
    if tem(r"GARAGEM.*GOURMET|GOURMET.*GARAGEM"):
        linhas.append("GARAGEM GOURMET" + com_ch if churrasqueira
                      else "GARAGEM COM ÁREA GOURMET INTEGRADA")
    elif tem(r"VARANDA.*GOURMET|GOURMET.*VARANDA"):
        linhas.append("COM VARANDA GOURMET" + (" E CHURRASQUEIRA" if churrasqueira else ""))
    elif tem(r"GOURMET", excluir=r"SERVI[CÇ]O"):
        linhas.append("COM ÁREA GOURMET" + (" E CHURRASQUEIRA" if churrasqueira else ""))
    elif tem(r"SERVI[CÇ]O.*GOURMET"):
        linhas.append("ÁREA DE SERVIÇO E GOURMET" + com_ch)
    elif churrasqueira:
        linhas.append("COM CHURRASQUEIRA")
    # ---- demais -------------------------------------------------------------
    if tem(r"SERVI[CÇ]O|LAVAND", excluir=r"GOURMET"):
        linhas.append("ÁREA DE SERVIÇO INDEPENDENTE")
    if tem(r"GARAGEM", excluir=r"GOURMET"):
        linhas.append("GARAGEM DESCOBERTA" if tem(r"GARAGEM.*DESCOBERT")
                      else "GARAGEM COBERTA")
    if tem(r"VARANDA", excluir=r"GOURMET"):
        linhas.append("VARANDA")
    if tem(r"JD\.|JARDIM DE INVERNO"):
        linhas.append("JARDIM DE INVERNO")
    if tem(r"CLOSET"):
        linhas.append("SUÍTE COM CLOSET")
    if tem(r"ESCRIT|OFFICE"):
        linhas.append("ESCRITÓRIO")
    if tem(r"DESPENSA"):
        linhas.append("DESPENSA")
    if tem(r"PISCINA"):
        linhas.append("PISCINA")
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


def _bytes(img, largura_alvo=None, q=94, sem_perda=False):
    """Reamostra para a resolucao que a folha realmente usa e grava a imagem.

    A PLANTA vai SEM PERDA (PNG). O JPEG e feito para foto: em desenho de
    linha ele espalha um halo cinza em volta de cada traco, e era exatamente
    isso que deixava a planta com cara de "borrada" na prancha. A perspectiva,
    que e imagem continua, continua em JPEG de alta qualidade porque ali o
    JPEG nao aparece e o arquivo fica dez vezes menor.
    """
    if largura_alvo and img.width > largura_alvo:
        h = max(1, int(img.height * largura_alvo / img.width))
        img = img.resize((largura_alvo, h), Image.LANCZOS)
    b = io.BytesIO()
    if sem_perda or img.mode == "RGBA":
        img.save(b, "PNG", optimize=True)
    else:
        img.convert("RGB").save(b, "JPEG", quality=q, subsampling=0, optimize=True)
    return b.getvalue()


def _caixa(img, margem=18):
    """Retangulo do conteudo (sem a margem branca em volta)."""
    if img.mode == "RGBA":
        nz = np.where(np.array(img.getchannel("A")) > 8)
    else:
        nz = np.where(np.array(img.convert("RGB")).min(axis=2) < 248)
    if not len(nz[0]):
        return (0, 0, img.width, img.height)
    y0, y1 = nz[0].min(), nz[0].max()
    x0, x1 = nz[1].min(), nz[1].max()
    return (max(0, int(x0 - margem)), max(0, int(y0 - margem)),
            min(img.width, int(x1 + margem)), min(img.height, int(y1 + margem)))


def recortar(img, margem=18):
    """Corta a margem em branco ao redor do desenho."""
    return img.crop(_caixa(img, margem))


def _cabe(w, h, cx, cy):
    """maior escala que cabe na caixa, mantendo proporcao"""
    k = min(cx / w, cy / h)
    return w * k, h * k


def _num(v):
    return f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".") + " m²"


def _dir(pg, x_dir, y, txt, fonte, tam, cor):
    # mede com a fonte que vai ser usada (DejaVu), nao com a Helvetica
    w = _larg(txt, fonte == "DJB", tam)
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


try:
    _F_REG = pymupdf.Font(fontfile=REG)
    _F_BOLD = pymupdf.Font(fontfile=BOLD)
except Exception:                                   # pragma: no cover
    _F_REG = _F_BOLD = None


def _larg(txt, negrito, tam):
    f = _F_BOLD if negrito else _F_REG
    if f is not None:
        return f.text_length(txt, fontsize=tam)
    return pymupdf.get_text_length(txt, fontname="hebo" if negrito else "helv",
                                   fontsize=tam) * 1.06


# Tamanhos das etiquetas, em PONTOS da folha. Sao fixos de proposito: era a
# falta disso que fazia cada comodo sair com um corpo de letra diferente.
ET_NOME = 6.4
ET_AREA = 5.3
ET_NOME_MIN = 5.0
ET_AREA_MIN = 4.3
ET_CHAMADA_NOME = 6.2      # etiqueta puxada para fora, com linha de chamada
ET_CHAMADA_AREA = 5.2


def _placa(pg, x0, y0, x1, y1):
    """Chapa clara sob a etiqueta. DESLIGADA desde 28/09 (fundo transparente,
    como na planta renderizada de referencia); fica aqui so para quem chamar
    _escrever(..., placa=True) de proposito."""
    pg.draw_rect(pymupdf.Rect(x0, y0, x1, y1), color=None, fill=(1, 1, 1),
                 fill_opacity=0.68)


def _escrever(pg, cx, topo, nome_linhas, area_txt, tn, ta, placa=False):
    """Bloco nome + area centrado em cx, comecando em 'topo'. Devolve a altura."""
    gap = tn * 0.20
    larguras = [_larg(t, True, tn) for t in nome_linhas]
    wa = _larg(area_txt, False, ta) if area_txt else 0
    alt = len(nome_linhas) * (tn + gap) + ta
    if placa:
        w = max(larguras + [wa]) / 2 + tn * 0.42
        _placa(pg, cx - w, topo - tn * 0.20, cx + w, topo + alt + ta * 0.35)
    y = topo + tn
    for t, w in zip(nome_linhas, larguras):
        pg.insert_text((cx - w / 2, y), t, fontname="DJB", fontsize=tn, color=NAVY)
        y += tn + gap
    if area_txt:
        pg.insert_text((cx - wa / 2, y), area_txt, fontname="DJ", fontsize=ta,
                       color=PETROL)
    return alt


def _cabe_dentro(nome, area_txt, vao):
    """Maior par de tamanhos que faz a etiqueta caber no vao do comodo.
    Devolve (linhas, tam_nome, tam_area) ou None se nem no menor corpo cabe."""
    import humanizar
    for k in (1.0, 0.92, 0.85, ET_NOME_MIN / ET_NOME):
        tn, ta = ET_NOME * k, max(ET_AREA_MIN, ET_AREA * k)
        for linhas in ([nome], humanizar.quebrar(nome)):
            if max(_larg(t, True, tn) for t in linhas) <= vao * 0.92 \
               and _larg(area_txt, False, ta) <= vao * 0.92:
                return linhas, tn, ta
    return None


def _bate(r, ocupados, folga=1.2):
    x0, y0, x1, y1 = r
    for a0, b0, a1, b1 in ocupados:
        if x0 < a1 + folga and a0 < x1 + folga and y0 < b1 + folga and b0 < y1 + folga:
            return True
    return False


def _chamadas(pg, pendentes, x_esq, x_dir, img_x0, img_x1, y_topo, y_base):
    """Etiqueta que nao cabe no comodo sai para a margem, com linha de chamada.

    E o que um projetista faz a mao quando o comodo e estreito demais para o
    nome. O texto vai para o lado com mais espaco livre, sempre no mesmo corpo,
    e uma linha fina liga o texto ao ponto do ambiente. Nenhum nome fica
    ilegivel e nenhum nome cai no comodo errado.
    """
    import humanizar
    folga = 5.0
    # O texto fica FORA do desenho, sempre: a margem da coluna e dele, o
    # miolo e da planta. Antes o texto entrava na planta quando o nome era
    # comprido, e tapava justamente o comodo que estava nomeando.
    espaco = {"e": (img_x0 - folga) - x_esq, "d": x_dir - (img_x1 + folga)}
    lados = {"e": [], "d": []}
    for p in pendentes:
        lado = p["lado"]
        if espaco[lado] < 34 and espaco["e" if lado == "d" else "d"] >= 34:
            lado = "e" if lado == "d" else "d"
        lados[lado].append(p)

    for lado, itens in lados.items():
        itens.sort(key=lambda p: p["py"])
        usados = []
        disp = max(28.0, espaco[lado])
        for p in itens:
            tn, ta = ET_CHAMADA_NOME, ET_CHAMADA_AREA
            linhas = [p["nome"]]
            larg = _larg(p["nome"], True, tn)
            if larg > disp:
                linhas = humanizar.quebrar(p["nome"])
                larg = max(_larg(t, True, tn) for t in linhas)
            while larg > disp and tn > 4.6:        # nome unico e comprido
                tn, ta = tn - 0.4, ta - 0.35
                larg = max(_larg(t, True, tn) for t in linhas)
            alt = len(linhas) * (tn + tn * 0.20) + ta

            y = min(max(p["py"] - alt / 2, y_topo), y_base - alt)
            for a, b in usados:                    # nao empilha em cima da outra
                if y < b and y + alt > a:
                    y = b + 2.4
            y = min(y, y_base - alt)
            usados.append((y, y + alt))

            if lado == "e":
                cx = img_x0 - folga - larg / 2
                fim = cx + larg / 2 + 2.5
            else:
                cx = img_x1 + folga + larg / 2
                fim = cx - larg / 2 - 2.5

            _escrever(pg, cx, y, linhas, p["area_txt"], tn, ta, placa=False)
            meio = y + alt / 2
            pg.draw_line(pymupdf.Point(fim, meio), pymupdf.Point(p["px"], p["py"]),
                         color=MINT_ESC, width=0.45)
            pg.draw_circle(pymupdf.Point(p["px"], p["py"]), 1.0,
                           color=None, fill=MINT_ESC)


def _etiquetar(pg, etiquetas, caixa, x_esq, x_dir, img_x0, img_x1, y_topo, y_base):
    """Escreve nome e area de cada ambiente EM TEXTO VETORIAL sobre a planta.

    O texto nunca entra na imagem: gravado no pixel ele seria reamostrado
    junto com a planta na hora de encaixar na folha, e era dai que vinha o
    nome borrado. Aqui ele e texto de PDF - fica nitido em qualquer zoom e
    em qualquer impressora.
    """
    x0, y0, k = caixa
    pendentes, ocupados = [], []
    for e in etiquetas:
        px = x0 + e["x"] * k
        py = y0 + e["y"] * k
        area_txt = ("%.2f" % e["area"]).replace(".", ",") + " m²" if e["area"] else ""
        cabe = _cabe_dentro(e["nome"], area_txt or "0", e["vao"] * k)
        dentro = None
        if cabe:
            linhas, tn, ta = cabe
            alt = len(linhas) * (tn + tn * 0.20) + ta
            larg = max([_larg(t, True, tn) for t in linhas]
                       + [_larg(area_txt, False, ta) if area_txt else 0])
            r = (px - larg / 2 - 1, py - alt / 2 - 1, px + larg / 2 + 1, py + alt / 2 + 1)
            # so cabe se houver folga em ALTURA tambem, e se nao bater numa
            # etiqueta ja colocada. Bater era o que fazia HALL e QUARTO
            # sairem escritos um por cima do outro.
            if e["raio"] * k * 2 >= alt * 1.05 and not _bate(r, ocupados):
                dentro = (linhas, tn, ta, alt, r)
        if dentro:
            linhas, tn, ta, alt, r = dentro
            _escrever(pg, px, py - alt / 2, linhas, area_txt, tn, ta, placa=False)
            ocupados.append(r)
        else:
            meio = (img_x0 + img_x1) / 2
            pendentes.append({"nome": e["nome"], "area_txt": area_txt,
                              "px": px, "py": py,
                              "lado": "e" if px <= meio else "d"})
    if pendentes:
        _chamadas(pg, pendentes, x_esq, x_dir, img_x0, img_x1, y_topo, y_base)


def montar(planta_img, tres_d_pdf, amb, titulo, saida, area_lote=None,
           timbrado="tb/word/media/image1.jpeg", humanizar_3d=True,
           area_construida=None, area_quintal=None, pecas=None,
           etiquetas=None, caracteristicas=None, churrasqueira=None,
           textos=(), telhado="revit"):
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
    corte = _caixa(planta_img)
    pl = planta_img.crop(corte)
    y_planta = y + 13
    alt_disp = RODAPE - y_planta
    pw, ph = _cabe(pl.width, pl.height, largL, alt_disp)
    cx = colL + (largL - pw) / 2
    pg.insert_text((colL, y + 5), "PLANTA BAIXA HUMANIZADA", fontname="DJB",
                   fontsize=7.6, color=PETROL)
    # 800 dpi efetivos e PNG sem perda: a planta e desenho de linha e ocupa
    # uma faixa estreita da folha, entao isso custa pouco no arquivo final.
    pg.insert_image(pymupdf.Rect(cx, y_planta, cx + pw, y_planta + ph),
                    stream=_bytes(pl, int(pw / 72 * 800), sem_perda=True))

    if etiquetas:
        k = pw / pl.width
        _etiquetar(pg, [dict(e, x=e["x"] - corte[0], y=e["y"] - corte[1])
                        for e in etiquetas],
                   (cx, y_planta, k), colL, colL + largL,
                   cx, cx + pw, y_planta, y_planta + ph)

    # ---------------- fachada 3D --------------------------------------------
    im3 = fachada.carregar(tres_d_pdf, humanizar=humanizar_3d, telhado=telhado)
    tw, th = _cabe(im3.width, im3.height, largR, 190)
    tx = colR + (largR - tw) / 2
    pg.insert_text((colR, y + 5), "PERSPECTIVA / FACHADA", fontname="DJB",
                   fontsize=7.6, color=PETROL)
    pg.insert_image(pymupdf.Rect(tx, y + 13, tx + tw, y + 13 + th),
                    stream=_bytes(im3, int(tw / 72 * 450), q=95))

    # ---------------- caracteristicas ---------------------------------------
    # Sem o quadro de ambientes (removido em 28/09) sobra folga: a descricao
    # sobe de corpo e ganha respiro - e ela que vende a casa.
    if caracteristicas is None:
        caracteristicas = resumo(amb, churrasqueira, textos)
    caracteristicas = [c.strip().upper() for c in caracteristicas if c and c.strip()]
    yd = y + 13 + th + 22
    pg.insert_text((colR, yd), "CARACTERÍSTICAS", fontname="DJB", fontsize=8.2, color=PETROL)
    yd += 15
    tam = 8.0 if len(caracteristicas) <= 9 else 7.2
    for ln in caracteristicas:
        # linha comprida quebra em duas em vez de sair da coluna
        partes = [ln]
        if _larg(ln, False, tam) > largR - 12:
            import humanizar
            partes = humanizar.quebrar(ln)
        pg.draw_circle(pymupdf.Point(colR + 2.6, yd - 2.9), 1.9, color=None, fill=MINT)
        for k, t in enumerate(partes):
            pg.insert_text((colR + 11, yd), t, fontname="DJ", fontsize=tam,
                           color=(0.16, 0.16, 0.22))
            yd += tam + 2.6 if k < len(partes) - 1 else 0
        yd += tam + 6.2

    # ---------------- resumo de areas ---------------------------------------
    yq = yd + 10
    A = areas(amb, area_lote, area_construida, area_quintal)
    alt = 17.0
    pg.draw_rect(pymupdf.Rect(colR, yq, colR + largR, yq + alt * len(A) + 8),
                 color=None, fill=(0.960, 0.969, 0.973))
    yy = yq + 14
    for i, (nome, v) in enumerate(A.items()):
        ult = i == len(A) - 1
        f = "DJB" if ult else "DJ"
        pg.insert_text((colR + 9, yy), nome, fontname=f, fontsize=7.4,
                       color=NAVY if ult else (0.25, 0.25, 0.3))
        _dir(pg, colR + largR - 9, yy, _num(v), f, 7.8, NAVY)
        if not ult:
            pg.draw_line(pymupdf.Point(colR + 9, yy + 5.2),
                         pymupdf.Point(colR + largR - 9, yy + 5.2),
                         color=(0.88, 0.90, 0.92), width=0.5)
        yy += alt

    # sem isto o PNG da planta fica gravado sem compressao e a prancha sai com
    # 11 MB em vez de 1,4 MB - mesmo desenho, mesma resolucao.
    doc.save(saida, garbage=4, deflate=True, clean=True)
    return saida
