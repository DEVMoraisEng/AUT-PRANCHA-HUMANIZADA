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
# Etiquetas dos ambientes: vermelho escuro + área grafite, com contorno branco
# (lê bem em cima de grama, concreto escuro, piso claro e madeira)
ET_COR_NOME = (0.69, 0.10, 0.12)
ET_COR_AREA = (0.20, 0.20, 0.24)
ONDA_NAVY = (41/255, 87/255, 120/255)
ONDA_MINT = (125/255, 207/255, 195/255)
# Contorno da onda do rodapé, medido no timbrado do .docx (fração da folha).
# Vai desenhada em VETOR: a imagem da onda dentro do .docx é um recorte de
# tela com moldura cinza, e era ela que deixava o rodapé torto e com bordas.
_ONDA_X = [0.0, 0.035, 0.071, 0.106, 0.142, 0.177, 0.212, 0.248, 0.283, 0.318, 0.354,
           0.389, 0.425, 0.46, 0.495, 0.531, 0.566, 0.602, 0.637, 0.672, 0.708, 0.743,
           0.778, 0.814, 0.849, 0.885, 0.92, 0.955, 0.991, 1.0]
_ONDA_MINT_Y = [0.904, 0.9035, 0.904, 0.9055, 0.907, 0.909, 0.9115, 0.9145, 0.917, 0.9205,
                0.9235, 0.9265, 0.929, 0.9315, 0.933, 0.934, 0.9345, 0.9335, 0.932, 0.929,
                0.925, 0.919, 0.912, 0.902, 0.8895, 0.874, 0.8555, 0.833, 0.803, 0.797]
_ONDA_NAVY_Y = [0.92, 0.92, 0.9205, 0.922, 0.924, 0.926, 0.9285, 0.9315, 0.9345, 0.938,
                0.941, 0.9435, 0.946, 0.948, 0.9495, 0.9505, 0.9505, 0.949, 0.947, 0.9435,
                0.939, 0.9325, 0.9245, 0.9135, 0.9, 0.8835, 0.8635, 0.8385, 0.809, 0.8035]

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


def _suave(xs, ys, n=8):
    """Catmull-Rom: liga os pontos medidos com curva lisa (sem quinas)."""
    P = list(zip(xs, ys))
    P = [P[0]] + P + [P[-1]]
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        for j in range(n):
            t = j / n
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[c]) + (-p0[c] + p2[c]) * t +
                                    (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * t2 +
                                    (-p0[c] + 3 * p1[c] - 3 * p2[c] + p3[c]) * t3) for c in (0, 1)))
    out.append(P[-2])
    return out


def _onda(pg):
    """Onda do rodapé em vetor, sangrando até as bordas da folha."""
    W, H = pg.rect.width, pg.rect.height
    for ys, cor in ((_ONDA_MINT_Y, ONDA_MINT), (_ONDA_NAVY_Y, ONDA_NAVY)):
        pts = [pymupdf.Point(x * (W + 2) - 1, y * H) for x, y in _suave(_ONDA_X, ys)]
        sh = pg.new_shape()
        sh.draw_polyline(pts + [pymupdf.Point(W + 1, H + 1), pymupdf.Point(-1, H + 1)])
        sh.finish(color=None, fill=cor, closePath=True)
        sh.commit()


def _fundo_timbrado(pg, timbrado, pecas=None, x_regua=None):
    """Papel timbrado: logo (PNG em resolução cheia), régua e onda em vetor.
    Sem o logo cai para a imagem de página inteira do .docx."""
    W, H = pg.rect.width, pg.rect.height
    from timbrado import POS_LOGO, POS_REGUA_Y, POS_REGUA_X
    pg.draw_rect(pg.rect, color=None, fill=(1, 1, 1))
    if pecas and "logo" in pecas:
        x0, y0, x1, y1 = POS_LOGO
        pg.insert_image(pymupdf.Rect(x0 * W, y0 * H, x1 * W, y1 * H),
                        filename=pecas["logo"], keep_proportion=True)
        # a régua começa alinhada com o texto da prancha (antes ficava 7 pt
        # para dentro e a folha parecia torta)
        xr = x_regua if x_regua is not None else POS_REGUA_X * W
        pg.draw_line(pymupdf.Point(xr, POS_REGUA_Y * H),
                     pymupdf.Point(W, POS_REGUA_Y * H), color=NAVY, width=1.1)
        _onda(pg)
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


_HALO = [(-1, 0), (1, 0), (0, -1), (0, 1), (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)]


def _texto(pg, x, y, txt, fonte, tam, cor, halo=True, rot=0):
    """Texto com contorno branco fino (cópias deslocadas por baixo). Não usa
    render_mode/border_width porque o PyMuPDF do navegador mede diferente.
    rot=90: lê de baixo para cima."""
    if halo:
        d = tam * 0.085
        for dx, dy in _HALO:
            pg.insert_text((x + dx * d, y + dy * d), txt, fontname=fonte, fontsize=tam,
                           color=(1, 1, 1), rotate=rot)
    pg.insert_text((x, y), txt, fontname=fonte, fontsize=tam, color=cor, rotate=rot)


def _escrever_girado(pg, cx, cy, nome_linhas, area_txt, tn, ta):
    """Mesmo bloco do _escrever, centrado em (cx, cy) e girado 90° (lê de
    baixo para cima) — para circulação, jardim lateral e corredor estreito."""
    gap = tn * 0.20
    alt = len(nome_linhas) * (tn + gap) + ta
    v = -alt / 2 + tn
    for t in nome_linhas:
        w = _larg(t, True, tn)
        _texto(pg, cx + v, cy + w / 2, t, "DJB", tn, ET_COR_NOME, rot=90)
        v += tn + gap
    if area_txt:
        wa = _larg(area_txt, False, ta)
        _texto(pg, cx + v, cy + wa / 2, area_txt, "DJ", ta, ET_COR_AREA, rot=90)


def _escrever(pg, cx, topo, nome_linhas, area_txt, tn, ta, placa=False, halo=True):
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
        _texto(pg, cx - w / 2, y, t, "DJB", tn, ET_COR_NOME, halo)
        y += tn + gap
    if area_txt:
        _texto(pg, cx - wa / 2, y, area_txt, "DJ", ta, ET_COR_AREA, halo)
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

            _escrever(pg, cx, y, linhas, p["area_txt"], tn, ta, placa=False, halo=False)
            meio = y + alt / 2
            pg.draw_line(pymupdf.Point(fim, meio), pymupdf.Point(p["px"], p["py"]),
                         color=MINT_ESC, width=0.45)
            pg.draw_circle(pymupdf.Point(p["px"], p["py"]), 1.0,
                           color=None, fill=MINT_ESC)


def _achar_lugar(livre, wpx, hpx, alvo):
    """Centro (x, y) em px da imagem onde um bloco wpx x hpx cabe INTEIRO no
    miolo livre do ambiente (sem móvel, sem giro de porta, longe da parede).
    Entre os lugares possíveis fica o mais "no meio" do espaço livre; empate
    vai para perto do ponto sugerido. None se não couber."""
    from scipy import ndimage
    m = np.asarray(livre["m"], bool)
    sp = float(livre["passo"])
    cw, ch = int(np.ceil(wpx / sp)) + 1, int(np.ceil(hpx / sp)) + 1
    R, C = m.shape
    if cw > C or ch > R or not m.any():
        return None
    S = np.zeros((R + 1, C + 1), np.int32)
    S[1:, 1:] = m.astype(np.int32).cumsum(0).cumsum(1)
    soma = S[ch:, cw:] - S[:-ch, cw:] - S[ch:, :-cw] + S[:-ch, :-cw]
    ok = soma == cw * ch
    if not ok.any():
        return None
    dt = ndimage.distance_transform_edt(np.pad(ok, 1)) [1:-1, 1:-1]
    ys, xs = np.nonzero(ok)
    cx = livre["c0"] + (xs + cw / 2.0) * sp
    cy = livre["r0"] + (ys + ch / 2.0) * sp
    # desempate: perto do meio dos lugares possíveis (numa faixa comprida
    # todos empatam e o nome ia parar na ponta)
    mx, my = float(cx.mean()), float(cy.mean())
    dist = np.hypot(cx - mx, cy - my) / sp
    i = int(np.argmax(dt[ys, xs] * 1.0 - 0.03 * dist))
    return float(cx[i]), float(cy[i])


def _bloco(linhas, area_txt, tn, ta):
    alt = len(linhas) * (tn + tn * 0.20) + ta
    larg = max([_larg(t, True, tn) for t in linhas]
               + [_larg(area_txt, False, ta) if area_txt else 0])
    return larg, alt


def _comprido(livre):
    """Ambiente bem mais comprido na vertical do que largo (corredor, jardim
    lateral): aí o nome deitado cabe com letra boa."""
    m = np.asarray(livre["m"], bool)
    if not m.any():
        return False
    ys, xs = np.nonzero(m)
    return (ys.max() - ys.min() + 1) >= 2.2 * (xs.max() - xs.min() + 1)


def _dentro_livre(e, area_txt, k, ocupados, x0, y0):
    """Tenta encaixar a etiqueta INTEIRA dentro do ambiente, nesta ordem:
      1. no chão livre (sem móvel nem giro de porta), em pé;
      2. deitada, se o ambiente é comprido e estreito;
      3. sobre o móvel (cama, sofá), longe só da parede e da porta.
    Em cada passo tenta corpo cheio, depois menor, em uma ou duas linhas.
    → (linhas, tn, ta, alt, larg, px, py, r, girado) ou None."""
    import humanizar
    escalas = (1.0, 0.92, 0.85, ET_NOME_MIN / ET_NOME)
    quebras = [[e["nome"]]]
    if " " in e["nome"].strip():
        quebras.append(humanizar.quebrar(e["nome"]))
    tentativas = [("livre", False)]
    if _comprido(e["livre"]):
        tentativas.append(("livre", True))
    if e.get("livre2") is not None:
        tentativas.append(("livre2", False))
        if _comprido(e["livre2"]):
            tentativas.append(("livre2", True))
    for chave, girado in tentativas:
        for esc in escalas:
            tn, ta = ET_NOME * esc, max(ET_AREA_MIN, ET_AREA * esc)
            for linhas in (quebras if not girado else quebras[:1]):
                larg, alt = _bloco(linhas, area_txt, tn, ta)
                bw, bh = (alt, larg) if girado else (larg, alt)
                # folga do contorno branco + respiro
                pos = _achar_lugar(e[chave], (bw + 2.4) / k, (bh + 2.0) / k, (e["x"], e["y"]))
                if pos is None:
                    continue
                px, py = x0 + pos[0] * k, y0 + pos[1] * k
                r = (px - bw / 2 - 1, py - bh / 2 - 1, px + bw / 2 + 1, py + bh / 2 + 1)
                if _bate(r, ocupados):
                    continue
                return linhas, tn, ta, alt, larg, px, py, r, girado
    return None


def _chamadas_h(pg, pendentes, img_x0, img_x1, x_min, x_max, y_img0, y_img1, banda):
    """Planta deitada: quem não cabe sai para cima ou para baixo do desenho,
    com linha de chamada, sem um texto encostar no outro."""
    import humanizar
    meio = (y_img0 + y_img1) / 2
    for lado in ("c", "b"):
        itens = sorted([p for p in pendentes if (p["py"] <= meio) == (lado == "c")],
                       key=lambda p: p["px"])
        fim_ant = x_min - 99
        for p in itens:
            tn, ta = ET_CHAMADA_NOME, ET_CHAMADA_AREA
            linhas = [p["nome"]]
            if _larg(p["nome"], True, tn) > 62:
                linhas = humanizar.quebrar(p["nome"])
            larg, alt = _bloco(linhas, p["area_txt"], tn, ta)
            cx = max(p["px"], fim_ant + 4 + larg / 2)
            cx = min(max(cx, x_min + larg / 2), x_max - larg / 2)
            fim_ant = cx + larg / 2
            if lado == "c":
                topo = y_img0 - 4 - alt
                y_lig = topo + alt + 1.2
            else:
                topo = y_img1 + 4
                y_lig = topo - 1.2
            _escrever(pg, cx, topo, linhas, p["area_txt"], tn, ta, halo=False)
            pg.draw_line(pymupdf.Point(cx, y_lig), pymupdf.Point(p["px"], p["py"]),
                         color=MINT_ESC, width=0.45)
            pg.draw_circle(pymupdf.Point(p["px"], p["py"]), 1.0, color=None, fill=MINT_ESC)


def _etiquetar(pg, etiquetas, caixa, x_esq, x_dir, img_x0, img_x1, y_topo, y_base,
               deitada=False):
    """Escreve nome e area de cada ambiente EM TEXTO VETORIAL sobre a planta.

    O texto nunca entra na imagem: gravado no pixel ele seria reamostrado
    junto com a planta na hora de encaixar na folha, e era dai que vinha o
    nome borrado. Aqui ele e texto de PDF - fica nitido em qualquer zoom e
    em qualquer impressora.

    Com o mapa do miolo livre (planta desenhada pelo Revit) o bloco vai para
    o maior vão do ambiente onde cabe inteiro, sem cair em cima de móvel nem
    do giro da porta.
    """
    x0, y0, k = caixa
    pendentes, ocupados = [], []
    # ambiente pequeno primeiro: ele tem menos opção de lugar
    ordem = sorted(etiquetas, key=lambda e: (e.get("livre") is None, e.get("area") or 0))
    for e in ordem:
        px = x0 + e["x"] * k
        py = y0 + e["y"] * k
        area_txt = ("%.2f" % e["area"]).replace(".", ",") + " m²" if e["area"] else ""
        dentro = None
        girado = False
        if e.get("livre") is not None:
            d = _dentro_livre(e, area_txt, k, ocupados, x0, y0)
            if d:
                linhas, tn, ta, alt, larg, px, py, r, girado = d
                dentro = (linhas, tn, ta, alt, r)
        else:
            cabe = _cabe_dentro(e["nome"], area_txt or "0", e["vao"] * k)
            if cabe:
                linhas, tn, ta = cabe
                larg, alt = _bloco(linhas, area_txt, tn, ta)
                r = (px - larg / 2 - 1, py - alt / 2 - 1, px + larg / 2 + 1, py + alt / 2 + 1)
                # so cabe se houver folga em ALTURA tambem, e se nao bater numa
                # etiqueta ja colocada. Bater era o que fazia HALL e QUARTO
                # sairem escritos um por cima do outro.
                if e["raio"] * k * 2 >= alt * 1.05 and not _bate(r, ocupados):
                    dentro = (linhas, tn, ta, alt, r)
        if dentro:
            linhas, tn, ta, alt, r = dentro
            if girado:
                _escrever_girado(pg, px, py, linhas, area_txt, tn, ta)
            else:
                _escrever(pg, px, py - alt / 2, linhas, area_txt, tn, ta, placa=False)
            ocupados.append(r)
        else:
            meio = (img_x0 + img_x1) / 2
            pendentes.append({"nome": e["nome"], "area_txt": area_txt,
                              "px": x0 + e["x"] * k, "py": y0 + e["y"] * k,
                              "lado": "e" if px <= meio else "d"})
    if pendentes:
        if deitada:
            _chamadas_h(pg, pendentes, img_x0, img_x1, x_esq, x_dir, y_topo, y_base, 24)
        else:
            _chamadas(pg, pendentes, x_esq, x_dir, img_x0, img_x1, y_topo, y_base)
    return pendentes


def _lados_com_chamada(ets, k, pw, ph):
    """Ensaio da etiquetagem numa folha de rascunho: quais lados da planta
    vão ter chamada (nome puxado para fora)."""
    rasc = pymupdf.open()
    pr = rasc.new_page(width=2000, height=2000)
    pr.insert_font(fontname="DJ", fontfile=REG)
    pr.insert_font(fontname="DJB", fontfile=BOLD)
    x0 = 500.0
    pend = _etiquetar(pr, ets, (x0, 20.0, k), x0 - 80, x0 + pw + 80, x0, x0 + pw, 20.0, 20.0 + ph)
    rasc.close()
    return set(p["lado"] for p in pend)


def _girar_etiquetas(etiquetas, h):
    """Planta girada 90° no sentido horário (frente da casa vai para a
    esquerda): leva ponto e mapa livre junto. h = altura da imagem."""
    out = []
    for e in etiquetas:
        e2 = dict(e, x=h - e["y"], y=e["x"])
        for chave in ("livre", "livre2"):
            lv = e.get(chave)
            if lv is not None:
                m = np.asarray(lv["m"], bool)
                R = m.shape[0]
                e2[chave] = {"m": np.rot90(m, -1), "passo": lv["passo"],
                             "c0": h - lv["r0"] - (R - 1) * lv["passo"], "r0": lv["c0"]}
        out.append(e2)
    return out


def _deslocar(etiquetas, dx, dy):
    """Leva as etiquetas para o recorte da planta (ponto e mapa livre)."""
    out = []
    for e in etiquetas:
        e2 = dict(e, x=e["x"] - dx, y=e["y"] - dy)
        for chave in ("livre", "livre2"):
            if e.get(chave) is not None:
                e2[chave] = dict(e[chave], r0=e[chave]["r0"] - dy, c0=e[chave]["c0"] - dx)
        out.append(e2)
    return out


def _titulo_secao(pg, x, y, txt):
    pg.insert_text((x, y), txt, fontname="DJB", fontsize=7.6, color=PETROL)


def _caracteristicas(pg, linhas, x, larg, yd):
    """Desenha a lista e devolve onde ela acaba. pg=None só mede."""
    if pg is not None:
        pg.insert_text((x, yd), "CARACTERÍSTICAS", fontname="DJB", fontsize=8.2, color=PETROL)
    yd += 15
    tam = 8.0 if len(linhas) <= 9 else 7.2
    for ln in linhas:
        # linha comprida quebra em duas em vez de sair da coluna
        partes = [ln]
        if _larg(ln, False, tam) > larg - 12:
            import humanizar
            partes = humanizar.quebrar(ln)
        if pg is not None:
            pg.draw_circle(pymupdf.Point(x + 2.6, yd - 2.9), 1.9, color=None, fill=MINT)
        for k, t in enumerate(partes):
            if pg is not None:
                pg.insert_text((x + 11, yd), t, fontname="DJ", fontsize=tam,
                               color=(0.16, 0.16, 0.22))
            yd += tam + 2.6 if k < len(partes) - 1 else 0
        yd += tam + 6.2
    return yd


def _quadro_areas(pg, A, x, larg, yq):
    alt = 17.0
    pg.draw_rect(pymupdf.Rect(x, yq, x + larg, yq + alt * len(A) + 8),
                 color=None, fill=(0.960, 0.969, 0.973))
    yy = yq + 14
    for i, (nome, v) in enumerate(A.items()):
        ult = i == len(A) - 1
        f = "DJB" if ult else "DJ"
        pg.insert_text((x + 9, yy), nome, fontname=f, fontsize=7.4,
                       color=NAVY if ult else (0.25, 0.25, 0.3))
        _dir(pg, x + larg - 9, yy, _num(v), f, 7.8, NAVY)
        if not ult:
            pg.draw_line(pymupdf.Point(x + 9, yy + 5.2),
                         pymupdf.Point(x + larg - 9, yy + 5.2),
                         color=(0.88, 0.90, 0.92), width=0.5)
        yy += alt
    return yy


def montar(planta_img, tres_d_pdf, amb, titulo, saida, area_lote=None,
           timbrado="tb/word/media/image1.jpeg", humanizar_3d=True,
           area_construida=None, area_quintal=None, pecas=None,
           etiquetas=None, caracteristicas=None, churrasqueira=None,
           textos=(), telhado="revit"):
    doc = pymupdf.open()
    pg = doc.new_page(width=595.276, height=841.89)
    W = pg.rect.width
    ML, MR = 52, 52
    _fundo_timbrado(pg, timbrado, pecas, x_regua=ML)
    pg.insert_font(fontname="DJ", fontfile=REG)
    pg.insert_font(fontname="DJB", fontfile=BOLD)

    topo = 203.0
    # Onde a onda do rodapé começa. Ela é baixa à esquerda e sobe à direita:
    # a coluna da planta desce até 745 pt, a da direita para em 705 pt.
    RODAPE_E = 745.0
    RODAPE = 705.0
    largU = W - ML - MR

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

    if caracteristicas is None:
        caracteristicas = resumo(amb, churrasqueira, textos)
    caracteristicas = [c.strip().upper() for c in caracteristicas if c and c.strip()]
    A = areas(amb, area_lote, area_construida, area_quintal)
    im3 = fachada.carregar(tres_d_pdf, humanizar=humanizar_3d, telhado=telhado)

    corte = _caixa(planta_img)
    pl = planta_img.crop(corte)
    ets = _deslocar(etiquetas or [], corte[0], corte[1])

    # ---------------- qual arranjo? -------------------------------------------
    # Casa comprida e estreita (lote 5 x 25) em pé fica fininha na coluna e os
    # nomes não cabem. Deitada, ela ocupa a largura toda da folha: desenho
    # maior e cômodos largos o bastante para o nome caber dentro.
    largL = 258.0
    BANDA = 26.0                         # faixa das chamadas acima/abaixo
    esc_pe = min(largL / pl.width, (RODAPE_E - (y + 13)) / pl.height)
    esc_deit = min(largU / pl.height, 175.0 / pl.width)
    deitada = pl.height > 1.8 * pl.width and esc_deit >= 1.1 * esc_pe

    if deitada:
        pr = pl.transpose(Image.ROTATE_270)          # frente da casa à esquerda
        k = esc_deit
        pw, ph = pr.width * k, pr.height * k
        _titulo_secao(pg, ML, y + 5, "PLANTA BAIXA HUMANIZADA")
        y_img = y + 13 + BANDA
        cx = ML + (largU - pw) / 2
        pg.insert_image(pymupdf.Rect(cx, y_img, cx + pw, y_img + ph),
                        stream=_bytes(pr, int(pw / 72 * 800), sem_perda=True))
        if ets:
            _etiquetar(pg, _girar_etiquetas(ets, pl.height), (cx, y_img, k),
                       ML, W - MR, cx, cx + pw, y_img, y_img + ph, deitada=True)
        y2 = y_img + ph + BANDA + 16

        # fachada à esquerda, texto à direita
        largF = largU * 0.56
        colR = ML + largF + 20
        largR = W - MR - colR
        _titulo_secao(pg, ML, y2 + 5, "PERSPECTIVA / FACHADA")
        tw, th = _cabe(im3.width, im3.height, largF, RODAPE - (y2 + 13) - 6)
        pg.insert_image(pymupdf.Rect(ML + (largF - tw) / 2, y2 + 13, ML + (largF + tw) / 2, y2 + 13 + th),
                        stream=_bytes(im3, int(tw / 72 * 450), q=95))
        yd = _caracteristicas(pg, caracteristicas, colR, largR, y2 + 5)
        _quadro_areas(pg, A, colR, largR, yd + 8)
    else:
        y_planta = y + 13
        k = esc_pe
        pw, ph = pl.width * k, pl.height * k
        # A coluna da planta acompanha a largura do desenho (+ espaço para as
        # chamadas dos dois lados); o que sobra vai para a fachada, que antes
        # ficava pequena ao lado de uma casa estreita.
        CHAMADA = 66.0
        lados = _lados_com_chamada(ets, k, pw, ph) if ets else set()
        esq = CHAMADA if "e" in lados else 0.0
        dir_ = CHAMADA if "d" in lados else 0.0
        largL_ef = min(largL, max(esq + pw + dir_, 150.0))
        colL = ML
        colR = ML + largL_ef + 30
        largR = W - MR - colR
        cx = colL + esq + (largL_ef - esq - dir_ - pw) / 2
        _titulo_secao(pg, colL, y + 5, "PLANTA BAIXA HUMANIZADA")
        # 800 dpi efetivos e PNG sem perda: a planta e desenho de linha e ocupa
        # uma faixa estreita da folha, entao isso custa pouco no arquivo final.
        pg.insert_image(pymupdf.Rect(cx, y_planta, cx + pw, y_planta + ph),
                        stream=_bytes(pl, int(pw / 72 * 800), sem_perda=True))
        if ets:
            _etiquetar(pg, ets, (cx, y_planta, k), colL, colL + largL_ef,
                       cx, cx + pw, y_planta, y_planta + ph)

        # ---------------- fachada 3D ----------------------------------------
        # altura que sobra depois do texto e do quadro de áreas
        alt_txt = (_caracteristicas(None, caracteristicas, colR, largR, 0)
                   + 10 + 17.0 * len(A) + 8)
        alt_max = max(150.0, min(290.0, RODAPE - (y + 13) - 22 - alt_txt - 30))
        tw, th = _cabe(im3.width, im3.height, largR, alt_max)
        tx = colR + (largR - tw) / 2
        _titulo_secao(pg, colR, y + 5, "PERSPECTIVA / FACHADA")
        pg.insert_image(pymupdf.Rect(tx, y + 13, tx + tw, y + 13 + th),
                        stream=_bytes(im3, int(tw / 72 * 450), q=95))
        # ---------------- caracteristicas + areas ---------------------------
        yd = _caracteristicas(pg, caracteristicas, colR, largR, y + 13 + th + 22)
        _quadro_areas(pg, A, colR, largR, yd + 10)

    # sem isto o PNG da planta fica gravado sem compressao e a prancha sai com
    # 11 MB em vez de 1,4 MB - mesmo desenho, mesma resolucao.
    doc.save(saida, garbage=4, deflate=True, clean=True)
    return saida
