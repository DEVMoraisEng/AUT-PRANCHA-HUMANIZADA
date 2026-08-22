/* Roda o programa Python dentro do navegador, numa thread separada.
   Thread separada porque a geração leva de 20 a 60 segundos: se rodasse na
   thread da página, o navegador acusaria "página não responde". */

/* Versão fixada de propósito: é a distribuição cujo lockfile foi conferido e
   que traz numpy, scipy, scikit-image, pillow e pymupdf prontos para wasm. */
const PYODIDE = "https://cdn.jsdelivr.net/npm/pyodide@314.0.5/";
importScripts(PYODIDE + "pyodide.js");

const MODULOS = ["extract.py", "segmentar.py", "pipeline.py", "humanizar.py",
                 "fachada.py", "prancha.py", "timbrado.py", "nomes.py", "api.py"];
const BINARIOS = ["fontes/DejaVuSans.ttf", "fontes/DejaVuSans-Bold.ttf",
                  "Modelo_papel_timbrado__Morais_Engenharia.docx"];

let pyodide = null;
let api = null;

const aviso = (texto, pct) => postMessage({ tipo: "andamento", texto, pct });

async function iniciar() {
  aviso("Carregando o motor Python…", 5);
  pyodide = await loadPyodide({ indexURL: PYODIDE });

  aviso("Baixando as bibliotecas de cálculo (só na primeira vez)…", 15);
  await pyodide.loadPackage(["numpy", "scipy", "scikit-image", "pillow", "pymupdf"]);

  aviso("Instalando o programa…", 70);
  pyodide.FS.mkdirTree("/app/fontes");
  for (const nome of MODULOS) {
    const txt = await (await fetch("app/" + nome + "?v=" + Date.now())).text();
    pyodide.FS.writeFile("/app/" + nome, txt);
  }
  for (const nome of BINARIOS) {
    const buf = await (await fetch("app/" + nome)).arrayBuffer();
    pyodide.FS.writeFile("/app/" + nome, new Uint8Array(buf));
  }
  pyodide.runPython(`import sys; sys.path.insert(0, "/app")`);
  api = pyodide.pyimport("api");

  aviso("Pronto para usar.", 100);
  postMessage({ tipo: "pronto" });
}

onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.tipo === "iniciar") return await iniciar();

    if (m.tipo === "ler") {
      aviso("Lendo os ambientes da planta…", 40);
      const r = api.ler(m.planta, m.pagina || 0, m.escala || null);
      postMessage({ tipo: "leitura", dados: JSON.parse(r) });
      return;
    }

    if (m.tipo === "gerar") {
      aviso("Reconstruindo os ambientes…", 25);
      const r = api.gerar(
        m.planta, m.fachada, m.titulo, m.lote, m.moveis, m.paleta,
        m.pagina || 0, m.escala || null,
        pyodide.toPy(m.pisos || {}), pyodide.toPy(m.apelidos || {}),
        m.timbrado || null
      );
      aviso("Montando a prancha…", 90);
      postMessage({ tipo: "gerado", dados: JSON.parse(r) });
      return;
    }
  } catch (err) {
    postMessage({ tipo: "erro", texto: String(err && err.message ? err.message : err) });
  }
};
