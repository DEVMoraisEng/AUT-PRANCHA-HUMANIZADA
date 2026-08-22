# Gerador de prancha humanizada — Morais Engenharia e Construção

Protótipo funcional. Transforma a planta do Revit (PDF vetorial) + a fachada 3D
em uma prancha pronta no papel timbrado da empresa.

## Instalar

```
pip install pymupdf pillow numpy scipy scikit-image
```

## Rodar

```
python3 main.py \
  --planta PLANTA.pdf \
  --fachada 3D.pdf \
  --timbrado "Modelo_papel_timbrado__Morais_Engenharia.docx" \
  --titulo "CASA 1 E 2" \
  --lote 90.00 \
  --saida PRANCHA.pdf \
  --relatorio conferencia.json
```

## Como ele garante que não inventa nada

| etapa | de onde vem o dado |
|---|---|
| nome dos ambientes | texto do próprio PDF, com coordenada |
| área de cada ambiente | texto do próprio PDF (`7,80 m²`) |
| paredes, portas, janelas | vetores do próprio PDF |
| móveis, louças, carro, árvores | vetores do próprio PDF (preservados) |
| escala do desenho | deduzida e conferida (achou 1:50) |
| "2 quartos sendo 1 suíte" | contagem dos rótulos |
| área construída / quintal | soma dos rótulos |
| área do lote | **entrada do usuário** (`--lote`), não está na planta |

O programa reconstrói a região de cada ambiente e **compara com a área escrita
na planta**. Se algum ambiente sair da tolerância (`--tolerancia`, 8% por padrão)
ele avisa no terminal e devolve código de saída 1 — em vez de entregar errado
sem avisar.

## Arquivos

| arquivo | função |
|---|---|
| `extract.py`   | lê rótulos, áreas e coordenadas do PDF |
| `segmentar.py` | reconstrói a região de cada ambiente (Dijkstra com cota de área) |
| `pipeline.py`  | junta leitura + escala + segmentação + conferência |
| `humanizar.py` | repinta a planta (pisos, paredes, vegetação, etiquetas) |
| `fachada.py`   | trata a perspectiva 3D (tons, fundo, sombra de apoio) |
| `prancha.py`   | monta a folha A4 no timbrado |
| `timbrado.py`  | extrai a arte de fundo do `.docx` |
| `main.py`      | linha de comando |

## Onde entra (opcionalmente) a API da OpenAI

O desenho **não** passa por IA generativa — se passasse, ela redesenharia a casa
e as medidas mudariam. A API só faria sentido para:

- padronizar nomes de ambiente esquisitos vindos do Revit;
- escrever o texto de venda com o tom da empresa;
- ler planta **escaneada / em imagem** (aí sim é visão computacional).

Nada disso é obrigatório: o programa roda 100% offline.

## Regra de ouro: o que o projeto não nomeia, o programa não toca

O programa só repinta um trecho da planta se existir **um ambiente com nome e
área** ali. Escada, quintal, calçada, árvore, cota, marcação: sem rótulo, fica
exatamente como saiu do Revit.

Isso não é preguiça — é o que impede o programa de "sumir" com o que ele não
entendeu. Uma versão anterior preenchia todo o vazio do lote com o ambiente
mais próximo e apagou uma escada inteira. Agora, no máximo, um trecho fica com
a cara do desenho técnico; nunca desaparece.

## Acabamento de piso

O acabamento **não é adivinhado a partir do desenho**. Ele vem de uma tabela
explícita, que o programa imprime a cada execução:

| ambiente | acabamento |
|---|---|
| quarto, suíte, sala, cozinha, banho, área de serviço, hall | `ceramica50` |
| área permeável / grama | `grama` |
| **todo o resto** (garagem, área gourmet, jardim de inverno, área impermeável, calçada) | `concreto` |

O padrão de quem não está na tabela é **concreto** — o mais conservador.
Para corrigir um ambiente específico:

```
--piso "JD. DE INVERNO=ceramica50" --piso "GARAGEM/ Á. GOURMET=concreto"
```

A malha da cerâmica é desenhada no **módulo real de 50x50 cm**, na escala do
desenho, e em coordenada única para a planta inteira — por isso ela não quebra
na divisa entre um cômodo e outro.

Portas e janelas viram **soleira**: o programa fecha o vão com uma barreira
virtual, então o piso de um ambiente nunca vaza para o vizinho por baixo de uma
janela. O acabamento troca exatamente na soleira.

## Aparência

Por padrão a planta sai **neutra**: os ambientes se distinguem pela textura e
pelo tom, não pela cor. Para a versão mais quente: `--paleta cor`.
Não há contorno nem sombra em volta dos cômodos — só a parede.

O nome e a área ficam **sem tarja**, posicionados no ponto mais folgado de cada
ambiente (longe de parede, móvel e louça), com um contorno claro leve para
não sumir na textura. Nome grande em cômodo pequeno encolhe e, se precisar,
quebra em duas linhas.

A perspectiva 3D também é tratada: os tons do Revit passam por uma rampa quente,
o preto duro vira grafite, o fundo branco vira um degradê suave com sombra de
apoio embaixo da casa, dissolvida nas bordas para não deixar emenda no timbrado.
Para usar o 3D cru: `--fachada-crua`.

## Mobiliário

Por padrão (`--moveis traco`) o mobiliário aparece como está no projeto.

Com `--moveis blocos`, cada peça que já existe no desenho é repintada como
bloco: a pegada é preenchida com um tom da própria família de cor dela
(madeira, estofado, louça, eletro) e ganha sombra no piso. **A pegada, a
posição e o tamanho continuam sendo os do projeto** — nenhum móvel é inventado
nem movido.

## Nome na peça de venda

`nomes.py` traduz o nome de projeto para o nome de corretor:
"Á. PERMEÁVEL 01 (GRAMA)" → JARDIM, "JD. DE INVERNO" → JARDIM DE INVERNO,
"Á. SERVIÇO" → ÁREA DE SERVIÇO, "SUITE" → SUÍTE.
Para um caso específico: `--nome "HALL DESCOBERTO=VARANDA"`.
O de-para é impresso a cada execução. A área, a conferência e a tabela técnica
continuam usando o nome original.

## Escala e páginas

A escala é deduzida do desenho e conferida contra as áreas escritas
(1:50, 1:75, 1:100...). O programa **rerrasteriza** a planta para trabalhar
sempre com a mesma quantidade de pixels por metro. Se avisar que não confia
(planta com poucos ambientes rotulados), force com `--escala 100`.
`--pagina 1` processa a segunda página do PDF.

## Ajustes rápidos

- Acabamentos e tons: `humanizar.PALETAS`; tabela ambiente→acabamento: `humanizar.REGRAS`
- Vão máximo tratado como porta/janela: `pipeline._tapar_vaos(vao_max=1.2)`
- Tratamento do 3D: `fachada.RAMPA`, `fachada.humanizar(sombra=, luz=)`
- Cores da marca: `NAVY`, `PETROL`, `MINT` em `humanizar.py` e `prancha.py`
- Posição dos blocos na folha: `prancha.montar()`
