"""Extrai o fundo do papel timbrado direto do .docx da empresa."""
import zipfile, io, os
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
