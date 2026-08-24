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


def _classificar(img, rgbs, tol_max=45):
    """Cada pixel vai para a cor de ficha MAIS PROXIMA - nao para "todas as
    que estao dentro de uma tolerancia".

    Isto nao e detalhe. Com 16 ambientes, as cores que o Revit gera chegam a
    ficar 19 de distancia entre si; com limiar fixo de 26 as manchas de dois
    comodos se sobrepoem e um engole o outro - a QUARTO 01 de 5,64 m2 apareceu
    com 15,50 m2 porque tinha comido a garagem ao lado. Vizinho mais proximo
    nao tem esse problema: cada pixel tem um dono so.

    A tolerancia ainda existe, para o traco preto e a parede nao virarem
    ambiente, mas e calculada a partir do espacamento REAL das cores da ficha:
    nunca passa da metade da menor distancia entre duas delas. O miolo do
    comodo tem a cor exata (distancia zero), entao nada de util se perde - e
    o pixel ambiguo da borda fica sem dono em vez de ir para o comodo errado.
    """
    tol = tol_max
    if len(rgbs) > 1:
        menor = min(max(abs(x - y) for x, y in zip(a, b))
                    for i, a in enumerate(rgbs) for b in rgbs[i + 1:])
        tol = int(max(6, min(tol_max, (menor - 1) // 2)))
    melhor_d = np.full(img.shape[:2], 1e9, np.float32)
    dono = np.zeros(img.shape[:2], np.int32)
    base = img.astype(np.int16)
    for i, c in enumerate(rgbs, start=1):
        d = np.abs(base - np.array(c, np.int16)).max(axis=2)
        troca = d < melhor_d
        melhor_d[troca] = d[troca]
        dono[troca] = i
    dono[melhor_d > tol] = 0      # longe de todas: nao e ambiente (parede, traco)
    return dono


def _fechar(m, passos):
    """Fechamento por quadrado (2*passos+1). Feito em passos de 3x3 porque
    mandar um elemento 41x41 direto trava o navegador."""
    if passos <= 0:
        return m
    return ndimage.binary_closing(m, np.ones((3, 3)), iterations=int(passos))


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
    dono = _classificar(img, rgbs)
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

    # ------------------------------------------- 3. mancha limpa de cada um
    passos = max(1, int(FECHO_M * ppm / 2.0))
    amb, seg = [], np.zeros(img.shape[:2], np.int32)
    cores_usadas, cotas = [], [0]
    for it, rgb, crua in zip(itens, rgbs, cruas):
        i = len(amb) + 1
        if crua.sum() < 40:
            continue
        # o movel e o texto sao desenhados POR CIMA: a mancha sai furada.
        # fechar + tapar buraco devolve o comodo - e continua sendo leitura,
        # porque o limite de fora e a cor, nao um palpite.
        m = ndimage.binary_fill_holes(_fechar(crua, passos)) & ~par_grosso
        lab, n = ndimage.label(m)
        if n > 1:                       # fica so com a mancha principal
            tam = np.bincount(lab.ravel())
            tam[0] = 0
            m = lab == int(np.argmax(tam))
        if not m.any():
            continue
        m &= seg == 0
        if not m.any():
            continue
        seg[m] = i
        cores_usadas.append(rgb)
        area = float(it.get("area") or 0)
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
        raise ValueError("A ficha nao bateu com nenhuma cor do PDF. Confira se "
                         "o JSON e o PDF sao da mesma exportacao.")

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
        raise ValueError("O PDF e o JSON nao parecem da mesma exportacao: "
                         "{0} de {1} ambientes nao bateram."
                         .format(reprovados, len(amb)))

    lote = ndimage.binary_fill_holes(seg > 0) | paredes
    return dict(page=page, amb=amb, img=img, paredes=paredes,
                par=ndimage.binary_closing(paredes, np.ones((5, 5))),
                tapa=np.zeros(img.shape[:2], bool), lote=lote,
                interno=(seg > 0), seg=seg, k=k, escala=escala,
                confiavel=reprovados == 0, dpi=dpi, sc=dpi / 72.0, orfao=0.0,
                cores_ambiente=cores_usadas, via_ficha=True)
