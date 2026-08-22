"""
Etapa 1 - EXTRACAO (deterministica, zero IA):
le o PDF vetorial da planta e devolve
  - ambientes (nome, area m2, ancora XY)
  - mascara de paredes em alta resolucao
  - poligono/regiao de cada ambiente por watershed a partir das etiquetas
A conferencia e feita contra as areas em m2 que ja estao escritas na planta.
"""
import re, json
import numpy as np
import pymupdf
from scipy import ndimage
from skimage.segmentation import watershed

DPI = 300
SCALE = DPI / 72.0


AREA = re.compile(r"^([\d]{1,4}(?:[.,][\d]{1,4})?)\s*m²$")


def _numero(txt):
    """Aceita virgula OU ponto como separador decimal (o Revit exporta os dois)."""
    t = txt.replace(" ", "")
    if "," in t and "." in t:                    # 1.234,56  ou  1,234.56
        dec = max(t.rfind(","), t.rfind("."))
        t = t[:dec].replace(".", "").replace(",", "") + "." + t[dec + 1:]
    else:
        t = t.replace(",", ".")
    return float(t)


def ler_rotulos(page, margem=8.0):
    """Le NOME + AREA + POSICAO de cada ambiente.

    Nao confia no agrupamento de blocos do PDF: o Revit as vezes joga o nome e
    a area em blocos diferentes, e as vezes quebra o nome em duas linhas.
    Aqui as linhas sao agrupadas por PROXIMIDADE: o que esta encostado forma
    uma etiqueta. Um grupo so vira ambiente se tiver nome E area.
    """
    words = page.get_text("words")
    porlinha = {}
    for w in words:
        porlinha.setdefault((w[5], w[6]), []).append(w)

    linhas = []
    for ws in porlinha.values():
        ws.sort(key=lambda w: w[0])
        linhas.append((" ".join(w[4] for w in ws),
                       min(w[0] for w in ws), min(w[1] for w in ws),
                       max(w[2] for w in ws), max(w[3] for w in ws)))

    pai = list(range(len(linhas)))

    def raiz(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    for i in range(len(linhas)):
        for j in range(i + 1, len(linhas)):
            _, ax0, ay0, ax1, ay1 = linhas[i]
            _, bx0, by0, bx1, by1 = linhas[j]
            if (ax0 - margem < bx1 and bx0 - margem < ax1
                    and ay0 - margem < by1 and by0 - margem < ay1):
                pai[raiz(i)] = raiz(j)

    grupos = {}
    for i, l in enumerate(linhas):
        grupos.setdefault(raiz(i), []).append(l)

    ambientes = []
    for g in grupos.values():
        g.sort(key=lambda l: (l[2], l[1]))
        partes, area = [], None
        for txt, x0, y0, x1, y1 in g:
            m = AREA.match(txt.strip())
            if m and area is None:
                area = _numero(m.group(1))
            else:
                partes.append(txt.strip())
        nome = " ".join(p for p in partes if p).strip()
        if nome and area is not None:
            ambientes.append({
                "nome": nome, "area": area,
                "x": (min(l[1] for l in g) + max(l[3] for l in g)) / 2,
                "y": (min(l[2] for l in g) + max(l[4] for l in g)) / 2,
            })
    return ambientes


def render(page):
    pix = page.get_pixmap(dpi=DPI, colorspace=pymupdf.csRGB)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3).copy()


def mascara_paredes(img):
    r, g, b = (img[:, :, i].astype(int) for i in range(3))
    return (r > 140) & (r - g > 55) & (r - b > 55)


def segmentar(paredes, ambientes):
    """Watershed: cada etiqueta cresce ate encontrar parede ou o vizinho.
       Vaos de porta ficam divididos no meio, sem vazamento."""
    barreira = ndimage.binary_closing(paredes, np.ones((7, 7)))
    interno = ~barreira

    marcadores = np.zeros(paredes.shape, dtype=np.int32)
    # sementes do EXTERIOR: os vaos de porta ficam repartidos no meio,
    # sem que a area externa invada os ambientes
    EXT = len(ambientes) + 1
    marcadores[2:12, :] = EXT; marcadores[-12:-2, :] = EXT
    marcadores[:, 2:12] = EXT; marcadores[:, -12:-2] = EXT
    marcadores[barreira] = 0
    for i, a in enumerate(ambientes, start=1):
        py, px = int(a["y"] * SCALE), int(a["x"] * SCALE)
        colocado = False
        for raio in range(0, 90, 3):
            ys, xs = np.where(interno[max(0, py-raio):py+raio+1, max(0, px-raio):px+raio+1])
            if len(ys):
                marcadores[max(0, py-raio)+ys[len(ys)//2], max(0, px-raio)+xs[len(xs)//2]] = i
                colocado = True
                break
        a["_semente"] = colocado

    # relevo = distancia ate a parede (negativa) -> a frente anda pelo miolo do vao
    dist = ndimage.distance_transform_edt(interno)
    return watershed(-dist, marcadores, mask=interno)


if __name__ == "__main__":
    doc = pymupdf.open("PLANTA.pdf")
    page = doc[0]
    amb = ler_rotulos(page)
    img = render(page)
    paredes = mascara_paredes(img)
    seg = segmentar(paredes, amb)

    print(f"{'AMBIENTE':<26}{'m2 PLANTA':>11}{'px':>10}{'m2 MEDIDO':>11}{'erro':>8}")
    px_m2 = []
    for i, a in enumerate(amb, start=1):
        n = int((seg == i).sum())
        if n: px_m2.append(n / a["area"])
    k = float(np.median(px_m2))
    ok = 0
    for i, a in enumerate(amb, start=1):
        n = int((seg == i).sum())
        med = n / k
        err = abs(med - a["area"]) / a["area"] * 100
        ok += err < 12
        print(f"{a['nome']:<26}{a['area']:>11.2f}{n:>10}{med:>11.2f}{err:>7.1f}%")
    print(f"\nescala: {k:.0f} px por m2  ->  {np.sqrt(k):.1f} px/m  ->  1:{(1/(np.sqrt(k)/DPI*25.4/1000)):.0f}")
    print(f"ambientes conferidos dentro de 12%: {ok}/{len(amb)}")

    np.save("seg.npy", seg); np.save("paredes.npy", paredes); np.save("img.npy", img)
    json.dump(amb, open("ambientes.json", "w"), ensure_ascii=False, indent=1, default=str)
