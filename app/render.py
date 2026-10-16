# -*- coding: utf-8 -*-
"""
render.py — planta humanizada RENDERIZADA a partir dos dados do Revit
Morais Engenharia e Construção

Em vez de repintar o PDF (que só sabe pixel), desenha a planta do zero com a
geometria que o Revit exporta na ficha v4:

    paredes   polígonos das paredes cortadas na altura do plano de corte
    portas    centro, eixo da parede, largura, arco de abertura (dobradiça)
    janelas   centro, eixo, largura
    ambientes contorno + nome + área + texto do acabamento de piso
    pisos     contorno + material de cada piso modelado
    objetos   móveis / louças / bancadas: família, tipo, pegada orientada,
              para onde estão virados e (bancada) o contorno real
    tracos    linhas visíveis de escada / guarda-corpo

Nada é inventado: cada móvel sai exatamente na posição, tamanho e giro do
Revit; o que muda é COMO ele é desenhado (cama com lençol e travesseiro,
sofá com almofada, bacia de louça...) e o piso, que ganha textura, luz e
sombra.

Só numpy + Pillow + scipy (roda no navegador, via Pyodide).

    img, etiquetas = renderizar(ficha)          # PIL.Image RGB
    etiquetas = [{nome, area, x, y, cabe}]      # posição em px da imagem
"""
from __future__ import division

import math
import re
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

# ─────────────────────────────────────────────────────────── utilidades
def norm(t):
    t = unicodedata.normalize('NFKD', u'{}'.format(t or u''))
    return u''.join(c for c in t if not unicodedata.combining(c)).upper()


def _rng(*chave):
    h = 2166136261
    for c in u'|'.join(u'{}'.format(k) for k in chave):
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    return np.random.default_rng(h)


def _cor(rgb):
    return tuple(int(max(0, min(255, round(v * 255)))) for v in rgb)


def _mix(a, b, t):
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3))


def _ruido(h, w, passo, rng):
    """Ruído suave (value noise) h×w com detalhe a cada `passo` px.
    Ampliação pelo Pillow (bicúbica em float) — 10× mais rápida que o zoom
    do scipy e igual a olho."""
    passo = max(1.0, float(passo))
    gh, gw = int(h / passo) + 3, int(w / passo) + 3
    g = rng.random((gh, gw)).astype(np.float32)
    im = Image.fromarray(g, 'F').resize((int(gw * passo), int(gh * passo)), Image.BICUBIC)
    z = np.asarray(im, np.float32)[:h, :w]
    if z.shape != (h, w):
        z = np.pad(z, ((0, h - z.shape[0]), (0, w - z.shape[1])), mode='edge')
    return z


def _fbm(h, w, passo, rng, oitavas=4):
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for _ in range(oitavas):
        out += amp * _ruido(h, w, passo, rng)
        tot += amp
        amp *= 0.5
        passo = max(1.0, passo / 2.0)
    return out / tot


_GRADES = {}


def _ruido_g(tela, caixa, passo, chave):
    """Ruído em coordenada GLOBAL da folha: dois recortes vizinhos (garagem e
    quintal, por exemplo) continuam a mesma mancha — o concreto não 'emenda'."""
    r0, r1, c0, c1 = caixa
    p = max(2, int(round(passo)))
    GH, GW = tela.H // p + 6, tela.W // p + 6
    ch = (chave, p, GH, GW)
    if ch not in _GRADES:
        _GRADES[ch] = _rng('grade', chave, p).random((GH, GW)).astype(np.float32)
    g = _GRADES[ch]
    g0r, g0c = max(0, r0 // p - 2), max(0, c0 // p - 2)
    g1r, g1c = min(GH, r1 // p + 4), min(GW, c1 // p + 4)
    sub = g[g0r:g1r, g0c:g1c]
    up = np.asarray(Image.fromarray(sub, 'F').resize(((g1c - g0c) * p, (g1r - g0r) * p), Image.BICUBIC), np.float32)
    orr, occ = r0 - g0r * p, c0 - g0c * p
    out = up[orr:orr + (r1 - r0), occ:occ + (c1 - c0)]
    if out.shape != (r1 - r0, c1 - c0):
        out = np.pad(out, ((0, r1 - r0 - out.shape[0]), (0, c1 - c0 - out.shape[1])), mode='edge')
    return out


def _fbm_g(tela, caixa, passo, chave, oitavas=4):
    r0, r1, c0, c1 = caixa
    out = np.zeros((r1 - r0, c1 - c0), np.float32)
    amp, tot = 1.0, 0.0
    for i in range(oitavas):
        out += amp * _ruido_g(tela, caixa, passo, (chave, i))
        tot += amp
        amp *= 0.5
        passo = max(2.0, passo / 2.0)
    return out / tot


# ───────────────────────────────────────────────────────────── a tela
class Tela(object):
    """Plano da vista (m, y para cima) ↔ pixels (x para a direita, y para baixo)."""

    def __init__(self, xmin, ymin, xmax, ymax, px_m):
        self.xmin, self.ymin, self.xmax, self.ymax = xmin, ymin, xmax, ymax
        self.k = float(px_m)
        self.W = int(math.ceil((xmax - xmin) * self.k))
        self.H = int(math.ceil((ymax - ymin) * self.k))

    def p(self, x, y):
        return ((x - self.xmin) * self.k, (self.ymax - y) * self.k)

    def pts(self, anel):
        return [self.p(x, y) for x, y in anel]

    def mascara(self, aneis, ss=2, preencher=True, varios=False):
        """Alfa (0..1) anti-serrilhado dos polígonos. Com varios=False os
        anéis depois do primeiro são FUROS (contorno de ambiente do Revit);
        com varios=True todos somam (paredes)."""
        out = np.zeros((self.H, self.W), np.float32)
        todos = [self.pts(a) for a in aneis if len(a) >= 3]
        if not todos:
            return out
        xs = [x for pp in todos for x, _ in pp]
        ys = [y for pp in todos for _, y in pp]
        X0, Y0 = max(0, int(math.floor(min(xs))) - 1), max(0, int(math.floor(min(ys))) - 1)
        X1, Y1 = min(self.W, int(math.ceil(max(xs))) + 2), min(self.H, int(math.ceil(max(ys))) + 2)
        if X1 <= X0 or Y1 <= Y0:
            return out
        tam = ((X1 - X0) * ss, (Y1 - Y0) * ss)
        if varios or len(todos) == 1:
            im = Image.new('L', tam, 0)
            d = ImageDraw.Draw(im)
            for pp in todos:
                d.polygon([((x - X0) * ss, (y - Y0) * ss) for x, y in pp], fill=255)
            acc = np.asarray(im) > 127
        else:
            # par-ímpar: furo dentro do anel some; anéis separados (faces
            # diferentes do mesmo piso) somam
            acc = np.zeros((tam[1], tam[0]), bool)
            for pp in todos:
                im = Image.new('1', tam, 0)
                ImageDraw.Draw(im).polygon([((x - X0) * ss, (y - Y0) * ss) for x, y in pp], fill=1)
                acc ^= np.asarray(im, bool)
        im = Image.fromarray((acc * 255).astype(np.uint8), 'L')
        if ss > 1:
            im = im.resize((X1 - X0, Y1 - Y0), Image.BOX)
        out[Y0:Y1, X0:X1] = np.asarray(im, np.float32) / 255.0
        return out

    def grade_m(self, r0, r1, c0, c1):
        """Coordenadas em metros dos pixels de um recorte (para o piso
        casar a junta de um ambiente com o do lado)."""
        ys = self.ymax - (np.arange(r0, r1, dtype=np.float32) + 0.5) / self.k
        xs = self.xmin + (np.arange(c0, c1, dtype=np.float32) + 0.5) / self.k
        return np.meshgrid(xs, ys)


def _caixa(alfa, folga=2):
    ys, xs = np.nonzero(alfa > 0.002)
    if len(ys) == 0:
        return None
    return (max(0, ys.min() - folga), min(alfa.shape[0], ys.max() + folga + 1),
            max(0, xs.min() - folga), min(alfa.shape[1], xs.max() + folga + 1))


def _compor(base, cor, alfa, caixa=None):
    """base[...] = base·(1-α) + cor·α  (cor: HxWx3 ou rgb)."""
    if caixa is None:
        caixa = _caixa(alfa)
        if caixa is None:
            return
    r0, r1, c0, c1 = caixa
    a = alfa[r0:r1, c0:c1, None]
    if isinstance(cor, np.ndarray) and cor.ndim == 3:
        c = cor if cor.shape[:2] == a.shape[:2] else cor[r0:r1, c0:c1]
    else:
        c = np.array(cor, np.float32)[None, None, :]
    base[r0:r1, c0:c1] = base[r0:r1, c0:c1] * (1 - a) + c * a


# ─────────────────────────────────────────────────────────── materiais
PALETA = {
    'porcelanato': (0.905, 0.878, 0.832),
    'ceramica': (0.915, 0.900, 0.865),
    'madeira': (0.690, 0.520, 0.360),
    'concreto': (0.815, 0.805, 0.785),
    'grama': (0.420, 0.600, 0.260),
    'deck': (0.600, 0.430, 0.290),
    'brita': (0.700, 0.690, 0.670),
    'pedra': (0.800, 0.770, 0.720),
    'pintado': (0.520, 0.530, 0.540),
}

REGRAS_NOME = [
    (r'PERMEAVEL|GRAMA|JARDIM(?! DE INVERNO)|CANTEIRO', 'grama'),
    (r'QUARTO|SUITE|DORM|SALA|ESTAR|JANTAR|COZINHA|COPA|BANH|WC|LAVABO|HALL|CIRCULA|CORREDOR|'
     r'SERVICO|LAVANDERIA|GOURMET|ESCRITORIO|CLOSET|VARANDA|SACADA|ESTUDO', 'porcelanato'),
]


def classificar_piso(texto, nome_ambiente=u''):
    """→ (material, tamanho_da_placa_m (l, c) ou None, explícito?)"""
    t = norm(texto)
    tam = None
    m = re.search(r'(\d{2,3})\s*[X×]\s*(\d{2,3})', t)
    if m:
        a, b = float(m.group(1)) / 100.0, float(m.group(2)) / 100.0
        if 0.1 <= a <= 1.6 and 0.1 <= b <= 3.2:
            tam = (a, b)
    for chave, mat in ((r'PORCELANAT|AREAS? SECAS?', 'porcelanato'),
                       (r'CERAMIC|REVESTIMENTO|LADRILHO|AREAS? MOLHADAS?', 'ceramica'),
                       (r'TINTA PARA PISO|PISO PINTADO|EPOXI', 'pintado'),
                       (r'LAMINAD|VINIL|MADEIRA|TACO|ASSOALHO', 'madeira'), (r'DECK', 'deck'),
                       (r'GRAMA|GRAMAD|PERMEAVEL|JARDIM', 'grama'), (r'BRITA|SEIXO', 'brita'),
                       (r'GRANITO|MARMORE|PEDRA|ARDOSIA|SAO TOME|QUARTZ', 'pedra'),
                       (r'CONCRET|CIMENT|CONTRAPISO|CALCADA|DESEMPENAD|ARGAMASS', 'concreto')):
        if re.search(chave, t):
            return mat, tam, True
    n = norm(nome_ambiente)
    for rx, mat in REGRAS_NOME:
        if re.search(rx, n):
            return mat, tam, False
    return 'concreto', tam, False


def _dentro(x, y, anel):
    d = False
    n = len(anel)
    for i in range(n):
        x0, y0 = anel[i]
        x1, y1 = anel[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / ((y1 - y0) or 1e-12):
            d = not d
    return d


def _ponto_interno(aneis):
    """Um ponto DENTRO do ambiente (o centro de um L pode cair fora)."""
    a0 = aneis[0]
    xs = [p[0] for p in a0]
    ys = [p[1] for p in a0]
    melhor = None
    for fy in (0.5, 0.35, 0.65, 0.2, 0.8):
        y = min(ys) + (max(ys) - min(ys)) * fy
        for fx in (0.5, 0.35, 0.65, 0.2, 0.8):
            x = min(xs) + (max(xs) - min(xs)) * fx
            if sum(1 for r in aneis if _dentro(x, y, r)) % 2 == 1:
                return x, y
            if melhor is None:
                melhor = (x, y)
    return melhor


def material_do_ambiente(amb, pisos=None):
    """Acabamento do ambiente → (material, placa). Ordem:
    1. nome que manda (ÁREA PERMEÁVEL / JARDIM / GRAMA = grama);
    2. o PISO modelado que está embaixo do ambiente (revestimento ganha do
       contrapiso);
    3. o texto que o Revit gravou para o ambiente;
    4. a regra pelo nome."""
    nome = norm(amb.get('nome', ''))
    if re.search(r'PERMEAVEL|GRAMA|GRAMADO|JARDIM(?! DE INVERNO)|CANTEIRO', nome):
        return 'grama', None
    cont = amb.get('contorno') or []
    if pisos and cont:
        pt = _ponto_interno(cont)
        achados = []
        for p in pisos:
            aneis = p.get('contorno') or []
            if pt and sum(1 for r in aneis if _dentro(pt[0], pt[1], r)) % 2 == 1:
                mat, tam, expl = classificar_piso(p.get('texto', ''))
                if expl:
                    achados.append((mat, tam))
        acab = [a for a in achados if a[0] not in ('concreto',)]
        if acab:
            return acab[-1]
        if achados:
            return achados[-1]
    mat, tam, expl = classificar_piso(amb.get('piso', ''), amb.get('nome', ''))
    return mat, tam


def _placas(tela, caixa, larg, comp, junta, base, varia, rng, desloc=0.0, veio=0.0):
    """Piso em placas: cada placa com um tom próprio, junta fina e (opcional)
    veio de mármore suave. Coordenadas globais → juntas casam entre ambientes."""
    r0, r1, c0, c1 = caixa
    X, Y = tela.grade_m(r0, r1, c0, c1)
    iy = np.floor(Y / comp).astype(np.int64)
    Xd = X + (iy % 2) * desloc * larg
    ix = np.floor(Xd / larg).astype(np.int64)
    fx = Xd / larg - ix
    fy = Y / comp - iy
    h = ((ix * 73856093) ^ (iy * 19349663)) & 0xFFFF
    tom = ((h % 997) / 997.0 - 0.5).astype(np.float32) * 2 * varia
    hh, ww = r1 - r0, c1 - c0
    detalhe = (_fbm(hh, ww, tela.k * 0.35, rng, 3) - 0.5) * 0.035
    img = np.empty((hh, ww, 3), np.float32)
    for i in range(3):
        img[..., i] = base[i] * (1 + tom + detalhe)
    if veio > 0:
        n = _fbm(hh, ww, tela.k * 0.5, rng, 4)
        v = np.abs(np.sin((X * 3.1 + Y * 1.7 + n * 6.0 + (h % 31)) * 2.0))
        v = np.clip(1 - v / 0.06, 0, 1) * veio
        img *= (1 - v[..., None] * np.array([0.10, 0.11, 0.12], np.float32))
    # junta (em px no mínimo 1)
    jx = junta / larg
    jy = junta / comp
    dist = np.minimum(np.minimum(fx, 1 - fx) / max(jx, 1.0 / (tela.k * larg)),
                      np.minimum(fy, 1 - fy) / max(jy, 1.0 / (tela.k * comp)))
    j = np.clip(1.2 - dist, 0, 1)[..., None]
    img = img * (1 - j * 0.16)
    # brilho do acetinado: luz larga vinda de cima à esquerda
    luz = (_ruido(hh, ww, tela.k * 2.2, rng) - 0.5) * 0.03
    img *= (1 + luz[..., None])
    return img


def textura(tela, caixa, mat, tam, chave):
    r0, r1, c0, c1 = caixa
    hh, ww = r1 - r0, c1 - c0
    rng = _rng(mat, chave)
    base = PALETA.get(mat, PALETA['concreto'])
    if mat == 'porcelanato':
        l, c = tam or (0.6, 0.6)
        if l > c:
            l, c = c, l
        return _placas(tela, caixa, l, c, 0.003, base, 0.022, rng, veio=0.35)
    if mat == 'ceramica':
        l, c = tam or (0.5, 0.5)
        return _placas(tela, caixa, l, c, 0.004, base, 0.03, rng)
    if mat == 'pedra':
        l, c = tam or (0.4, 0.4)
        return _placas(tela, caixa, l, c, 0.004, base, 0.06, rng, veio=0.15)
    if mat in ('madeira', 'deck'):
        larg = 0.20 if mat == 'madeira' else 0.14
        comp = 1.2 if mat == 'madeira' else 2.4
        X, Y = tela.grade_m(r0, r1, c0, c1)
        # tábuas ao longo de X; junta de topo desencontrada
        iy = np.floor(Y / larg).astype(np.int64)
        off = ((iy * 2654435761) & 0xFFFF) / 65535.0 * comp
        ix = np.floor((X + off) / comp).astype(np.int64)
        fy = Y / larg - iy
        fx = (X + off) / comp - ix
        h = ((ix * 73856093) ^ (iy * 19349663)) & 0xFFFF
        tom = ((h % 991) / 991.0 - 0.5) * 0.16
        grao = _ruido(hh, max(ww, 4), tela.k * 0.012, rng)
        grao = ndimage.zoom(grao, (1, 1), order=1)
        grao = ndimage.uniform_filter1d(grao, size=max(3, int(tela.k * 0.25)), axis=1)
        veio = (np.sin((Y * 90 + grao * 14 + (h % 17))) * 0.5 + 0.5) * 0.06
        img = np.empty((hh, ww, 3), np.float32)
        for i in range(3):
            img[..., i] = base[i] * (1 + tom - veio + (grao - 0.5) * 0.10)
        gap = (np.minimum(fy, 1 - fy) * larg * tela.k < 0.7) | (np.minimum(fx, 1 - fx) * comp * tela.k < 0.7)
        img[gap] *= 0.72
        return img
    if mat == 'grama':
        n1 = _fbm_g(tela, caixa, tela.k * 0.9, 'grama', 4)
        n2 = _ruido(hh, ww, 1.4, rng)
        n3 = _ruido(hh, ww, 2.6, rng)
        img = np.empty((hh, ww, 3), np.float32)
        claro = (0.50, 0.68, 0.30)
        escuro = (0.24, 0.42, 0.16)
        t = np.clip(0.25 + n1 * 0.6 + (n2 - 0.5) * 0.55 + (n3 - 0.5) * 0.3, 0, 1)
        for i in range(3):
            img[..., i] = escuro[i] * (1 - t) + claro[i] * t
        return img
    if mat == 'brita':
        n = _ruido(hh, ww, max(2.0, tela.k * 0.02), rng)
        n2 = _ruido(hh, ww, 1.2, rng)
        img = np.empty((hh, ww, 3), np.float32)
        for i in range(3):
            img[..., i] = base[i] * (0.82 + n * 0.25 + (n2 - 0.5) * 0.15)
        return img
    if mat == 'pintado':
        n1 = _fbm_g(tela, caixa, tela.k * 1.2, 'pintado', 3)
        img = np.empty((hh, ww, 3), np.float32)
        for i in range(3):
            img[..., i] = base[i] * (0.96 + n1 * 0.07)
        return img
    # concreto: liso, manchado, poro fino (mancha em coordenada global)
    n1 = _fbm_g(tela, caixa, tela.k * 1.6, 'concreto', 4)
    n2 = _ruido(hh, ww, 1.3, rng)
    img = np.empty((hh, ww, 3), np.float32)
    for i in range(3):
        img[..., i] = base[i] * (0.93 + n1 * 0.12 + (n2 - 0.5) * 0.05)
    return img


# ───────────────────────────────────────────────────── móveis (sprites)
# Cada sprite é desenhado com a FRENTE da peça para baixo (lado de uso:
# pé da cama, assento do sofá, bacia do vaso), em escala SS vezes maior e
# depois reduzido — é isso que dá a borda limpa.
SS = 3


def _rr(d, box, r, fill, outline=None, w=1):
    x0, y0, x1, y1 = box
    if x1 - x0 < 1 or y1 - y0 < 1:
        return
    r = max(0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill, outline=outline, width=w)


def _grad_v(im, box, c0, c1):
    """degradê vertical dentro de box (sobre a máscara alfa já desenhada)."""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    if x1 <= x0 or y1 <= y0:
        return
    h = y1 - y0
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    g = (np.array(c0, np.float32) * (1 - t) + np.array(c1, np.float32) * t)
    g = np.repeat(g, x1 - x0, axis=1)
    reg = np.asarray(im.crop((x0, y0, x1, y1))).astype(np.float32)
    a = reg[..., 3:4] / 255.0
    rgb = reg[..., :3] * (1 - a * 0) * 0 + g * 255
    out = np.concatenate([rgb, reg[..., 3:4]], axis=2).clip(0, 255).astype(np.uint8)
    im.paste(Image.fromarray(out, 'RGBA'), (x0, y0))


def _sombreado(im, forca=0.18, raio=None):
    """Volume: escurece as bordas da peça (oclusão) e clareia o miolo."""
    a = np.asarray(im)[..., 3].astype(np.float32) / 255.0
    if a.max() <= 0:
        return im
    raio = raio or max(2.0, min(im.size) * 0.06)
    dist = ndimage.distance_transform_edt(a > 0.5)
    f = np.clip(dist / raio, 0, 1)
    k = (1 - forca) + forca * f
    rgb = np.asarray(im)[..., :3].astype(np.float32) * k[..., None]
    out = np.concatenate([rgb, (a * 255)[..., None]], axis=2).clip(0, 255).astype(np.uint8)
    return Image.fromarray(out, 'RGBA')


def _textura_tecido(im, rng, forca=0.06, passo=2.0):
    arr = np.asarray(im).astype(np.float32)
    n = _ruido(arr.shape[0], arr.shape[1], passo, rng)
    arr[..., :3] *= (1 + (n[..., None] - 0.5) * forca)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')


def _veio_madeira(im, rng, forca=0.10, horizontal=True):
    arr = np.asarray(im).astype(np.float32)
    h, w = arr.shape[:2]
    n = _ruido(h, w, max(2.0, (w if horizontal else h) * 0.25), rng)
    if horizontal:
        yy = np.arange(h, dtype=np.float32)[:, None]
        v = np.sin(yy * 0.45 + n * 9.0) * 0.5 + 0.5
    else:
        xx = np.arange(w, dtype=np.float32)[None, :]
        v = np.sin(xx * 0.45 + n * 9.0) * 0.5 + 0.5
    arr[..., :3] *= (1 - v[..., None] * forca)
    return Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')


TECIDOS = [(0.47, 0.56, 0.62), (0.70, 0.52, 0.42), (0.55, 0.60, 0.50), (0.78, 0.72, 0.62),
           (0.42, 0.47, 0.58), (0.72, 0.62, 0.48)]
MADEIRA = (0.66, 0.50, 0.34)
MADEIRA_CLARA = (0.80, 0.68, 0.52)
BRANCO = (0.975, 0.972, 0.965)
LOUCA = (0.985, 0.985, 0.98)
INOX = (0.78, 0.79, 0.80)
PRETO = (0.12, 0.12, 0.13)
CONTORNO = (0.35, 0.33, 0.31)


def _novo(w, h):
    im = Image.new('RGBA', (max(1, int(round(w))), max(1, int(round(h)))), (0, 0, 0, 0))
    return im, ImageDraw.Draw(im)


def sp_cama(W, D, k, rng):
    im, d = _novo(W, D)
    e = 0.025 * k
    _rr(d, (0, 0, W, D), 0.03 * k, _cor(MADEIRA) + (255,))
    cab = 0.07 * k
    _rr(d, (0, 0, W, cab), 0.02 * k, _cor(_mix(MADEIRA, (0.2, 0.15, 0.1), 0.35)) + (255,))
    # colchão
    _rr(d, (e, cab, W - e, D - e), 0.05 * k, _cor(BRANCO) + (255,))
    # travesseiros
    n = 2 if W > 1.15 * k else 1
    tw = (W - 2 * e - (n + 1) * 0.06 * k) / n
    for i in range(n):
        x0 = e + 0.06 * k + i * (tw + 0.06 * k)
        _rr(d, (x0, cab + 0.05 * k, x0 + tw, cab + 0.05 * k + 0.36 * k), 0.07 * k,
            _cor((0.99, 0.985, 0.975)) + (255,), _cor((0.82, 0.80, 0.77)) + (255,), max(1, int(0.006 * k)))
    # edredom
    cor = TECIDOS[int(rng.integers(len(TECIDOS)))]
    y_ed = cab + 0.50 * k
    _rr(d, (e * 0.6, y_ed, W - e * 0.6, D - e * 0.4), 0.06 * k, _cor(cor) + (255,))
    # dobra do lençol
    d.rectangle((e * 0.6 + 1, y_ed, W - e * 0.6 - 1, y_ed + 0.11 * k), fill=_cor(_mix(BRANCO, cor, 0.08)) + (255,))
    # manta no pé
    cor2 = _mix(cor, (0.15, 0.13, 0.12), 0.35)
    d.rectangle((e * 0.6 + 1, D - 0.42 * k, W - e * 0.6 - 1, D - 0.20 * k), fill=_cor(cor2) + (255,))
    # rugas do edredom: faixas largas de luz e sombra, bem suaves
    arr = np.asarray(im).astype(np.float32)
    hh, ww = arr.shape[:2]
    y0i = int(y_ed + 0.12 * k)
    if hh - y0i > 4:
        xx = np.arange(ww, dtype=np.float32)
        n = _ruido(hh - y0i, ww, max(3.0, 0.25 * k), rng)
        onda = np.sin(xx[None, :] / (0.11 * k) + n * 5.0)
        arr[y0i:, :, :3] *= (1 + onda[..., None] * 0.045)
    im = Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')
    im = _textura_tecido(im, rng, 0.05)
    return _sombreado(im, 0.16)


def sp_sofa(W, D, k, rng, lugares=None):
    im, d = _novo(W, D)
    cor = TECIDOS[int(rng.integers(len(TECIDOS)))]
    cor = _mix(cor, (0.62, 0.60, 0.57), 0.55)
    esc = _mix(cor, (0, 0, 0), 0.18)
    _rr(d, (0, 0, W, D), 0.07 * k, _cor(esc) + (255,))
    braco = min(0.16 * k, W * 0.14)
    enc = min(0.20 * k, D * 0.28)
    _rr(d, (braco * 0.5, 0, W - braco * 0.5, enc + 0.04 * k), 0.06 * k, _cor(_mix(cor, (0, 0, 0), 0.08)) + (255,))
    _rr(d, (0, 0, braco, D), 0.06 * k, _cor(esc) + (255,))
    _rr(d, (W - braco, 0, W, D), 0.06 * k, _cor(esc) + (255,))
    n = lugares or max(1, int(round((W - 2 * braco) / (0.62 * k))))
    lw = (W - 2 * braco) / n
    for i in range(n):
        x0 = braco + i * lw
        _rr(d, (x0 + 0.01 * k, enc, x0 + lw - 0.01 * k, D - 0.03 * k), 0.05 * k, _cor(cor) + (255,),
            _cor(_mix(cor, (0, 0, 0), 0.22)) + (255,), max(1, int(0.006 * k)))
    # almofadas soltas
    for x in (braco + 0.05 * k, W - braco - 0.40 * k):
        if W > 1.1 * k:
            c2 = TECIDOS[int(rng.integers(len(TECIDOS)))]
            _rr(d, (x, enc - 0.06 * k, x + 0.35 * k, enc + 0.16 * k), 0.05 * k, _cor(c2) + (255,))
    im = _textura_tecido(im, rng, 0.07)
    return _sombreado(im, 0.22)


def sp_guarda_roupa(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.01 * k, _cor(MADEIRA_CLARA) + (255,), _cor(CONTORNO) + (255,), max(1, int(0.008 * k)))
    im = _veio_madeira(im, rng, 0.07, horizontal=W >= D)
    d = ImageDraw.Draw(im)
    n = max(1, int(round(W / (0.5 * k))))
    for i in range(1, n):
        x = W * i / n
        d.line([(x, 0), (x, D)], fill=_cor(_mix(MADEIRA_CLARA, (0, 0, 0), 0.30)) + (200,), width=max(1, int(0.006 * k)))
    d.line([(0, D - 2), (W, D - 2)], fill=_cor(_mix(MADEIRA_CLARA, (0, 0, 0), 0.4)) + (255,), width=max(1, int(0.01 * k)))
    return _sombreado(im, 0.10)


def sp_criado(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.02 * k, _cor(MADEIRA) + (255,))
    im = _veio_madeira(im, rng, 0.08)
    d = ImageDraw.Draw(im)
    r = min(W, D) * 0.28
    cx, cy = W * 0.5, D * 0.45
    for i, t in enumerate(np.linspace(1, 0.4, 6)):
        rr = r * t
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=_cor(_mix((0.96, 0.92, 0.80), (1, 1, 0.95), i / 6.0)) + (255,))
    return _sombreado(im, 0.12)


def sp_rack(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.015 * k, _cor(_mix(MADEIRA, (0.1, 0.08, 0.06), 0.25)) + (255,))
    im = _veio_madeira(im, rng, 0.08, horizontal=W >= D)
    d = ImageDraw.Draw(im)
    # TV (vista de cima: barra fina preta no fundo)
    tw = min(W * 0.75, 1.25 * k)
    _rr(d, ((W - tw) / 2, D * 0.12, (W + tw) / 2, D * 0.12 + max(2, 0.05 * k)), 2, _cor(PRETO) + (255,))
    # vaso decorativo
    r = 0.07 * k
    d.ellipse((W * 0.88 - r, D * 0.55 - r, W * 0.88 + r, D * 0.55 + r), fill=_cor((0.36, 0.55, 0.30)) + (255,))
    return _sombreado(im, 0.12)


def sp_mesa(W, D, k, rng, cadeiras=True):
    """Mesa de jantar. Se a pegada já inclui as cadeiras (> 1 m de fundo e
    > 1,4 m de frente), o tampo é recuado; senão as cadeiras saem para fora
    (sprite com margem)."""
    inclui = D > 1.05 * k and W > 1.4 * k
    m = 0 if inclui or not cadeiras else 0.42 * k
    im, d = _novo(W + 2 * m, D + 2 * m)
    tx0, ty0, tx1, ty1 = m, m, m + W, m + D
    if inclui:
        tx0, ty0, tx1, ty1 = 0.42 * k, 0.42 * k, W - 0.42 * k, D - 0.42 * k
        if tx1 - tx0 < 0.5 * k:
            tx0, tx1 = 0.05 * k, W - 0.05 * k
    cw, cd = 0.44 * k, 0.42 * k
    cor_c = _mix(TECIDOS[int(rng.integers(len(TECIDOS)))], (0.85, 0.82, 0.78), 0.45)
    if cadeiras:
        lw = tx1 - tx0
        lh = ty1 - ty0
        nx = max(1, int(lw / (0.58 * k)))
        ny = 1 if lh > 0.95 * k else 0
        for i in range(nx):
            cx = tx0 + lw * (i + 0.5) / nx
            for yy, sgn in ((ty0, -1), (ty1, 1)):
                y0 = yy - cd + 0.12 * k if sgn < 0 else yy - 0.12 * k
                _rr(d, (cx - cw / 2, y0, cx + cw / 2, y0 + cd), 0.06 * k, _cor(cor_c) + (255,),
                    _cor(_mix(cor_c, (0, 0, 0), 0.3)) + (255,), max(1, int(0.006 * k)))
        for j in range(ny):
            cy = ty0 + lh / 2
            for xx, sgn in ((tx0, -1), (tx1, 1)):
                x0 = xx - cd + 0.12 * k if sgn < 0 else xx - 0.12 * k
                _rr(d, (x0, cy - cw / 2, x0 + cd, cy + cw / 2), 0.06 * k, _cor(cor_c) + (255,),
                    _cor(_mix(cor_c, (0, 0, 0), 0.3)) + (255,), max(1, int(0.006 * k)))
    tampo, td = _novo(tx1 - tx0, ty1 - ty0)
    _rr(td, (0, 0, tx1 - tx0, ty1 - ty0), 0.03 * k, _cor(MADEIRA) + (255,))
    tampo = _sombreado(_veio_madeira(tampo, rng, 0.10, horizontal=(tx1 - tx0) >= (ty1 - ty0)), 0.10)
    im.alpha_composite(tampo, (int(round(tx0)), int(round(ty0))))
    d = ImageDraw.Draw(im)
    # centro de mesa
    r = 0.09 * k
    cx, cy = (tx0 + tx1) / 2, (ty0 + ty1) / 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_cor((0.93, 0.92, 0.88)) + (255,))
    d.ellipse((cx - r * 0.6, cy - r * 0.6, cx + r * 0.6, cy + r * 0.6), fill=_cor((0.42, 0.60, 0.32)) + (255,))
    return im, m / k


def sp_mesa_centro(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.04 * k, _cor(_mix(MADEIRA, (0.95, 0.93, 0.9), 0.25)) + (255,))
    im = _veio_madeira(im, rng, 0.08, horizontal=W >= D)
    d = ImageDraw.Draw(im)
    bw, bh = min(W, D) * 0.45, min(W, D) * 0.32
    d.rectangle((W * 0.18, D * 0.2, W * 0.18 + bw, D * 0.2 + bh), fill=_cor((0.25, 0.32, 0.42)) + (255,))
    r = min(W, D) * 0.14
    d.ellipse((W * 0.7 - r, D * 0.65 - r, W * 0.7 + r, D * 0.65 + r), fill=_cor((0.95, 0.94, 0.9)) + (255,))
    return _sombreado(im, 0.12)


def sp_tapete(W, D, k, rng):
    im, d = _novo(W, D)
    cor = _mix(TECIDOS[int(rng.integers(len(TECIDOS)))], (0.92, 0.90, 0.86), 0.55)
    _rr(d, (0, 0, W, D), 0.01 * k, _cor(cor) + (235,))
    b = 0.06 * k
    d.rectangle((b, b, W - b, D - b), outline=_cor(_mix(cor, (0, 0, 0), 0.18)) + (235,), width=max(1, int(0.015 * k)))
    return _textura_tecido(im, rng, 0.12, 1.5)


def sp_bancada(W, D, k, rng, poligono=None, cozinha=False):
    im, d = _novo(W, D)
    pedra = (0.20, 0.20, 0.21) if cozinha else (0.86, 0.85, 0.83)
    if poligono:
        d.polygon(poligono, fill=_cor(pedra) + (255,))
    else:
        _rr(d, (0, 0, W, D), 0.01 * k, _cor(pedra) + (255,))
    arr = np.asarray(im).astype(np.float32)
    n = _ruido(arr.shape[0], arr.shape[1], 1.3, rng)
    n2 = _fbm(arr.shape[0], arr.shape[1], max(4, 0.15 * k), rng, 3)
    arr[..., :3] *= (1 + (n[..., None] - 0.5) * (0.35 if cozinha else 0.04) + (n2[..., None] - 0.5) * 0.06)
    im = Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')
    return _sombreado(im, 0.14, raio=0.05 * k)


def sp_cuba(W, D, k, rng, inox=False):
    im, d = _novo(W, D)
    if inox:
        _rr(d, (0, 0, W, D), 0.03 * k, _cor(INOX) + (255,))
        _rr(d, (0.03 * k, 0.03 * k, W - 0.03 * k, D - 0.03 * k), 0.04 * k, _cor(_mix(INOX, (0.3, 0.3, 0.32), 0.25)) + (255,))
    else:
        d.ellipse((0, 0, W, D), fill=_cor(LOUCA) + (255,), outline=_cor((0.78, 0.77, 0.75)) + (255,), width=max(1, int(0.006 * k)))
        e = min(W, D) * 0.13
        d.ellipse((e, e, W - e, D - e), fill=_cor((0.90, 0.90, 0.90)) + (255,))
    r = min(W, D) * 0.07
    d.ellipse((W / 2 - r, D * 0.55 - r, W / 2 + r, D * 0.55 + r), fill=_cor((0.55, 0.55, 0.56)) + (255,))
    # torneira (atrás)
    d.rectangle((W / 2 - 0.012 * k, -0.0, W / 2 + 0.012 * k, D * 0.28), fill=_cor((0.70, 0.71, 0.73)) + (255,))
    return _sombreado(im, 0.25)


def sp_bacia(W, D, k, rng):
    im, d = _novo(W, D)
    cx = W / 2
    cx_t = 0.18 * k
    _rr(d, (W * 0.06, 0, W * 0.94, cx_t), 0.03 * k, _cor(LOUCA) + (255,), _cor((0.78, 0.77, 0.75)) + (255,), max(1, int(0.006 * k)))
    d.ellipse((W * 0.08, cx_t - 0.04 * k, W * 0.92, D), fill=_cor(LOUCA) + (255,), outline=_cor((0.78, 0.77, 0.75)) + (255,),
              width=max(1, int(0.006 * k)))
    d.ellipse((W * 0.22, cx_t + 0.03 * k, W * 0.78, D - 0.06 * k), fill=_cor((0.88, 0.89, 0.90)) + (255,))
    d.ellipse((cx - 0.03 * k, cx_t * 0.4 - 0.015 * k, cx + 0.03 * k, cx_t * 0.4 + 0.015 * k), fill=_cor((0.75, 0.76, 0.78)) + (255,))
    return _sombreado(im, 0.22)


def sp_chuveiro(W, D, k, rng):
    """Box: bandeja levemente mais escura, ralo e vidro no lado aberto."""
    im, d = _novo(W, D)
    d.rectangle((0, 0, W, D), fill=(95, 110, 125, 46))
    r = 0.05 * k
    d.ellipse((W / 2 - r, D / 2 - r, W / 2 + r, D / 2 + r), fill=(150, 150, 152, 255), outline=(110, 110, 112, 255))
    for i in range(-2, 3):
        d.line([(W / 2 - r * 0.7, D / 2 + i * r * 0.3), (W / 2 + r * 0.7, D / 2 + i * r * 0.3)], fill=(110, 110, 112, 255), width=1)
    gw = max(2, 0.025 * k)
    d.rectangle((0, D - gw, W, D), fill=(170, 205, 225, 230))
    # chuveiro (ducha) no fundo
    rr = 0.09 * k
    d.ellipse((W / 2 - rr, 0.06 * k, W / 2 + rr, 0.06 * k + 2 * rr), fill=(200, 202, 205, 255), outline=(140, 140, 145, 255))
    return im


def sp_cooktop(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.02 * k, _cor(PRETO) + (255,))
    for fx, fy, fr in ((0.28, 0.3, 0.09), (0.72, 0.3, 0.07), (0.28, 0.72, 0.07), (0.72, 0.72, 0.09)):
        r = fr * k
        d.ellipse((W * fx - r, D * fy - r, W * fx + r, D * fy + r), outline=(115, 115, 118, 255), width=max(1, int(0.008 * k)))
    return _sombreado(im, 0.1)


def sp_geladeira(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.02 * k, _cor(INOX) + (255,))
    _grad_v(im, (0, 0, W, D), (0.84, 0.85, 0.86), (0.68, 0.69, 0.70))
    d = ImageDraw.Draw(im)
    d.line([(0, D - 0.04 * k), (W, D - 0.04 * k)], fill=(120, 122, 125, 255), width=max(1, int(0.008 * k)))
    return _sombreado(im, 0.12)


def sp_maquina(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.03 * k, _cor(BRANCO) + (255,), _cor((0.75, 0.75, 0.74)) + (255,), max(1, int(0.006 * k)))
    r = min(W, D) * 0.32
    d.ellipse((W / 2 - r, D * 0.55 - r, W / 2 + r, D * 0.55 + r), fill=(205, 210, 215, 255), outline=(150, 152, 155, 255),
              width=max(1, int(0.01 * k)))
    return _sombreado(im, 0.15)


def sp_tanque(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.03 * k, _cor(LOUCA) + (255,), _cor((0.75, 0.75, 0.74)) + (255,), max(1, int(0.006 * k)))
    _rr(d, (W * 0.1, D * 0.35, W * 0.9, D * 0.9), 0.03 * k, _cor((0.88, 0.89, 0.90)) + (255,))
    for i in range(5):
        y = D * (0.08 + i * 0.05)
        d.line([(W * 0.15, y), (W * 0.85, y)], fill=(200, 200, 200, 255), width=max(1, int(0.006 * k)))
    return _sombreado(im, 0.2)


def sp_churrasqueira(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.01 * k, _cor((0.62, 0.33, 0.24)) + (255,))
    arr = np.asarray(im).astype(np.float32)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w]
    tij = 0.065 * k
    linha = ((yy % tij) < max(1, 0.01 * k)) | (((xx + (yy // tij % 2) * tij) % (tij * 2)) < max(1, 0.01 * k))
    arr[linha, :3] = arr[linha, :3] * 0.6 + 200 * 0.4
    im = Image.fromarray(arr.clip(0, 255).astype(np.uint8), 'RGBA')
    d = ImageDraw.Draw(im)
    gx0, gy0, gx1, gy1 = W * 0.18, D * 0.2, W * 0.82, D * 0.8
    d.rectangle((gx0, gy0, gx1, gy1), fill=_cor((0.15, 0.14, 0.14)) + (255,))
    for i in range(1, 10):
        x = gx0 + (gx1 - gx0) * i / 10
        d.line([(x, gy0), (x, gy1)], fill=(120, 120, 120, 255), width=max(1, int(0.005 * k)))
    return _sombreado(im, 0.12)


def sp_escrivaninha(W, D, k, rng):
    im, d = _novo(W, D + 0.35 * k)
    tampo, td = _novo(W, D)
    _rr(td, (0, 0, W, D), 0.015 * k, _cor(MADEIRA_CLARA) + (255,))
    tampo = _sombreado(_veio_madeira(tampo, rng, 0.08), 0.1)
    cw = 0.46 * k
    _rr(d, (W / 2 - cw / 2, D - 0.1 * k, W / 2 + cw / 2, D + 0.33 * k), 0.08 * k, _cor((0.25, 0.26, 0.28)) + (255,))
    im.alpha_composite(tampo, (0, 0))
    d = ImageDraw.Draw(im)
    lw = min(W * 0.4, 0.35 * k)
    d.rectangle((W / 2 - lw / 2, D * 0.35, W / 2 + lw / 2, D * 0.35 + 0.22 * k), fill=(60, 62, 66, 255))
    return im, 0.0


def sp_planta(W, D, k, rng):
    s = max(W, D)
    im, d = _novo(s, s)
    d.ellipse((s * 0.12, s * 0.12, s * 0.88, s * 0.88), fill=(150, 110, 80, 255))
    for i in range(14):
        a = rng.random() * 2 * math.pi
        rr = s * (0.12 + rng.random() * 0.22)
        cx, cy = s / 2 + math.cos(a) * rr * 0.6, s / 2 + math.sin(a) * rr * 0.6
        r = s * (0.12 + rng.random() * 0.1)
        g = _mix((0.25, 0.45, 0.18), (0.48, 0.66, 0.30), rng.random())
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=_cor(g) + (255,))
    return _sombreado(im, 0.25)


def sp_generico(W, D, k, rng):
    im, d = _novo(W, D)
    _rr(d, (0, 0, W, D), 0.025 * k, _cor((0.86, 0.84, 0.80)) + (255,), _cor(CONTORNO) + (255,), max(1, int(0.006 * k)))
    return _sombreado(im, 0.14)


CATALOGO = [
    # (regex no texto família+tipo+categoria, função, camada)   camada 0 = no chão (tapete); -1 = não desenha
    (r'TORNEIRA|ASSENTO|REGISTRO|RALO|CHUVEIRINHO|DUCHA HIGIEN|ACABAMENTO|SIFAO|VALVULA|PAPELEIRA|SABONETEIRA|'
     r'CABIDE|TOALHEIRO|ESPELHO|INTERRUPTOR|TOMADA', 'ignorar', -1),
    (r'TAPETE', 'tapete', 0),
    (r'BELICHE|CAMA|BED', 'cama', 2),
    (r'SOFA|SOFÁ|ESTOFADO|CHAISE', 'sofa', 2),
    (r'POLTRONA|ARMCHAIR|PUFF|PUFE', 'poltrona', 2),
    (r'GUARDA.?ROUPA|ROUPEIRO|ARMARIO|ARMÁRIO|CLOSET|WARDROBE', 'guarda_roupa', 2),
    (r'CRIADO|CABECEIRA|MESA DE APOIO|NIGHT', 'criado', 2),
    (r'RACK|PAINEL TV|HOME THEATER|ESTANTE', 'rack', 2),
    (r'MESA DE CENTRO|MESA CENTRO|COFFEE', 'mesa_centro', 2),
    (r'ESCRIVANINHA|MESA DE ESCRITORIO|MESA ESCRITORIO|DESK|ESTUDO', 'escrivaninha', 2),
    (r'MESA|TABLE', 'mesa', 2),
    (r'CADEIRA|CHAIR|BANQUETA', 'poltrona', 2),
    (r'COOKTOP|FOGAO|FOGÃO|STOVE', 'cooktop', 3),
    (r'GELADEIRA|REFRIGERADOR|FRIDGE', 'geladeira', 2),
    (r'MAQUINA|MÁQUINA|LAVADORA|WASHER', 'maquina', 2),
    (r'TANQUE', 'tanque', 2),
    (r'CHURRASQ', 'churrasqueira', 2),
    (r'BANCADA|GRANITO|COUNTER|NICHO', 'bancada', 2),
    (r'CUBA|LAVATORIO|LAVATÓRIO|PIA|SINK|BASIN', 'cuba', 3),
    (r'BACIA|VASO SANIT|SANITARIO|SANITÁRIO|TOILET|BIDE', 'bacia', 2),
    (r'CHUVEIRO|BOX|DUCHA|SHOWER', 'chuveiro', 1),
    (r'PLANTA|VASO DE PLANTA|FLOREIRA|PLANT', 'planta', 2),
]


# lado que encosta na parede, por tipo:  'longo' | 'curto' | None (qualquer)
COSTAS = {'cama': 'curto', 'bacia': 'curto', 'sofa': 'longo', 'poltrona': None, 'guarda_roupa': 'longo',
          'rack': 'longo', 'bancada': 'longo', 'tanque': 'longo', 'maquina': None, 'geladeira': None,
          'criado': None, 'churrasqueira': 'longo', 'cuba': None, 'escrivaninha': 'longo', 'cooktop': None}


def _encostar(o, nome, dist_m, tela):
    """Família com eixo 'torto' (frente para a parede, sofá com o lado
    comprido no fundo...): a peça é girada para que o lado que encosta na
    parede seja as COSTAS. Só mexe quando a parede está claramente de um lado
    e não do outro — senão fica como o Revit mandou."""
    regra = COSTAS.get(nome, 'x')
    if regra == 'x':
        return o
    cs = [np.array(c, float) for c in o['cantos']]
    cen = sum(cs) / 4.0
    arestas = []
    for i in range(4):
        a, b = cs[i], cs[(i + 1) % 4]
        L = np.linalg.norm(b - a)
        meio = (a + b) / 2.0
        fora = meio - cen
        fora = fora / (np.linalg.norm(fora) or 1)
        ds = []
        for t in (0.15, 0.35, 0.5, 0.65, 0.85):
            p = a + (b - a) * t + fora * 0.05
            X, Y = tela.p(p[0], p[1])
            xi, yi = int(round(X)), int(round(Y))
            if 0 <= yi < dist_m.shape[0] and 0 <= xi < dist_m.shape[1]:
                ds.append(dist_m[yi, xi])
        arestas.append((i, L, float(np.percentile(ds, 40)) if ds else 9.0))
    longo = max(a[1] for a in arestas)
    if regra == 'longo':
        cand = [a for a in arestas if a[1] >= longo * 0.98]
    elif regra == 'curto':
        cand = [a for a in arestas if a[1] < longo * 0.98] or arestas
    else:
        cand = arestas
    fr = np.array(o.get('frente') or [0, -1], float)
    # aresta de costas atual = a mais oposta à frente
    atual = min(arestas, key=lambda a: np.dot((cs[a[0]] + cs[(a[0] + 1) % 4]) / 2.0 - cen, fr))
    if atual in cand:
        # eixo certo: só vira 180° se a FRENTE é que está colada na parede
        oposta = [a for a in arestas if a[0] == (atual[0] + 2) % 4][0]
        if not (oposta[2] <= 0.12 and atual[2] > 0.3):
            return o
        i = oposta[0]
    else:
        melhor = min(cand, key=lambda a: a[2])
        if melhor[2] > 0.15:
            return o
        i = melhor[0]
    novos = [cs[(i + j) % 4] for j in range(4)]           # c0-c1 = costas
    ey = novos[3] - novos[0]
    o = dict(o)
    o['cantos'] = [list(c) for c in novos]
    o['largura'] = float(np.linalg.norm(novos[1] - novos[0]))
    o['fundo'] = float(np.linalg.norm(ey))
    o['frente'] = list(ey / (np.linalg.norm(ey) or 1))
    o['_girado'] = True
    return o



def tipo_do_objeto(o):
    t = norm(u'{} {} {}'.format(o.get('familia', ''), o.get('tipo', ''), o.get('categoria', '')))
    for rx, nome, camada in CATALOGO:
        if re.search(norm(rx), t):
            return nome, camada
    if norm(o.get('categoria', '')) == 'CASEWORK':
        return 'bancada', 2
    return 'generico', 2


# ───────────────────────────────────────────────────────── renderizar
LUZ = (1.0, -1.35)        # sombra vai para a direita e para baixo na folha


def _limites(ficha):
    """Enquadramento: os AMBIENTES (com folga para o muro e a calçada). Parede
    que o recorte da vista pegou do vizinho não alarga a folha."""
    xs, ys = [], []
    for a in ficha.get('ambientes') or []:
        for anel in a.get('contorno') or []:
            for x, y in anel:
                xs.append(x)
                ys.append(y)
    if xs:
        return min(xs) - 0.2, min(ys) - 0.2, max(xs) + 0.2, max(ys) + 0.2
    for chave in ('lote', 'paredes'):
        for anel in ficha.get(chave) or []:
            for x, y in anel:
                xs.append(x)
                ys.append(y)
    for a in ficha.get('ambientes') or []:
        for anel in a.get('contorno') or []:
            for x, y in anel:
                xs.append(x)
                ys.append(y)
    if not xs:
        raise ValueError('A ficha não tem geometria (paredes / ambientes).')
    return min(xs), min(ys), max(xs), max(ys)


def _sombra(alfa, k, dx_m, dy_m, sigma_m, forca):
    """Máscara de sombra projetada: desloca e desfoca a máscara do objeto."""
    s = ndimage.shift(alfa, (dy_m * k, dx_m * k), order=1, mode='constant')
    s = ndimage.gaussian_filter(s, sigma_m * k)
    return np.clip(s * forca, 0, 0.9)


def _colar_sprite(tela, base, sombra, sprite, o, margem_m=0.0):
    """Mapeia o sprite (frente para baixo) para a pegada orientada do objeto."""
    cs = o['cantos']
    fr = o.get('frente') or [0, -1]
    c0, c1, c2, c3 = [np.array(c, float) for c in cs]
    ey_loc = c3 - c0
    if np.dot(np.array(fr, float), ey_loc) >= 0:     # frente do lado c3-c2
        O, ex, ey = c0, c1 - c0, c3 - c0
    else:
        O, ex, ey = c3, c2 - c3, c0 - c3
    W_m = np.linalg.norm(ex)
    D_m = np.linalg.norm(ey)
    if W_m < 1e-3 or D_m < 1e-3:
        return
    ux, uy = ex / W_m, ey / D_m
    O = O - ux * margem_m - uy * margem_m
    sw, sh = sprite.size
    # px do sprite → m:   P = O + ux*(sx/sw*(W+2m)) + uy*(sy/sh*(D+2m))
    Wt, Dt = W_m + 2 * margem_m, D_m + 2 * margem_m
    # m → px da tela
    def T(P):
        return np.array(tela.p(P[0], P[1]))
    Ot = T(O)
    ax = (T(O + ux * Wt) - Ot) / sw
    ay = (T(O + uy * Dt) - Ot) / sh
    corners = [Ot, Ot + ax * sw, Ot + ax * sw + ay * sh, Ot + ay * sh]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    X0, Y0 = int(math.floor(min(xs))) - 1, int(math.floor(min(ys))) - 1
    X1, Y1 = int(math.ceil(max(xs))) + 1, int(math.ceil(max(ys))) + 1
    X0c, Y0c = max(0, X0), max(0, Y0)
    X1c, Y1c = min(tela.W, X1), min(tela.H, Y1)
    if X1c <= X0c or Y1c <= Y0c:
        return
    M = np.array([[ax[0], ay[0]], [ax[1], ay[1]]])
    try:
        Mi = np.linalg.inv(M)
    except np.linalg.LinAlgError:
        return
    # saída (X, Y) → entrada (sx, sy):  [sx, sy] = Mi · ([X, Y] - Ot)
    ox, oy = X0c, Y0c
    a, b = Mi[0]
    c, d = Mi[1]
    out = sprite.transform((X1c - X0c, Y1c - Y0c), Image.AFFINE, (a, b, -(a * Ot[0] + b * Ot[1]) + a * ox + b * oy,
                                                                   c, d, -(c * Ot[0] + d * Ot[1]) + c * ox + d * oy),
                           resample=Image.BICUBIC)
    arr = np.asarray(out).astype(np.float32) / 255.0
    al = arr[..., 3]
    if sombra is not None:
        sombra[Y0c:Y1c, X0c:X1c] = np.maximum(sombra[Y0c:Y1c, X0c:X1c], al)
    base[Y0c:Y1c, X0c:X1c] = base[Y0c:Y1c, X0c:X1c] * (1 - al[..., None]) + arr[..., :3] * al[..., None]


def _desenhar_sprite(nome, W, D, k, rng, o, tela):
    """Devolve (sprite RGBA, margem em m)."""
    Wp, Dp = W * SS, D * SS
    kk = k * SS
    t = norm(u'{} {}'.format(o.get('familia', ''), o.get('tipo', '')))
    margem = 0.0
    if nome == 'cama':
        im = sp_cama(Wp, Dp, kk, rng)
    elif nome == 'sofa':
        im = sp_sofa(Wp, Dp, kk, rng)
    elif nome == 'poltrona':
        im = sp_sofa(Wp, Dp, kk, rng, lugares=1)
    elif nome == 'guarda_roupa':
        im = sp_guarda_roupa(Wp, Dp, kk, rng)
    elif nome == 'criado':
        im = sp_criado(Wp, Dp, kk, rng)
    elif nome == 'rack':
        im = sp_rack(Wp, Dp, kk, rng)
    elif nome == 'mesa_centro':
        im = sp_mesa_centro(Wp, Dp, kk, rng)
    elif nome == 'mesa':
        im, margem = sp_mesa(Wp, Dp, kk, rng)
    elif nome == 'escrivaninha':
        im, margem = sp_escrivaninha(Wp, Dp, kk, rng)
    elif nome == 'tapete':
        im = sp_tapete(Wp, Dp, kk, rng)
    elif nome == 'cooktop':
        im = sp_cooktop(Wp, Dp, kk, rng)
    elif nome == 'geladeira':
        im = sp_geladeira(Wp, Dp, kk, rng)
    elif nome == 'maquina':
        im = sp_maquina(Wp, Dp, kk, rng)
    elif nome == 'tanque':
        im = sp_tanque(Wp, Dp, kk, rng)
    elif nome == 'churrasqueira':
        im = sp_churrasqueira(Wp, Dp, kk, rng)
    elif nome == 'cuba':
        im = sp_cuba(Wp, Dp, kk, rng, inox=bool(re.search(r'INOX|COZINHA|ACO|TRAMONTINA|RETANGULAR|QUADRADA|FRANKE|TECNOCUBA', t)
                                                 and not re.search(r'LAVATORIO|LOUCA|PORCELANA|OVAL', t)))
    elif nome == 'bacia':
        im = sp_bacia(Wp, Dp, kk, rng)
    elif nome == 'chuveiro':
        im = sp_chuveiro(Wp, Dp, kk, rng)
    elif nome == 'planta':
        im = sp_planta(Wp, Dp, kk, rng)
    elif nome == 'bancada':
        poli = None
        cont = o.get('contorno')
        if cont:
            # o contorno do tampo só vale para bancada em L/U: tampo retangular
            # sai pela pegada (a face de cima costuma ser menor que a peça e
            # deixava um vão entre a bancada e a parede)
            xs_ = [p[0] for p in cont[0]]
            ys_ = [p[1] for p in cont[0]]
            caixa_ = (max(xs_) - min(xs_)) * (max(ys_) - min(ys_)) or 1.0
            a_ = _area_anel(cont[0])
            if a_ / caixa_ > 0.9 or a_ < 0.6 * (o.get('largura') or 0) * (o.get('fundo') or 0):
                cont = None
        if cont:
            # contorno real (bancada em L) no referencial do sprite
            poli = _contorno_no_sprite(o, cont[0], Wp, Dp)
        escura = bool(re.search(r'UBATUBA|PRETO|SAO GABRIEL|ABSOLUTO|GRAFITE|VERDE|NEGRO|BLACK', t))
        clara = bool(re.search(r'BRANCO|MARMORE|ITAUNAS|SIENA|DALLAS|QUARTZO BRANCO|NANOGLASS|BRANCA', t))
        im = sp_bancada(Wp, Dp, kk, rng, poli,
                        cozinha=escura or (not clara and bool(re.search(r'COZINHA|GOURMET|BALCAO|CHURRAS', t))))
        if re.search(r'CUBA|LAVATORIO|PIA', t) and not o.get('_tem_cuba'):
            # cuba que faz parte da família da bancada (não vem como peça separada)
            cw, cd = min(Wp * 0.8, 0.50 * kk), min(Dp * 0.7, 0.36 * kk)
            cuba = sp_cuba(cw, cd, kk, rng, inox=bool(re.search(r'COZINHA|INOX|QUADRADA|RETANGULAR', t))
                           and not re.search(r'OVAL|BANHEIRO|LAVATORIO', t))
            im.alpha_composite(cuba, (int((Wp - cw) / 2), int(Dp * 0.55 - cd / 2)))
    else:
        im = sp_generico(Wp, Dp, kk, rng)
    s = max(1, int(round(im.size[0] / SS))), max(1, int(round(im.size[1] / SS)))
    return im.resize(s, Image.LANCZOS), margem / SS if margem else 0.0


def _area_anel(anel):
    a = 0.0
    for i in range(len(anel)):
        x0, y0 = anel[i]
        x1, y1 = anel[(i + 1) % len(anel)]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _contorno_no_sprite(o, anel, Wp, Dp):
    cs = o['cantos']
    fr = np.array(o.get('frente') or [0, -1], float)
    c0, c1, c2, c3 = [np.array(c, float) for c in cs]
    if np.dot(fr, c3 - c0) >= 0:
        O, ex, ey = c0, c1 - c0, c3 - c0
    else:
        O, ex, ey = c3, c2 - c3, c0 - c3
    M = np.array([ex, ey]).T
    Mi = np.linalg.inv(M)
    out = []
    for p in anel:
        u, v = Mi.dot(np.array(p, float) - O)
        out.append((u * Wp, v * Dp))
    return out


def _arco(tela, d, c, r, p0, p1, cor, larg):
    cx, cy = tela.p(*c)
    rp = r * tela.k
    a0 = math.degrees(math.atan2(-(p0[1] - c[1]), p0[0] - c[0]))
    a1 = math.degrees(math.atan2(-(p1[1] - c[1]), p1[0] - c[0]))
    # menor arco entre a0 e a1
    da = (a1 - a0) % 360
    if da > 180:
        a0, a1 = a1, a0
    d.arc((cx - rp, cy - rp, cx + rp, cy + rp), a0, a1 if a1 > a0 else a1 + 360, fill=cor, width=larg)


MAPA_TABELA = {'ceramica50': 'ceramica', 'porcelanato60': 'porcelanato'}


def renderizar(ficha, px_m=None, max_px=5200, margem_m=0.25, rotulos=True, fonte=None, fonte_bold=None,
               override=None):
    """override: {nome do ambiente: material} — a escolha que a pessoa fez na
    tabela do gerador vale mais que o texto do Revit."""
    over = {}
    for kk, vv in (override or {}).items():
        over[norm(kk).strip()] = MAPA_TABELA.get(vv, vv)
    xmin, ymin, xmax, ymax = _limites(ficha)
    xmin -= margem_m
    ymin -= margem_m
    xmax += margem_m
    ymax += margem_m
    if px_m is None:
        px_m = min(220.0, max_px / max(xmax - xmin, ymax - ymin))
    tela = Tela(xmin, ymin, xmax, ymax, px_m)
    k = tela.k
    H, W = tela.H, tela.W
    img = np.ones((H, W, 3), np.float32)
    img[:] = (1.0, 1.0, 1.0)

    # ── 1. chão do lote (fora dos ambientes): concreto
    lote = ficha.get('lote') or []
    if lote:
        a_lote = tela.mascara(lote[:1])
    else:
        # sem lote na ficha: tudo o que paredes + ambientes + pisos cercam
        tudo = tela.mascara(ficha.get('paredes') or [], varios=True) > 0.5
        for a_ in ficha.get('ambientes') or []:
            tudo |= tela.mascara(a_.get('contorno') or []) > 0.5
        for p_ in ficha.get('pisos') or []:
            tudo |= tela.mascara(p_.get('contorno') or []) > 0.5
        a_lote = ndimage.binary_fill_holes(tudo).astype(np.float32)
    cx = _caixa(a_lote)
    if cx:
        _compor(img, textura(tela, cx, 'concreto', None, 'lote'), a_lote, cx)

    # ── 2. pisos genéricos (contrapiso, calçada, toposólido) por baixo
    pisos = ficha.get('pisos') or []
    acabamentos = []
    for i, p in enumerate(pisos):
        mat, tam, expl = classificar_piso(p.get('texto', ''))
        if mat in ('concreto', 'pintado') or norm(p.get('categoria', '')) == 'TOPOSOLID':
            a = tela.mascara(p.get('contorno') or [])
            c = _caixa(a)
            if c:
                _compor(img, textura(tela, c, mat, tam, 'piso%d' % i), a, c)
        else:
            acabamentos.append((i, p, mat, tam))

    # ── 3. ambientes pelo acabamento
    amb_alfa = []
    for i, amb in enumerate(ficha.get('ambientes') or []):
        mat, tam = material_do_ambiente(amb, ficha.get('pisos'))
        nn = norm(amb.get('nome', '')).strip()
        if nn in over and over[nn] in PALETA:
            mat = over[nn]
        a = tela.mascara(amb.get('contorno') or [])
        c = _caixa(a)
        amb_alfa.append((amb, a, mat))
        if c:
            _compor(img, textura(tela, c, mat, tam, amb.get('nome', i)), a, c)

    # ── 3b. vão de passagem dentro da casa (sem porta): piso do vizinho
    a_par0 = tela.mascara(ficha.get('paredes') or [], varios=True)
    tem_amb = np.zeros((H, W), bool)
    for amb, a, mat in amb_alfa:
        tem_amb |= a > 0.5
    r_ = 0.13 * k
    dil = ndimage.distance_transform_edt(~tem_amb) <= r_          # fechamento por
    fechado = ndimage.distance_transform_edt(dil) > r_ - 0.5      # distância (rápido)
    buraco = fechado & ~tem_amb & (a_par0 < 0.5)
    if buraco.any():
        _d, (iy, ix) = ndimage.distance_transform_edt(~tem_amb, return_indices=True)
        ok = buraco & (_d < 0.4 * k)
        img[ok] = img[iy[ok], ix[ok]]

    # ── 4. pisos com acabamento por cima (porcelanato só na parte gourmet…)
    for i, p, mat, tam in acabamentos:
        a = tela.mascara(p.get('contorno') or [])
        c = _caixa(a)
        if c:
            _compor(img, textura(tela, c, mat, tam, 'acab%d' % i), a, c)

    # ── 5. paredes (máscara) e luz: oclusão no pé da parede + sombra projetada
    paredes = ficha.get('paredes') or []
    a_par = tela.mascara(paredes, varios=True)
    vaos = []
    for item in (ficha.get('portas') or []) + (ficha.get('janelas') or []):
        try:
            c = np.array(item['centro'], float)
            e = np.array(item.get('eixo') or [1, 0], float)
            e = e / (np.linalg.norm(e) or 1)
            n = np.array([-e[1], e[0]])
            L = float(item.get('largura') or 0.8) - 0.01
            t = float(item.get('espessura') or 0.15) + 0.04
            vaos.append([list(c - e * L / 2 - n * t / 2), list(c + e * L / 2 - n * t / 2),
                         list(c + e * L / 2 + n * t / 2), list(c - e * L / 2 + n * t / 2)])
        except Exception:
            pass
    # para virar móvel para a parede, janela conta como parede
    jan = []
    for item in ficha.get('janelas') or []:
        try:
            c = np.array(item['centro'], float)
            e = np.array(item.get('eixo') or [1, 0], float)
            e = e / (np.linalg.norm(e) or 1)
            n = np.array([-e[1], e[0]])
            L = float(item.get('largura') or 1.0)
            t = float(item.get('espessura') or 0.15)
            jan.append([list(c - e * L / 2 - n * t / 2), list(c + e * L / 2 - n * t / 2),
                        list(c + e * L / 2 + n * t / 2), list(c - e * L / 2 + n * t / 2)])
        except Exception:
            pass
    perto = np.maximum(a_par, tela.mascara(jan, varios=True)) if jan else a_par
    dist_enc = ndimage.distance_transform_edt(perto < 0.5) / k
    if vaos:
        a_par = a_par * (1 - tela.mascara(vaos, varios=True))
    dist = ndimage.distance_transform_edt(a_par < 0.5) / k
    ao = 1 - (0.20 * np.exp(-dist / 0.12) + 0.08 * np.exp(-dist / 0.45)) * a_lote
    img *= ao[..., None]
    sombra = _sombra(a_par, k, LUZ[0] * 0.07, LUZ[1] * 0.07, 0.05, 0.50) * a_lote
    img *= (1 - sombra[..., None] * (1 - a_par[..., None]))
    # luz que entra pela janela: mancha clara e quente no piso, só dentro
    a_int = np.zeros((H, W), np.float32)
    for amb, a, mat in amb_alfa:
        if mat not in ('grama', 'concreto'):
            a_int = np.maximum(a_int, a)
    luz = np.zeros((H, W), np.float32)
    for j in ficha.get('janelas') or []:
        c = np.array(j['centro'], float)
        L = float(j.get('largura') or 1.0)
        cx_, cy_ = tela.p(*c)
        r = (0.7 + L * 0.6) * k
        X0, X1 = int(max(0, cx_ - r)), int(min(W, cx_ + r))
        Y0, Y1 = int(max(0, cy_ - r)), int(min(H, cy_ + r))
        if X1 <= X0 or Y1 <= Y0:
            continue
        yy, xx = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        dd = np.hypot(xx - cx_, yy - cy_) / r
        luz[Y0:Y1, X0:X1] = np.maximum(luz[Y0:Y1, X0:X1], np.clip(1 - dd, 0, 1) ** 1.6)
    luz = ndimage.gaussian_filter(luz, 0.1 * k) * a_int
    img = img * (1 + luz[..., None] * np.array([0.10, 0.08, 0.04], np.float32))

    # ── 6. escada / traços
    imgP = Image.fromarray((img.clip(0, 1) * 255).astype(np.uint8), 'RGB')
    d = ImageDraw.Draw(imgP)
    for t in ficha.get('tracos') or []:
        for anel in t.get('contorno') or []:
            d.polygon(tela.pts(anel), fill=(214, 208, 198))
        for ln in t.get('linhas') or []:
            d.line(tela.pts(ln), fill=(120, 116, 110), width=max(1, int(0.012 * k)))
    img = np.asarray(imgP).astype(np.float32) / 255.0
    img = img.copy()

    # ── 7. móveis: sombra primeiro, peça por cima; tapete antes de tudo
    objs = []
    for o in ficha.get('objetos') or []:
        nome, camada = tipo_do_objeto(o)
        if camada < 0:
            continue
        try:
            o = _encostar(o, nome, dist_enc, tela)
        except Exception:
            pass
        objs.append((camada, nome, o))
    # bancada que já tem uma cuba como peça separada não ganha outra desenhada
    cubas = [o for c_, n_, o in objs if n_ == 'cuba']
    for c_, n_, o in objs:
        if n_ != 'bancada':
            continue
        cs = np.array(o['cantos'], float)
        x0, y0 = cs.min(axis=0)
        x1, y1 = cs.max(axis=0)
        for cb in cubas:
            cc = np.array(cb['cantos'], float).mean(axis=0)
            if x0 - 0.02 <= cc[0] <= x1 + 0.02 and y0 - 0.02 <= cc[1] <= y1 + 0.02:
                o['_tem_cuba'] = True
                break
    objs.sort(key=lambda t: t[0])
    sombra_mov = np.zeros((H, W), np.float32)
    pend = []
    for camada, nome, o in objs:
        W_m = float(o.get('largura') or 0)
        D_m = float(o.get('fundo') or 0)
        if W_m < 0.05 or D_m < 0.05:
            continue
        rng = _rng(nome, round(o['cantos'][0][0], 2), round(o['cantos'][0][1], 2))
        sprite, margem = _desenhar_sprite(nome, W_m * k, D_m * k, k, rng, o, tela)
        pend.append((camada, nome, o, sprite, margem))
    # sombra de todos os móveis "em pé" (camada ≥ 2) de uma vez
    for camada, nome, o, sprite, margem in pend:
        if camada == 0:
            _colar_sprite(tela, img, None, sprite, o, margem)
    tmp = np.zeros((H, W, 3), np.float32)
    for camada, nome, o, sprite, margem in pend:
        if camada >= 2:
            _colar_sprite(tela, tmp, sombra_mov, sprite, o, margem)
    s = _sombra(sombra_mov, k, LUZ[0] * 0.035, LUZ[1] * 0.035, 0.035, 0.45)
    img *= (1 - s[..., None])
    for camada, nome, o, sprite, margem in pend:
        if camada >= 1:
            _colar_sprite(tela, img, None, sprite, o, margem)

    # ── 8. portas: soleira + folha + arco
    imgP = Image.fromarray((img.clip(0, 1) * 255).astype(np.uint8), 'RGB')
    d = ImageDraw.Draw(imgP, 'RGBA')
    for po in ficha.get('portas') or []:
        c = np.array(po['centro'], float)
        e = np.array(po.get('eixo') or [1, 0], float)
        e = e / (np.linalg.norm(e) or 1)
        n = np.array([-e[1], e[0]])
        L = float(po.get('largura') or 0.8)
        t = float(po.get('espessura') or 0.15)
        q = [c - e * L / 2 - n * t / 2, c + e * L / 2 - n * t / 2, c + e * L / 2 + n * t / 2, c - e * L / 2 + n * t / 2]
        d.polygon([tela.p(*p) for p in q], fill=(196, 186, 172, 255))
        for arc in (po.get('arcos') or [])[:2]:
            h = arc['centro']
            pa, pb = np.array(arc['p0'], float), np.array(arc['p1'], float)
            da_ = abs(np.dot(pa - c, n))
            db_ = abs(np.dot(pb - c, n))
            p0, p1 = (list(pa), list(pb)) if db_ >= da_ else (list(pb), list(pa))
            _arco(tela, d, h, arc['raio'], p0, p1, (110, 104, 98, 150), max(1, int(0.008 * k)))
            # folha
            hv = np.array(h, float)
            dv = np.array(p1, float) - hv
            dl = np.linalg.norm(dv) or 1
            nv = np.array([-dv[1], dv[0]]) / dl * 0.035
            fol = [hv, hv + dv, hv + dv + nv, hv + nv]
            d.polygon([tela.p(*p) for p in fol], fill=_cor(_mix(MADEIRA, (1, 1, 1), 0.15)) + (255,),
                      outline=(90, 70, 50, 255))
        if not po.get('arcos'):
            # correr: duas folhas desencontradas
            for s_, off in ((-1, 0.02), (1, -0.02)):
                a0 = c - e * L / 2 * (1 if s_ < 0 else 0) + n * off
                fol = [a0, a0 + e * L / 2 * 1.05, a0 + e * L / 2 * 1.05 + n * 0.03, a0 + n * 0.03]
                d.polygon([tela.p(*p) for p in fol], fill=_cor(MADEIRA) + (255,))

    # ── 9. paredes por cima de tudo
    img = np.asarray(imgP).astype(np.float32) / 255.0
    cor_par = np.array((0.22, 0.225, 0.24), np.float32)
    img = img * (1 - a_par[..., None]) + cor_par * a_par[..., None]
    # aresta de cima mais clara (luz) — dá volume ao corte
    borda = np.clip(a_par - ndimage.shift(a_par, (-LUZ[1] * 0.6, -LUZ[0] * 0.6), order=1), 0, 1)
    img += borda[..., None] * 0.10

    # ── 10. janelas: peitoril + vidro (no vão da parede)
    imgP = Image.fromarray((img.clip(0, 1) * 255).astype(np.uint8), 'RGB')
    d = ImageDraw.Draw(imgP, 'RGBA')
    for j in ficha.get('janelas') or []:
        c = np.array(j['centro'], float)
        e = np.array(j.get('eixo') or [1, 0], float)
        e = e / (np.linalg.norm(e) or 1)
        n = np.array([-e[1], e[0]])
        L = float(j.get('largura') or 1.0)
        t = float(j.get('espessura') or 0.15)
        q = [c - e * L / 2 - n * t / 2, c + e * L / 2 - n * t / 2, c + e * L / 2 + n * t / 2, c - e * L / 2 + n * t / 2]
        d.polygon([tela.p(*p) for p in q], fill=(236, 236, 234, 255), outline=(150, 150, 150, 255))
        for off in (-0.018, 0.018):
            a = c - e * L / 2 + n * off
            b = c + e * L / 2 + n * off
            d.line([tela.p(*a), tela.p(*b)], fill=(120, 170, 205, 255), width=max(1, int(0.012 * k)))
        for s_ in (-1, 1):
            a = c + e * s_ * L / 2 - n * t / 2
            b = c + e * s_ * L / 2 + n * t / 2
            d.line([tela.p(*a), tela.p(*b)], fill=(80, 80, 82, 255), width=max(1, int(0.01 * k)))

    # ── 11. lugar das etiquetas (miolo livre do ambiente)
    # ocupado = pegada de TODO móvel/louça + giro das portas: o nome e a área
    # não podem cair em cima da cama, da máquina ou do arco da porta
    oc = Image.new('L', (W, H), 0)
    do = ImageDraw.Draw(oc)
    op_ = Image.new('L', (W, H), 0)               # só o giro das portas
    dp_ = ImageDraw.Draw(op_)
    for camada, nome, o, sprite, margem in pend:
        if camada >= 1 and o.get('cantos'):       # tapete (0) pode ter texto por cima
            do.polygon([tela.p(*c_) for c_ in o['cantos']], fill=255)
    for po in ficha.get('portas') or []:
        for arc in (po.get('arcos') or [])[:2]:
            try:
                h = np.array(arc['centro'], float)
                pa, pb = np.array(arc['p0'], float) - h, np.array(arc['p1'], float) - h
                r_ = float(arc.get('raio') or np.linalg.norm(pa))
                a0, a1 = np.arctan2(pa[1], pa[0]), np.arctan2(pb[1], pb[0])
                da = (a1 - a0 + np.pi) % (2 * np.pi) - np.pi
                angs = a0 + da * np.linspace(0, 1, 12)
                giro = [tela.p(*h)] + [tela.p(h[0] + r_ * np.cos(t_), h[1] + r_ * np.sin(t_))
                                       for t_ in angs]
                do.polygon(giro, fill=255)
                dp_.polygon(giro, fill=255)
            except Exception:
                pass
    ocupado = np.asarray(oc) > 0
    porta_giro = np.asarray(op_) > 0
    etiquetas = []
    livre_mov = (sombra_mov < 0.3) & ~ocupado
    for amb, a, mat in amb_alfa:
        m = (a > 0.5) & livre_mov
        if m.sum() < 20:
            m = a > 0.5
        if m.sum() < 5:
            continue
        cx_ = _caixa(m.astype(np.float32), 2)
        r0, r1, c0, c1 = cx_
        dt = np.zeros(m.shape, np.float32)
        dt[r0:r1, c0:c1] = ndimage.distance_transform_edt(m[r0:r1, c0:c1])
        iy, ix = np.unravel_index(np.argmax(dt), dt.shape)
        lin = m[iy]
        col = m[:, ix]
        def _corrida(v, i):
            a_ = i
            while a_ > 0 and v[a_ - 1]:
                a_ -= 1
            b_ = i
            while b_ < len(v) - 1 and v[b_ + 1]:
                b_ += 1
            return a_, b_
        ha, hb = _corrida(lin, ix)
        va, vb = _corrida(col, iy)
        # mapas do miolo, reduzidos — a prancha procura neles um lugar onde o
        # bloco de texto caiba INTEIRO:
        #   'livre'  = chão sem móvel nem giro de porta (preferido)
        #   'livre2' = chão inteiro do ambiente menos o giro da porta (a
        #              etiqueta tem plaquinha branca, então pode ficar sobre a
        #              cama/o sofá quando o quarto é pequeno)
        # os dois afastados 6 cm da parede
        piso_ = a > 0.5
        R0, R1, C0, C1 = _caixa(piso_.astype(np.float32), 2)
        mg = max(1, int(round(0.06 * k)))
        # malha pela ÁREA (não pelo lado maior): faixa comprida e fina, como o
        # jardim lateral, não pode ficar com 12 células de largura
        passo = max(1, int(np.ceil(np.sqrt(float(R1 - R0) * (C1 - C0)) / 200.0)))
        mapas = {}
        for chave, mm in (('livre', piso_ & livre_mov), ('livre2', piso_ & ~porta_giro)):
            mm = mm[R0:R1, C0:C1]
            if mm.sum() > 50:
                mm = ndimage.binary_erosion(mm, iterations=mg)
            mapas[chave] = {'r0': int(R0), 'c0': int(C0), 'passo': passo,
                            'm': mm[::passo, ::passo].copy()}
        etiquetas.append({'nome': amb.get('nome', ''), 'area': amb.get('area'),
                          'livre': mapas['livre'], 'livre2': mapas['livre2'],
                          'x': float((ha + hb) / 2.0), 'y': float(iy),
                          'folga_px': float(dt[iy, ix]), 'meia_larg': float(hb - ha) / 2.0 - 0.05 * k,
                          'meia_alt': float(min(dt[iy, ix] * 2.5, (vb - va) / 2.0)) - 0.05 * k, 'material': mat})
    if rotulos:
        _rotular(imgP, etiquetas, k, fonte, fonte_bold)
    return imgP, etiquetas, tela


def _texto_img(txt, fo, cor):
    from PIL import ImageDraw as _D
    tmp = Image.new('RGBA', (8, 8))
    bb = _D.Draw(tmp).textbbox((0, 0), txt, font=fo)
    w_, h_ = bb[2] - bb[0] + 8, bb[3] - bb[1] + 8
    im = Image.new('RGBA', (w_, h_), (0, 0, 0, 0))
    d = _D.Draw(im)
    for ox in (-2, -1, 0, 1, 2):
        for oy in (-2, -1, 0, 1, 2):
            if ox or oy:
                d.text((4 - bb[0] + ox, 4 - bb[1] + oy), txt, font=fo, fill=(255, 255, 255, 80))
    d.text((4 - bb[0], 4 - bb[1]), txt, font=fo, fill=cor)
    return im


def _rotular(imgP, etiquetas, k, fonte=None, fonte_bold=None, mascaras=None):
    """Nome + área no miolo livre; a letra diminui até caber (mín. 60%) e,
    em ambiente estreito e comprido, o texto gira 90°. Quem não cabe nem
    assim fica marcado cabe=False (a prancha manda para a margem)."""
    from PIL import ImageFont
    cache = {}

    def f(tam, bold):
        ch = (tam, bold)
        if ch not in cache:
            try:
                cache[ch] = ImageFont.truetype((fonte_bold if bold else fonte) or
                                               ('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'), tam)
            except Exception:
                cache[ch] = ImageFont.load_default()
        return cache[ch]
    for e in etiquetas:
        nome = e['nome']
        area = u'{:.2f} m²'.format(e['area']).replace('.', ',') if e.get('area') else u''
        meia_w, meia_h = e.get('meia_larg', e['folga_px']), e.get('meia_alt', e['folga_px'])
        escolha = None
        for esc in (1.0, 0.9, 0.8, 0.7, 0.6):
            fb, fr = f(int(0.16 * k * esc), True), f(int(0.125 * k * esc), False)
            ib = _texto_img(nome, fb, (36, 40, 72, 255))
            ia = _texto_img(area, fr, (60, 64, 90, 255)) if area else None
            larg = max(ib.size[0], ia.size[0] if ia else 0)
            alt = ib.size[1] + (ia.size[1] if ia else 0)
            if larg <= 2 * meia_w and alt <= 2 * meia_h:
                escolha = (esc, ib, ia, False)
                break
            if larg <= 2 * meia_h and alt <= 2 * meia_w and meia_h > meia_w * 1.3:
                escolha = (esc, ib, ia, True)
                break
        e['cabe'] = escolha is not None
        if escolha is None:
            escolha = (0.6, ib, ia, meia_h > meia_w * 1.3)
        esc, ib, ia, girar = escolha
        e['escala_fonte'], e['girado'] = esc, girar
        bloco = Image.new('RGBA', (max(ib.size[0], ia.size[0] if ia else 0),
                                   ib.size[1] + (ia.size[1] - 6 if ia else 0)), (0, 0, 0, 0))
        bloco.alpha_composite(ib, ((bloco.size[0] - ib.size[0]) // 2, 0))
        if ia:
            bloco.alpha_composite(ia, ((bloco.size[0] - ia.size[0]) // 2, ib.size[1] - 6))
        if girar:
            bloco = bloco.rotate(90, expand=True)
        x, y = int(e['x'] - bloco.size[0] / 2), int(e['y'] - bloco.size[1] / 2)
        base = imgP.convert('RGBA')
        base.alpha_composite(bloco, (max(0, x), max(0, y)))
        imgP.paste(base.convert('RGB'))


if __name__ == '__main__':
    import json
    import sys
    import time
    t0 = time.time()
    ficha = json.load(open(sys.argv[1], encoding='utf-8'))
    im, et, tela = renderizar(ficha, fonte='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                              fonte_bold='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
    im.save(sys.argv[2])
    print('ok', im.size, round(time.time() - t0, 1), 's')
