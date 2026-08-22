"""
Segmentacao de ambientes: inundacao por prioridade com COTA DE AREA.

Ideia: cada ambiente cresce a partir da propria etiqueta, sempre pelo miolo
do espaco livre (prioridade = distancia ate a parede), e CONGELA quando
atinge exatamente a area em m2 que ja esta escrita na planta.
Assim o desenho fica com os limites certos (nos vaos de porta) e a area
pintada bate com a area do projeto. Nada e inventado.
"""
import heapq
import numpy as np
from scipy import ndimage

VIZ = ((-1, 0), (1, 0), (0, -1), (0, 1))
VIZ_ES = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], bool)


def escala_do_desenho(px_por_m2_estimado, dpi, tol=0.08):
    """Deduz a escala (1:50, 1:75, 1:100...) a partir do proprio desenho."""
    px_por_m = np.sqrt(px_por_m2_estimado)
    denom = (dpi / 25.4) * 1000 / px_por_m
    padroes = [20, 25, 50, 75, 100, 125, 150, 200]
    melhor = min(padroes, key=lambda p: abs(p - denom))
    return (melhor, True) if abs(melhor - denom) / denom <= tol else (denom, False)


def inundar_com_cota(interno, sementes, cotas, beta=40.0):
    """Dijkstra multi-origem com cota de area.

    Custo para entrar num pixel = 1 + beta/(1+distancia ate a parede).
    - em espaco aberto o custo e ~1  -> o ambiente cresce compacto,
      e o limite com o vizinho integrado vira uma reta entre as etiquetas;
    - num vao de porta o custo dispara -> o ambiente enche o proprio
      comodo antes de atravessar a porta.
    O ambiente congela ao atingir a area em m2 escrita na planta.
    """
    dist = ndimage.distance_transform_edt(interno).astype(np.float32)
    custo = 1.0 + beta / (1.0 + dist)
    H, W = interno.shape
    seg = np.zeros((H, W), np.int32)
    n = int(sementes.max())
    conta = np.zeros(n + 1, np.int64)
    cheio = np.zeros(n + 1, bool)

    fila = []
    ys, xs = np.nonzero(sementes)
    for y, x in zip(ys.tolist(), xs.tolist()):
        i = int(sementes[y, x])
        seg[y, x] = i
        conta[i] += 1
        heapq.heappush(fila, (0.0, y, x, i))

    C, S = custo, seg
    while fila:
        d, y, x, i = heapq.heappop(fila)
        if cheio[i]:
            continue
        for dy, dx in VIZ:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and S[ny, nx] == 0 and interno[ny, nx]:
                S[ny, nx] = i
                conta[i] += 1
                if conta[i] >= cotas[i]:
                    cheio[i] = True
                    break
                heapq.heappush(fila, (d + float(C[ny, nx]), ny, nx, i))
    return seg


def completar(seg, interno, alcance=None):
    """Fecha os cantos que sobraram continuando a inundacao SEM cota.
    Geodesico: a sobra vai para o ambiente mais proximo ANDANDO pelo piso,
    nunca atravessando parede.
    'alcance' limita ate onde a sobra pode ser adotada: area grande que o
    projeto nao nomeia continua sem dono, em vez de ser despejada no vizinho."""
    falta = interno & (seg == 0)
    if not falta.any():
        return seg
    dist = ndimage.distance_transform_edt(interno).astype(np.float32)
    custo = 1.0 + 40.0 / (1.0 + dist)
    H, W = interno.shape
    S = seg.copy()
    fila = []
    borda = (S > 0) & ndimage.binary_dilation(falta, VIZ_ES)
    for y, x in zip(*np.nonzero(borda)):
        heapq.heappush(fila, (0.0, int(y), int(x), int(S[y, x])))
    while fila:
        d, y, x, i = heapq.heappop(fila)
        if alcance is not None and d > alcance:
            continue
        for dy, dx in VIZ:
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and S[ny, nx] == 0 and interno[ny, nx]:
                S[ny, nx] = i
                heapq.heappush(fila, (d + float(custo[ny, nx]), ny, nx, i))
    return S


def limpar(seg, interno):
    """Tira respingos: cada ambiente fica so com a sua maior mancha continua.
    O que sobra volta para o vizinho mais proximo andando pelo piso."""
    out = seg.copy()
    for i in range(1, int(seg.max()) + 1):
        m = seg == i
        if not m.any():
            continue
        lab, n = ndimage.label(m)
        if n <= 1:
            continue
        tam = ndimage.sum(m, lab, range(1, n + 1))
        maior = int(np.argmax(tam)) + 1
        out[m & (lab != maior)] = 0
    return completar(out, interno)
