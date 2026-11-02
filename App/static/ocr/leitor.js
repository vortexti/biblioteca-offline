// Segundo leitor de texto em imagens: Tesseract rodando dentro do navegador (tesseract.js, WebAssembly).
// Não depende do Windows: funciona em qualquer computador e no celular (aí quem faz o trabalho é o celular).
// Português + inglês ficam em /static/ocr/lang; nada vem da internet.
(function () {
  'use strict';
  var BASE = '/static/ocr/';
  var trabalhador = null;

  function carregarScript(src) {
    return new Promise(function (ok, erro) {
      if (document.querySelector('script[src="' + src + '"]')) return ok();
      var s = document.createElement('script');
      s.src = src; s.onload = ok;
      s.onerror = function () { erro(new Error('não carregou ' + src)); };
      document.head.appendChild(s);
    });
  }

  function paragrafos(texto) {
    // o Tesseract separa parágrafos com linha em branco; junta as linhas de dentro e une palavras hifenizadas
    return String(texto || '').split(/\n\s*\n/).map(function (p) {
      return p.split('\n').reduce(function (acc, l) {
        l = l.trim();
        if (!l) return acc;
        if (/-$/.test(acc) && /^[a-zà-ú]/.test(l)) return acc.slice(0, -1) + l;
        return acc ? acc + ' ' + l : l;
      }, '');
    }).filter(Boolean).join('\n\n');
  }

  function obterTrabalhador(aoProgresso) {
    if (trabalhador) return trabalhador;
    trabalhador = carregarScript(BASE + 'tesseract.min.js').then(function () {
      return Tesseract.createWorker(['por', 'eng'], 1, {
        workerPath: BASE + 'worker.min.js',
        corePath: BASE + 'core',
        langPath: BASE + 'lang',
        logger: function (m) {
          if (aoProgresso && m && m.status) aoProgresso(m.status, m.progress || 0);
        }
      });
    });
    trabalhador.catch(function () { trabalhador = null; });
    return trabalhador;
  }

  // imagem: Blob/File, <img>, <canvas> ou URL do mesmo site. Devolve {texto, segundos}.
  function lerComTesseract(imagem, aoProgresso) {
    var t0 = Date.now();
    return obterTrabalhador(aoProgresso).then(function (w) {
      return w.recognize(imagem);
    }).then(function (r) {
      return { texto: paragrafos(r.data && r.data.text), segundos: Math.round((Date.now() - t0) / 100) / 10 };
    });
  }

  // Desenha uma página de PDF num <canvas> com o pdf.js (para quando o Windows não pode desenhar).
  function desenharPaginaPdf(urlPdf, pagina) {
    return import(BASE + 'pdf.min.mjs').then(function (pdfjs) {
      pdfjs.GlobalWorkerOptions.workerSrc = BASE + 'pdf.worker.min.mjs';
      return pdfjs.getDocument({ url: urlPdf, disableAutoFetch: true }).promise;
    }).then(function (doc) {
      return doc.getPage(Math.min(pagina, doc.numPages)).then(function (pg) {
        var vp0 = pg.getViewport({ scale: 1 });
        var vp = pg.getViewport({ scale: Math.min(3, 2000 / vp0.width) });
        var c = document.createElement('canvas');
        c.width = Math.round(vp.width); c.height = Math.round(vp.height);
        // intent 'print': desenha de uma vez, mesmo com a aba em segundo plano (o modo 'display' espera a tela)
        return pg.render({ canvasContext: c.getContext('2d'), viewport: vp, intent: 'print' }).promise.then(function () {
          return { canvas: c, paginas: doc.numPages };
        });
      });
    });
  }

  var NOMES = { 'loading tesseract core': 'carregando o leitor', 'initializing tesseract': 'preparando o leitor',
    'loading language traineddata': 'carregando português e inglês', 'initializing api': 'preparando',
    'recognizing text': 'lendo o texto' };
  function descreverProgresso(status, p) {
    return (NOMES[status] || status) + (p ? ' ' + Math.round(p * 100) + '%' : '') + '…';
  }

  window.LeitorOCR = { tesseract: lerComTesseract, pdfPagina: desenharPaginaPdf, progresso: descreverProgresso };
})();
