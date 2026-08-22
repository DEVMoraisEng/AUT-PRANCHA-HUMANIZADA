"""
Dicionario de nomes para a peca de venda.

O nome que sai do Revit e um nome de PROJETO ("Á. PERMEÁVEL 01 (GRAMA)").
Na prancha de venda ele vira um nome de CORRETOR ("JARDIM").
A area, a geometria e a tabela tecnica continuam usando o nome original -
so o rotulo mostrado muda, e o programa imprime o de-para a cada execucao.
"""
import re

# (padrao, nome de venda). O primeiro que casar manda.
APELIDOS = [
    (r"^Á\.?\s*PERME[AÁ]VEL.*", "JARDIM"),
    (r"^JD\.?\s*DE\s*INVERNO$", "JARDIM DE INVERNO"),
    (r"^Á\.?\s*SERVI[CÇ]O\s*E\s*GOURMET$", "ÁREA DE SERVIÇO E GOURMET"),
    (r"^Á\.?\s*SERVI[CÇ]O$", "ÁREA DE SERVIÇO"),
    (r"^Á\.?\s*GOURMET$", "ÁREA GOURMET"),
    (r"^Á\.?\s*IMPERME[AÁ]VEL$", "ÁREA IMPERMEÁVEL"),
    (r"^GARAGEM\s*/\s*Á\.?\s*GOURMET$", "GARAGEM E ÁREA GOURMET"),
    (r"^SUITE$", "SUÍTE"),
    (r"^AMBIENTE$", "CIRCULAÇÃO"),
]


SUFIXO_NUM = re.compile(r"\s*(?:N[º°]\s*)?\d{1,2}\s*$")


def sem_numeracao(nome):
    """QUARTO 01 -> QUARTO. Só tira número no FIM do nome, e nunca deixa
    o rótulo vazio (um ambiente chamado só "02" continua "02")."""
    limpo = SUFIXO_NUM.sub("", nome).strip()
    return limpo or nome


def bonito(nome, override=None, tirar_numero=False):
    n = " ".join(nome.upper().split())
    if override:
        for de, para in override.items():
            if " ".join(de.upper().split()) == n:
                return para.upper()
    saida = n
    for padrao, para in APELIDOS:
        if re.match(padrao, n):
            saida = para
            break
    return sem_numeracao(saida) if tirar_numero else saida


def tabela(amb, override=None, tirar_numero=False):
    """De-para aplicado, para o programa mostrar antes de gerar."""
    return [(a["nome"], bonito(a["nome"], override, tirar_numero)) for a in amb
            if bonito(a["nome"], override, tirar_numero)
            != " ".join(a["nome"].upper().split())]
