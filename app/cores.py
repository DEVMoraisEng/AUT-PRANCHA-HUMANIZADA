"""
Leitura da planta pela FICHA DE CORES do Revit.

Quando a planta vem do botao "Planta Humanizada" do pyRevit, cada ambiente
sai pintado com uma cor unica e vem junto um JSON dizendo qual cor e qual
ambiente. Aqui a regiao de cada ambiente deixa de ser deducao e vira leitura:
o pixel tem a cor do ambiente ou nao tem.

O unico lugar onde ainda ha calculo e o buraco que o MOVEL faz na mancha de
cor (o movel e desenhado por cima). Esse buraco e recuperado com dois freios
ao mesmo tempo: a mancha nao pode passar da area que o proprio Revit declara,
e nao pode caminhar longe. Sem os dois ela atravessa a porta e come o vizinho.

O outro caminho (segmentar.py) continua existindo para planta antiga,
exportada antes de o botao existir.
"""
import json

import numpy as np
import pymupdf
from scipy import ndimage

import segmentar as S

# quanto a mancha pode caminhar para recuperar o que estava embaixo do movel
ALCANCE_M = 3.0
# px por metro em que o resto do programa foi calibrado (1:50 a 150 dpi).
# A planta e reamostrada ate chegar perto disso, entao a textura de piso e o
# modulo de 50 cm saem iguais venha o PDF na escala que vier.
PPM_ALVO = 118.1
# fecha vao de texto e linha fina de movel dentro da mancha
FECHO_M = 0.10
# abaixo disso a mancha lida nao confere com a area declarada -> nao repinta.
# vale o que for mais generoso: 45% da area, ou uma falta de ate 0,35 m2
# (comodo pequeno some embaixo do proprio texto do rotulo).
MINIMO_LIDO = 0.45
FALTA_TOLERADA = 0.35


def carregar_ficha(caminho_ou_texto):
    """Aceita o caminho do .json ou o proprio conteudo."""
    try:
        with open(caminho_ou_texto, "r") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return json.loads(caminho_ou_texto)


def _mascara_da_cor(img, rgb, tol=26):
    d = np.abs(img.astype(np.int16) - np.array(rgb, np.int16)).max(axis=2)
    return d <= tol


def _classificar(img, rgbs, tol=40):
    """Cada pixel vai para a cor de ficha MAIS PROXIMA - nao para "todas as
    que estao dentro de uma tolerancia".

    Isto nao e detalhe. Com 16 ambientes as cores que o Revit gera chegam a
    ficar 19 de distancia entre si; com limiar fixo as manchas de dois comodos
    se sobrepoem e um engole o outro - a QUARTO 01 de 5,64 m2 ja apareceu com
    15,50 m2 porque tinha comido a garagem ao lado. Vizinho mais proximo nao
    tem esse problema: cada pixel tem um dono so, e a tolerancia so decide
    "isto e cor de ambiente ou e parede/traco/papel".

    Por isso a tolerancia pode - e deve - ser folgada. Ja tentei aperta-la
    para metade do espacamento das cores: qualquer desvio de cor do PDF
    (perfil de cor, conversao na impressao) derrubava a leitura inteira.
    """
    melhor_d = np.full(img.shape[:2], np.inf, np.float32)
    dono = np.zeros(img.shape[:2], np.int32)
    base = img.astype(np.int16)
    for i, c in enumerate(rgbs, start=1):
        d = np.abs(base - np.array(c, np.int16)).max(axis=2)
        troca = d < melhor_d
        melhor_d[troca] = d[troca]
        dono[troca] = i
    dono[melhor_d > tol] = 0      # longe de todas: nao e ambiente (parede, traco)
    return dono, melhor_d


def _achou_bastante(dono, n, mininmo_px=200):
    """Quantos ambientes da ficha aparecem de fato no PDF."""
    conta = np.bincount(dono.ravel(), minlength=n + 1)
    return int((conta[1:] >= mininmo_px).sum())


def _diagnostico(img, rgbs, nomes):
    """Por que as cores nao bateram? Mede a distancia entre a cor pedida e a
    cor mais parecida que existe no PDF - e isso que separa "PDF de outra
    exportacao" de "PDF certo, com a cor deslocada"."""
    base = img.astype(np.int16)
    linhas = []
    for c, nome in zip(rgbs, nomes):
        d = int(np.abs(base - np.array(c, np.int16)).max(axis=2).min())
        linhas.append((d, nome, c))
    linhas.sort()
    return linhas


def _fechar(m, passos):
    """Fechamento por quadrado (2*passos+1). Feito em passos de 3x3 porque
    mandar um elemento 41x41 direto trava o navegador."""
    if passos <= 0:
        return m
    return ndimage.binary_closing(m, np.ones((3, 3)), iterations=int(passos))


def _mancha(crua, passos, par_grosso):
    """Mancha fechada de um ambiente: fecha vaos, tapa buracos, fica com a
    maior parte continua."""
    m = ndimage.binary_fill_holes(_fechar(crua, passos)) & ~par_grosso
    lab, n = ndimage.label(m)
    if n > 1:
        t = np.bincount(lab.ravel())
        t[0] = 0
        m = lab == int(np.argmax(t))
    return m


def _px_por_m2(cruas, celulas, areas):
    """Mede pixels por m2 no proprio desenho, usando os comodos FECHADOS.

    Num comodo fechado, a celula - o espaco cercado por parede - E o comodo.
    A area da celula em pixels e a area do projeto em m2 falam da mesma coisa,
    entao a razao entre as duas e a escala real do PDF.

    Por que medir em vez de usar a escala da ficha: o Revit exporta a vista
    ajustada a folha (senao a planta sai cortada), e "ajustar a folha" mexe na
    escala por um fator que ninguem sabe de antemao. A ficha diz 1:50 e o
    papel entrega 1:63. Medir resolve isso de uma vez - e ainda funciona para
    PDF que alguem exportou na mao, em qualquer escala.

    Comodo aberto para outro (sala/cozinha integradas) cai fora da conta: a
    celula ali tem dois donos e nao serve de regua. Basta um comodo fechado.
    """
    dono = {}
    for i, m in enumerate(cruas, start=1):
        if not m.any():
            continue
        c = celulas[m]
        c = c[c > 0]
        if not c.size:
            continue
        dono.setdefault(int(np.bincount(c).argmax()), []).append(i)
    tam = np.bincount(celulas.ravel())
    razoes = []
    for celula, quem in dono.items():
        if len(quem) != 1:
            continue
        a = areas[quem[0] - 1]
        if a >= 1.5 and celula < len(tam):
            razoes.append(tam[celula] / a)
    if len(razoes) >= 2:
        return float(np.median(razoes))
    return None


def preparar(pdf, ficha, dpi=None, pagina=0, apelidos=None, sem_numero=False,
             _reamostrado=False):
    """Mesmo formato de saida de pipeline.preparar(), mas sem adivinhar nada."""
    import nomes

    dados = ficha if isinstance(ficha, dict) else carregar_ficha(ficha)
    itens = [i for i in dados.get("ambientes", []) if i.get("cor")]
    if not itens:
        raise ValueError("A ficha (.json) nao tem nenhum ambiente com cor. "
                         "Rode de novo o botao Planta Humanizada no Revit.")

    escala_ficha = float(dados.get("escala") or 50)
    if dpi is None:
        dpi = 150

    doc = pymupdf.open(pdf)
    page = doc[pagina]
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()

    # ------------------------------------------------- 1. onde esta cada cor
    rgbs = [tuple(int(c) for c in it["cor"]) for it in itens]
    nomes_ficha = [it.get("nome", "?") for it in itens]
    # tolerancia folgada, e afrouxa mais se o PDF vier com a cor deslocada
    # (perfil de cor, conversao na exportacao). Vizinho mais proximo ja impede
    # que dois comodos se misturem, entao afrouxar aqui e barato e seguro.
    for tol in (40, 60, 85):
        dono, _dist = _classificar(img, rgbs, tol)
        if _achou_bastante(dono, len(rgbs)) >= max(1, len(rgbs) // 2):
            break
    tol_usada = tol
    cruas = [dono == i for i in range(1, len(rgbs) + 1)]
    areas = [float(it.get("area") or 0) for it in itens]

    # ------------------------------------------------------- 2. onde e parede
    # A hachura de parede do template e vermelha. Cor de ambiente NUNCA e
    # parede - por isso ela sai do teste antes, com folga para o antialias.
    # (Sem isso um ambiente rosa some inteiro: ja aconteceu com a Á. SERVIÇO.)
    r, g, b = (img[:, :, i].astype(int) for i in range(3))
    paredes = (r > 140) & (r - g > 55) & (r - b > 55)
    paredes &= ~ndimage.binary_dilation(dono > 0, np.ones((3, 3)))
    par_grosso = ndimage.binary_dilation(paredes, np.ones((3, 3)))
    celulas, _ = ndimage.label(~par_grosso)

    # --------------------------------------------------- 2b. escala de verdade
    k = _px_por_m2(cruas, celulas, areas)
    medida = k is not None
    if not medida:
        k = ((dpi / 25.4) * 1000 / escala_ficha) ** 2
    ppm = float(np.sqrt(k))

    # trabalha sempre com a mesma quantidade de pixels por metro: a textura de
    # piso e o modulo de 50 cm dependem disso
    if not _reamostrado and abs(ppm - PPM_ALVO) / PPM_ALVO > 0.10:
        novo = int(round(min(600, max(90, dpi * PPM_ALVO / ppm))))
        if novo != dpi:
            return preparar(pdf, ficha, dpi=novo, pagina=pagina,
                            apelidos=apelidos, sem_numero=sem_numero,
                            _reamostrado=True)
    escala = (dpi / 25.4) * 1000 / ppm

    # ------------------------------------------- 3. mancha de cada ambiente
    #
    # Ja tentei delimitar pelo espaco cercado por parede ("celula"): nao
    # funciona nestes PDFs. A parede sai como HACHURA de linhas, o vazio passa
    # entre as listras, e porta e janela abrem o resto - a planta inteira vira
    # uma celula so. Medido: 2 celulas para 13 ambientes. Quem delimita aqui e
    # a cor, ponto.
    passos = max(1, int(FECHO_M * ppm / 2.0))
    amb, seg = [], np.zeros(img.shape[:2], np.int32)
    cores_usadas, cotas = [], [0]
    remendo = 0
    for it, rgb, crua in zip(itens, rgbs, cruas):
        if crua.sum() < 40:
            continue
        area = float(it.get("area") or 0)
        i = len(amb) + 1
        # o movel e o texto sao desenhados POR CIMA: a mancha sai furada.
        # fechar + tapar buraco devolve o comodo - e continua sendo leitura,
        # porque o limite de fora e a cor, nao um palpite.
        m = _mancha(crua, passos, par_grosso)
        # mancha muito menor que a area declarada = preenchimento HACHURADO em
        # vez de solido. Com hachura a cor cobre so ~15% do comodo (medido).
        # Fechar mais forte costura as listras de volta numa mancha inteira.
        if area and m.sum() / k < 0.55 * area:
            forte = _mancha(crua, max(passos, int(0.30 * ppm / 2.0)), par_grosso)
            if forte.sum() > m.sum() * 1.2:
                m = forte
                remendo += 1
        if not m.any():
            continue
        m = m & (seg == 0)
        if not m.any():
            continue
        seg[m] = i
        cores_usadas.append(rgb)
        lido = float(m.sum()) / k
        cotas.append(int(round(area * k)) if area else 10 ** 12)
        amb.append({
            "nome": it.get("nome", "AMBIENTE {0}".format(i)),
            "area": area, "lido": lido,
            "medido": lido, "erro": 0.0,
            "x": 0.0, "y": 0.0,
            "cor": rgb,
        })

    if not amb:
        pior = _diagnostico(img, rgbs, nomes_ficha)
        raise ValueError(
            "Nenhuma cor da ficha foi encontrada no PDF.\n"
            "A cor mais parecida que existe no desenho esta a {0} de distancia "
            "da primeira cor da ficha (procurei ate {1}).\n"
            "Distancia perto de zero = PDF certo. Distancia grande = o PDF e "
            "de outra exportacao, ou foi exportado em preto e branco.\n"
            "Exemplos: {2}".format(
                pior[0][0], tol_usada,
                "; ".join("{0} pedia {1}, mais perto {2}".format(n, c, d)
                          for d, n, c in pior[:3])))

    # ------------------------------------ 4. recuperar o que o movel escondeu
    # A cor diz QUEM e o ambiente; a parede diz ATE ONDE ele pode ir; a area
    # do Revit diz QUANTO ele pode crescer. O que sobrar continua sem dono e
    # mantem o desenho original - area que o projeto nao nomeia nao vira piso.
    validas = set(np.unique(celulas[seg > 0]).tolist()) - {0}
    dentro = np.isin(celulas, list(validas)) & ~par_grosso
    seg = S.crescer_ate_cota(seg, dentro, np.array(cotas, np.int64),
                             alcance=ALCANCE_M * ppm)

    # ------------------------------------------------------- 5. conferencia
    for i, a in enumerate(amb, start=1):
        a["medido"] = float((seg == i).sum()) / k
        ys, xs = np.nonzero(seg == i)
        if len(ys):
            a["x"] = float(xs.mean()) * 72.0 / dpi
            a["y"] = float(ys.mean()) * 72.0 / dpi
        a["rotulo"] = nomes.bonito(a["nome"], apelidos, sem_numero)
        # a area NAO se confere aqui: ela veio do Revit, e exata. O numero
        # mostrado e a diferenca entre o que foi pintado e o que o projeto
        # declara - com a ficha isso fica em zero.
        a["erro"] = (abs(a["medido"] - a["area"]) / a["area"] * 100
                     if a["area"] else 0.0)
        # o que se confere e se a mancha de cor do PDF e mesmo desta ficha
        ok = (a["lido"] >= MINIMO_LIDO * a["area"]
              or a["area"] - a["lido"] <= FALTA_TOLERADA) if a["area"] else True
        a["celula_ok"] = bool(ok)
        a["confiavel"] = bool(ok)
        a["motivo"] = "" if ok else (
            "a mancha de cor no PDF ({0:.2f} m²) nao bate com a area da ficha "
            "({1:.2f} m²) - PDF e JSON parecem de exportacoes diferentes"
            .format(a["lido"], a["area"]))

    reprovados = sum(1 for a in amb if not a["confiavel"])
    if reprovados > len(amb) / 2.0:
        piores = sorted(amb, key=lambda a: a["lido"])[:4]
        raise ValueError(
            "O PDF e o JSON nao parecem da mesma exportacao: {0} de {1} "
            "ambientes nao bateram.\n"
            "Escala medida no desenho: 1:{2:.0f} · tolerancia de cor usada: "
            "{3} · pixels por metro: {4:.0f}\n"
            "Os que menos apareceram: {5}\n"
            "Se as areas lidas estao todas perto de zero, a mancha de cor saiu "
            "vazada (hachura em vez de solido) ou o PDF foi exportado sem "
            "cor. Rode de novo o botao Planta Humanizada e confira se a planta "
            "aparece colorida no Revit.".format(
                reprovados, len(amb), escala, tol_usada, ppm,
                "; ".join("{0} pedia {1:.2f} m2, achei {2:.2f}".format(
                    a["nome"], a["area"], a["lido"]) for a in piores)))

    lote = ndimage.binary_fill_holes(seg > 0) | paredes
    return dict(page=page, amb=amb, img=img, paredes=paredes,
                par=ndimage.binary_closing(paredes, np.ones((5, 5))),
                tapa=np.zeros(img.shape[:2], bool), lote=lote,
                interno=(seg > 0), seg=seg, k=k, escala=escala,
                confiavel=reprovados == 0, dpi=dpi, sc=dpi / 72.0, orfao=0.0,
                cores_ambiente=cores_usadas, via_ficha=True)
