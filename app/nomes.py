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


def bonito(nome, override=None):
    n = " ".join(nome.upper().split())
    if override:
        for de, para in override.items():
            if " ".join(de.upper().split()) == n:
                return para.upper()
    for padrao, para in APELIDOS:
        if re.match(padrao, n):
            return para
    return n


def tabela(amb, override=None):
    """De-para aplicado, para o programa mostrar antes de gerar."""
    return [(a["nome"], bonito(a["nome"], override)) for a in amb
            if bonito(a["nome"], override) != " ".join(a["nome"].upper().split())]
