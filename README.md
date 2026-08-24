# AUT-PRANCHA-HUMANIZADA

Gera a prancha de venda (planta humanizada + fachada 3D + quadro de áreas) no
papel timbrado da Morais, a partir do PDF vetorial que sai do Revit.

**Gerar:** https://devmoraiseng.github.io/AUT-PRANCHA-HUMANIZADA/gerar.html

A página roda o programa dentro do próprio navegador (Pyodide/WebAssembly).
Não há servidor: os PDFs não saem do computador de quem gera.

## O caminho recomendado: exportar pelo Revit

Instale a extensão de `revit/` no pyRevit (`revit/COMO_INSTALAR.md`) e use
**Morais → Exportar Tudo**. Saem três arquivos: a planta com cada ambiente
pintado com uma cor própria, a **ficha `.json`** dizendo qual cor é qual, e a
fachada 3D.

Com a ficha o gerador **lê** o limite de cada cômodo. Sem ela, ele **deduz** a
partir do desenho — e é da dedução que vinham os erros (escada virando banheiro,
grama entrando na casa por um vão de porta). Sem a ficha continua funcionando,
só volta a deduzir.

## Estrutura

| pasta | o que é |
|---|---|
| `revit/` | a extensão pyRevit (os três botões dentro do Revit) |
| `app/` | o programa (Python) e o modelo de papel timbrado |
| `entrada/` | onde você coloca `PLANTA.pdf` e `3D.pdf` |
| `exemplos/` | comparativos antes/depois |
| `.github/workflows/gerar.yml` | roda o programa pelo GitHub, sem instalar nada |
| `index.html` | página de apresentação |
| `gerar.html` + `gerar-worker.js` | o gerador que roda no navegador |

## Caminho alternativo: rodar pelo GitHub

1. `entrada/` → **Add file → Upload files** → sobe `PLANTA.pdf` e `3D.pdf`
2. **Actions → Gerar prancha → Run workflow** → preenche o título
3. Baixa o artefato **prancha** no fim da execução

## Rodar no computador

```bash
cd app
pip install -r requirements.txt
python main.py --planta ../entrada/PLANTA.pdf --ficha ../entrada/PLANTA.json \
               --fachada ../entrada/3D.pdf \
               --titulo "CASA 1 E 2" --lote 90.00 \
               --saida PRANCHA.pdf --relatorio conferencia.json
```

Detalhes de funcionamento, regras de acabamento e opções: `app/COMO_FUNCIONA.md`.

## Token

Não usa. Roda offline. Se um dia precisar, vai em
**Settings → Secrets and variables → Actions** — nunca no código.
