#!/usr/bin/env python3
"""
Morais - gerador de prancha de venda.

    python3 main.py --planta PLANTA.pdf --fachada 3D.pdf \
                    --titulo "CASA 1 E 2" --lote 90.00 --saida PRANCHA.pdf

O que ele faz, nesta ordem:
 1. le a planta em PDF (vetorial) e extrai NOME + AREA + POSICAO de cada ambiente;
 2. descobre a escala do desenho e reconstroi a regiao de cada ambiente;
 3. CONFERE a area reconstruida contra a area escrita na planta e avisa se algo
    ficou fora da tolerancia;
 4. repinta a planta (piso por material, paredes na cor da marca, vegetacao),
    preservando 100% do mobiliario e das loucas do projeto original;
 5. monta planta + fachada + caracteristicas + quadro de areas no papel
    timbrado da empresa e grava o PDF final.

Nada e inventado: todo texto e todo numero da prancha sai do arquivo de entrada.
"""
import argparse, sys, json
import pipeline, humanizar, prancha, timbrado, nomes, cores


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--planta", default="PLANTA.pdf")
    p.add_argument("--fachada", default="3D.pdf")
    p.add_argument("--titulo", default="CASA")
    p.add_argument("--lote", type=float, default=None,
                   help="area do lote em m2 (dado de matricula; nao esta na planta)")
    p.add_argument("--saida", default="PRANCHA.pdf")
    p.add_argument("--timbrado", default="Modelo_papel_timbrado__Morais_Engenharia.docx",
                   help="o .docx do papel timbrado (a arte de fundo e extraida dele)")
    p.add_argument("--tolerancia", type=float, default=8.0,
                   help="erro maximo aceito por ambiente, em %%")
    p.add_argument("--fachada-crua", action="store_true",
                   help="usa o 3D como saiu do Revit, sem tratamento")
    p.add_argument("--piso", action="append", default=[], metavar="AMBIENTE=ACABAMENTO",
                   help='forca o acabamento de um ambiente, ex: --piso "JD. DE INVERNO=concreto". '
                        "acabamentos: ceramica50, concreto, grama")
    p.add_argument("--nome", action="append", default=[], metavar="DE=PARA",
                   help='renomeia um ambiente na peca de venda, ex: --nome "HALL DESCOBERTO=VARANDA"')
    p.add_argument("--escala", type=float, default=None,
                   help="forca a escala do desenho (ex: 100) quando a deteccao avisar que nao confia")
    p.add_argument("--pagina", type=int, default=0, help="pagina do PDF da planta (0 = primeira)")
    p.add_argument("--construida", type=float, default=None,
                   help="area construida em m2 (se nao informar, soma os ambientes cobertos)")
    p.add_argument("--quintal", type=float, default=None,
                   help="area de quintal em m2 (se nao informar, soma as areas descobertas)")
    # A prancha e peca de venda: ali o ambiente e QUARTO, nunca QUARTO 02.
    # Deixou de ser opcao. A flag continua aceita para nao quebrar script antigo.
    p.add_argument("--sem-numero", action="store_true", default=True,
                   help=argparse.SUPPRESS)
    p.add_argument("--ficha", default=None,
                   help="o .json de cores gravado pelo botao Planta Humanizada do "
                        "pyRevit. Com ele o programa LE os ambientes (cor por cor) "
                        "em vez de deduzir os limites pelo desenho")
    p.add_argument("--relatorio", default=None)
    a = p.parse_args()

    if a.timbrado.lower().endswith(".docx"):
        fundo, pecas = timbrado.extrair(a.timbrado), timbrado.pecas(a.timbrado)
    else:
        fundo, pecas = a.timbrado, None

    override = {}
    for reg in a.piso:
        k, _, v = reg.partition("=")
        override[k.strip()] = v.strip()

    apelidos = {}
    for reg in a.nome:
        k, _, v = reg.partition("=")
        apelidos[k.strip()] = v.strip()

    if a.ficha:
        P = cores.preparar(a.planta, a.ficha, pagina=a.pagina,
                           apelidos=apelidos, sem_numero=a.sem_numero)
        print(f"ambientes LIDOS da ficha do Revit: {len(P['amb'])} · escala 1:{P['escala']:.0f}")
    else:
        P = pipeline.preparar(a.planta, beta=250.0, pagina=a.pagina,
                              apelidos=apelidos, escala_fixa=a.escala,
                              sem_numero=a.sem_numero)
        print(f"escala do desenho DEDUZIDA: 1:{P['escala']:.0f}"
              f"{'' if P['confiavel'] else '  (NAO PADRAO - conferir!)'}")
    print(f"{'AMBIENTE':<28}{'PLANTA':>9}{'RECONSTRUIDO':>14}{'ERRO':>8}")
    fora = []
    for x in P["amb"]:
        flag = "" if x["confiavel"] else "  <-- NAO repintado: " + x["motivo"]
        if flag:
            fora.append(x["nome"])
        print(f"{x['nome']:<28}{x['area']:>9.2f}{x['medido']:>14.2f}{x['erro']:>7.1f}%{flag}")

    de_para = nomes.tabela(P["amb"], apelidos, a.sem_numero)
    if de_para:
        print("\nNOME NA PECA DE VENDA (mude com --nome \"DE=PARA\")")
        for de, para in de_para:
            print(f"  {de:<28}->  {para}")

    print("\nACABAMENTO DE PISO (mude com --piso \"AMBIENTE=acabamento\")")
    for nome, mat in humanizar.tabela_acabamentos(P["amb"], override):
        print(f"  {nome:<28}{mat}")

    dpi_saida = int(min(600, max(420, P["dpi"] * 1.25)))
    img, etiquetas = humanizar.desenhar(P, dpi_saida=dpi_saida, override=override)
    prancha.montar(img, a.fachada, P["amb"], a.titulo, a.saida,
                   area_lote=a.lote, timbrado=fundo,
                   humanizar_3d=not a.fachada_crua,
                   area_construida=a.construida, area_quintal=a.quintal,
                   pecas=pecas, etiquetas=etiquetas)
    print(f"\nprancha gravada em {a.saida}")

    if a.relatorio:
        json.dump([{k: v for k, v in x.items() if not k.startswith("_")}
                   for x in P["amb"]], open(a.relatorio, "w"),
                  ensure_ascii=False, indent=1)
    if fora:
        print("ATENCAO: revisar visualmente ->", ", ".join(fora))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
