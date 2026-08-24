/* Roda o programa Python dentro do navegador, numa thread separada.
   Thread separada porque a geração leva de 20 a 60 segundos: se rodasse na
   thread da página, o navegador acusaria "página não responde".

   Tudo aqui é embrulhado em try/catch e reportado para a página. Worker que
   morre calado deixa a tela parada sem explicação — já aconteceu. */

const CDNS = [
  "https://cdn.jsdelivr.net/npm/pyodide@314.0.5/",
  "https://unpkg.com/pyodide@314.0.5/",
  "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/",
];

const MODULOS = ["extract.py", "segmentar.py", "pipeline.py", "humanizar.py",
                 "fachada.py", "prancha.py", "timbrado.py", "nomes.py",
                 "cores.py", "api.py"];
const BINARIOS = ["fontes/DejaVuSans.ttf", "fontes/DejaVuSans-Bold.ttf",
                  "Modelo_papel_timbrado__Morais_Engenharia.docx"];

let pyodide = null, api = null, base = null;

const aviso = (texto, pct) => postMessage({ tipo: "andamento", texto, pct });
const falha = (texto, detalhe) => postMessage({ tipo: "erro", texto, detalhe: detalhe || "" });

function carregarPyodideJs() {
  const erros = [];
  for (const url of CDNS) {
    try {
      importScripts(url + "pyodide.js");
      if (typeof loadPyodide === "function") { base = url; return; }
      erros.push(url + " carregou mas não expôs loadPyodide");
    } catch (e) {
      erros.push(url + " → " + (e && e.message ? e.message : e));
    }
  }
  throw new Error("Não consegui baixar o motor Python de nenhum endereço.\n" + erros.join("\n"));
}

async function baixar(caminho, binario) {
  const r = await fetch(caminho, { cache: "no-cache" });
  if (!r.ok) throw new Error("faltou o arquivo " + caminho + " (HTTP " + r.status + ")");
  return binario ? new Uint8Array(await r.arrayBuffer()) : await r.text();
}

async function iniciar() {
  aviso("Baixando o motor Python…", 5);
  carregarPyodideJs();

  aviso("Iniciando o motor…", 12);
  pyodide = await loadPyodide({ indexURL: base });

  aviso("Baixando as bibliotecas de cálculo (só na primeira vez, ~40 MB)…", 20);
  await pyodide.loadPackage(["numpy", "scipy", "scikit-image", "pillow", "pymupdf"]);

  aviso("Instalando o programa…", 75);
  pyodide.FS.mkdirTree("/app/fontes");
  for (const nome of MODULOS) {
    pyodide.FS.writeFile("/app/" + nome, await baixar("app/" + nome, false));
  }
  for (const nome of BINARIOS) {
    pyodide.FS.writeFile("/app/" + nome, await baixar("app/" + nome, true));
  }
  pyodide.runPython('import sys; sys.path.insert(0, "/app")');
  api = pyodide.pyimport("api");

  aviso("Pronto para usar.", 100);
  postMessage({ tipo: "pronto" });
}

onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.tipo === "iniciar") return await iniciar();
    if (!api) return falha("O motor ainda não terminou de carregar.");

    if (m.tipo === "ler") {
      aviso(m.ficha ? "Lendo os ambientes pela ficha do Revit…"
                    : "Deduzindo os ambientes da planta…", 45);
      const r = api.ler(m.planta, m.pagina || 0, m.escala || null,
                        !!m.semNumero, m.ficha || null);
      postMessage({ tipo: "leitura", dados: JSON.parse(r) });
      return;
    }

    if (m.tipo === "gerar") {
      aviso(m.ficha ? "Lendo os ambientes pela ficha do Revit…"
                    : "Reconstruindo os ambientes…", 25);
      const r = api.gerar(
        m.planta, m.fachada, m.titulo, m.lote,
        m.pagina || 0, m.escala || null,
        pyodide.toPy(m.pisos || {}), pyodide.toPy(m.apelidos || {}),
        m.timbrado || null, m.construida || null, m.quintal || null,
        !!m.semNumero, m.ficha || null
      );
      aviso("Montando a prancha…", 92);
      postMessage({ tipo: "gerado", dados: JSON.parse(r) });
      return;
    }
  } catch (err) {
    const t = String(err && err.message ? err.message : err);
    falha(t.split("\n")[0].slice(0, 220), t.slice(0, 3000));
  }
};

self.onerror = (ev) => {
  falha("O motor quebrou ao carregar.", String(ev && ev.message ? ev.message : ev));
};
