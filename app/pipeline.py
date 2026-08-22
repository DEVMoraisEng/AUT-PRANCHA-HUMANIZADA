"""Pipeline comum: le a planta, acha o lote, segmenta os ambientes."""
import numpy as np, pymupdf
from scipy import ndimage
from skimage.morphology import convex_hull_image
import extract as E, segmentar as S, nomes

DPI = 150


def _tapar_vaos(par, px_por_m, vao_max=1.2, tolerancia=2.0):
    """Fecha os vaos de porta e de janela com um tampao virtual.

    Uma abertura de parede e um trecho vazio ENTRE DOIS PEDACOS DA MESMA
    PAREDE: some se fecharmos na direcao da parede, e a espessura dela
    perpendicular continua sendo a espessura da parede. E isso que a
    distingue de um comodo estreito, que tambem "fecha" mas fica grosso.

    Fechar essas aberturas faz o acabamento de piso mudar exatamente na
    soleira - como num projeto desenhado a mao - e impede que o piso de um
    ambiente vaze para o vizinho por baixo de uma janela.
    O tampao nao vira parede no desenho: e so barreira de pintura.
    """
    if not par.any():
        return np.zeros_like(par)
    espessura = 2 * np.percentile(ndimage.distance_transform_edt(par)[par], 92)
    L = max(3, int(vao_max * px_por_m))
    saida = np.zeros_like(par)
    for eixo in (0, 1):
        est = np.ones((L, 1), bool) if eixo == 0 else np.ones((1, L), bool)
        cand = ndimage.binary_closing(par, est) & ~par
        lab, n = ndimage.label(cand)
        if not n:
            continue
        manter = np.zeros(n + 1, bool)
        for i, sl in enumerate(ndimage.find_objects(lab), 1):
            if sl is None:
                continue
            alt = sl[0].stop - sl[0].start
            larg = sl[1].stop - sl[1].start
            perpendicular = larg if eixo == 0 else alt
            manter[i] = perpendicular <= espessura * tolerancia
        saida |= manter[lab]
    return saida


def preparar(pdf="PLANTA.pdf", dpi=DPI, beta=40.0, pagina=0, apelidos=None,
             escala_fixa=None, sem_numero=False, _reamostrado=False):
    """Le a planta e devolve tudo que o desenho precisa.

    A resolucao de trabalho e ajustada a ESCALA do desenho: uma planta 1:100
    ocupa metade do papel de uma 1:50, entao ela e rasterizada com o dobro do
    dpi. Assim o algoritmo enxerga sempre a mesma quantidade de pixels por
    metro, independente da escala em que a prancha foi plotada.
    """
    doc = pymupdf.open(pdf); page = doc[pagina]
    amb = E.ler_rotulos(page)
    sc = dpi / 72.0
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    r, g, b = (img[:, :, i].astype(int) for i in range(3))
    paredes = (r > 140) & (r - g > 55) & (r - b > 55)
    par = ndimage.binary_closing(paredes, np.ones((5, 5)))
    lote = convex_hull_image(par)
    ppm = (dpi / 25.4) * 1000 / 50   # px por metro, escala do desenho
    tapa = _tapar_vaos(par, ppm)     # portas e janelas viram divisa de piso
    interno = lote & ~par & ~tapa

    # escala deduzida do desenho, conferida contra as areas escritas na planta
    mk = np.zeros(paredes.shape, np.int32)
    for i, a in enumerate(amb, 1):
        mk[int(a["y"] * sc), int(a["x"] * sc)] = i
    # Escala: uma primeira inundacao SEM cota reparte o espaco livre entre os
    # ambientes; a mediana de (pixels / area declarada) da a escala do desenho.
    # A mediana e usada de proposito: alguns ambientes encostam em area externa
    # e ficam inflados, mas a maioria fica certa.
    livres = np.full(len(amb) + 1, np.iinfo(np.int64).max, np.int64)
    seg0 = S.completar(S.inundar_com_cota(interno, mk, livres, beta=beta), interno)
    razoes = [float((seg0 == i).sum()) / a["area"]
              for i, a in enumerate(amb, 1) if (seg0 == i).any()]
    escala, confiavel = S.escala_do_desenho(float(np.median(razoes)), dpi)
    if escala_fixa:
        escala, confiavel = float(escala_fixa), True

    if not _reamostrado and confiavel and abs(escala - 50) > 1:
        novo = int(round(min(600, max(100, DPI * escala / 50))))
        if abs(novo - dpi) > 10:
            return preparar(pdf, novo, beta, pagina, apelidos, escala_fixa,
                            sem_numero, _reamostrado=True)

    k = ((dpi / 25.4) * 1000 / escala) ** 2

    # Os pisos externos ja vem chapados no proprio PDF (area pavimentada em
    # cinza, area permeavel em verde claro). Onde o desenho ja diz onde o
    # ambiente comeca e termina, usamos o desenho - nao o palpite.
    for rgb in ((132, 132, 132), (226, 237, 232)):
        chapado = (np.abs(img.astype(np.int16) - np.array(rgb)).max(axis=2) <= 3) & interno
        comp, _ = ndimage.label(chapado)
        for i, a in enumerate(amb, 1):
            py, px = int(a["y"] * sc), int(a["x"] * sc)
            c = comp[py, px]
            if c and (comp == c).sum() > 0.25 * a["area"] * ((dpi / 25.4) * 1000 / 50) ** 2:
                mk[comp == c] = i

    cotas = np.zeros(len(amb) + 1, np.int64)
    for i, a in enumerate(amb, 1):
        cotas[i] = int(a["area"] * k)
    ppm_out = np.sqrt(k)
    seg = S.completar(S.inundar_com_cota(interno, mk, cotas, beta=beta), interno,
                      alcance=1.0 * ppm_out * 41)   # ~1 m de tolerancia
    seg = S.limpar(seg, interno)
    orfao = float((interno & (seg == 0)).sum()) / k
    for i, a in enumerate(amb, 1):
        a["medido"] = float((seg == i).sum()) / k
        a["erro"] = abs(a["medido"] - a["area"]) / a["area"] * 100
        a["rotulo"] = nomes.bonito(a["nome"], apelidos, sem_numero)
    return dict(page=page, amb=amb, img=img, paredes=paredes, par=par,
                tapa=tapa, lote=lote, interno=interno, seg=seg, k=k,
                escala=escala, confiavel=confiavel, dpi=dpi, sc=sc,
                orfao=orfao)
