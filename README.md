# AUT-PRANCHA-HUMANIZADA

Gera a prancha de venda (planta humanizada + fachada 3D + quadro de áreas) no
papel timbrado da Morais, a partir do PDF vetorial que sai do Revit.

Página da automação: https://devmoraiseng.github.io/AUT-PRANCHA-HUMANIZADA/

## Estrutura

| pasta | o que é |
|---|---|
| `app/` | o programa (Python) e o modelo de papel timbrado |
| `entrada/` | onde você coloca `PLANTA.pdf` e `3D.pdf` |
| `exemplos/` | comparativos antes/depois |
| `.github/workflows/gerar.yml` | roda o programa pelo GitHub, sem instalar nada |
| `index.html` | página publicada no GitHub Pages |

## Rodar pelo GitHub (sem instalar nada)

1. `entrada/` → **Add file → Upload files** → sobe `PLANTA.pdf` e `3D.pdf`
2. **Actions → Gerar prancha → Run workflow** → preenche o título
3. Baixa o artefato **prancha** no fim da execução

## Rodar no computador

```bash
cd app
pip install -r requirements.txt
python main.py --planta ../entrada/PLANTA.pdf --fachada ../entrada/3D.pdf \
               --titulo "CASA 1 E 2" --lote 90.00 --moveis blocos \
               --saida PRANCHA.pdf --relatorio conferencia.json
```

Detalhes de funcionamento, regras de acabamento e opções: `app/COMO_FUNCIONA.md`.

## Token

Não usa. Roda offline. Se um dia precisar, vai em
**Settings → Secrets and variables → Actions** — nunca no código.
