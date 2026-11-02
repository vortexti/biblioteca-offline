// Biblioteca Offline — interface. JavaScript simples, sem dependências.
'use strict';

var $ = function (s, r) { return (r || document).querySelector(s); };
var conteudo = $('#conteudo');
var ATALHOS = [];
var TEMAS = [];

// ---------------------------------------------------------------- utilidades

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function tamanho(b) {
  if (!b) return '0';
  var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return b.toFixed(i >= 3 ? 1 : 0).replace('.', ',') + ' ' + u[i];
}
function api(url, opcoes) {
  return fetch(url, opcoes).then(function (r) {
    return r.json().then(function (j) {
      if (!r.ok) throw new Error(j.erro || ('erro ' + r.status));
      return j;
    });
  });
}
function postar(url, dados) {
  return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(dados || {}) });
}
function lerSessao(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }
function gravarSessao(k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }
function etiquetaIdioma(i) {
  if (!i) return '';
  return '<span class="etiqueta ' + (i === 'en' ? 'en' : '') + '">' + esc(i === 'pt' ? 'PT' : i === 'en' ? 'Inglês' : i) + '</span>';
}
function parametros() {
  var h = location.hash.replace(/^#\/?/, '');
  var i = h.indexOf('?');
  var rota = i >= 0 ? h.slice(0, i) : h;
  var p = new URLSearchParams(i >= 0 ? h.slice(i + 1) : '');
  return { rota: rota || 'inicio', p: p };
}
function irPara(rota, params) {
  var q = params ? new URLSearchParams(params).toString() : '';
  location.hash = '#/' + rota + (q ? '?' + q : '');
}

// Lê uma resposta NDJSON (um evento JSON por linha) conforme chega.
function lerStream(url, dados, aoEvento, sinal) {
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(dados), signal: sinal })
    .then(function (r) {
      if (!r.body || !r.body.getReader) {           // navegador antigo: espera tudo
        return r.text().then(function (t) { t.split('\n').forEach(function (l) { if (l.trim()) aoEvento(JSON.parse(l)); }); });
      }
      var leitor = r.body.getReader(), dec = new TextDecoder(), resto = '';
      function passo() {
        return leitor.read().then(function (x) {
          if (x.done) { if (resto.trim()) aoEvento(JSON.parse(resto)); return; }
          resto += dec.decode(x.value, { stream: true });
          var linhas = resto.split('\n');
          resto = linhas.pop();
          linhas.forEach(function (l) { if (l.trim()) { try { aoEvento(JSON.parse(l)); } catch (e) {} } });
          return passo();
        });
      }
      return passo();
    });
}

// Texto da IA -> HTML: parágrafos, listas, **negrito** e citações [n] clicáveis.
function formatarResposta(texto, fontes) {
  var porN = {};
  (fontes || []).forEach(function (f) { porN[f.n] = f; });
  var linhas = esc(texto).split(/\n/);
  var html = '', lista = null;
  function fecha() { if (lista) { html += '</' + lista + '>'; lista = null; } }
  linhas.forEach(function (l) {
    var m;
    if ((m = l.match(/^\s*(\d+)[.)]\s+(.*)$/))) {
      if (lista !== 'ol') { fecha(); html += '<ol>'; lista = 'ol'; }
      html += '<li>' + m[2] + '</li>';
    } else if ((m = l.match(/^\s*[-*•]\s+(.*)$/))) {
      if (lista !== 'ul') { fecha(); html += '<ul>'; lista = 'ul'; }
      html += '<li>' + m[1] + '</li>';
    } else if (l.trim() === '') {
      fecha();
    } else {
      fecha(); html += '<p>' + l + '</p>';
    }
  });
  fecha();
  html = html.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/^#{1,4}\s*/gm, '');
  html = html.replace(/\[(\d{1,2})\]/g, function (t, n) {
    var f = porN[n];
    return f ? '<a class="cita" href="' + esc(f.url) + '" target="_blank" title="' + esc(f.titulo) + '">[' + n + ']</a>' : t;
  });
  return html;
}

// ---------------------------------------------------------------- telas

var telas = {};

telas.inicio = function () {
  var viu = false;
  try { viu = !!localStorage.getItem('viu_ajuda'); } catch (e) {}
  var h = (viu ? '' : '<div class="aviso boas-vindas" id="boas-vindas"><b>Primeira vez aqui?</b> Veja o guia rápido de como usar a biblioteca ' +
      '(2 minutos). <a class="botao pequeno" href="#/ajuda">Abrir o guia</a> <button class="botao pequeno secundario" type="button" id="b-ja-sei">Já sei usar</button></div>') +
    '<section class="inicio-cab">' +
    '<h1>O que você precisa saber?</h1>' +
    '<p>Uma busca procura em todos os manuais e enciclopédias do SSD. Ex.: <i>como tratar água do rio</i>, <i>plantar mandioca</i>. ' + linkAjuda('buscar', 'Dicas de busca') + '</p>' +
    formBusca('') + '</section>' +
    blocoAtalhos('Atalhos por necessidade', ATALHOS.filter(function (a) { return !a.grupo; }), 0) +
    blocoAtalhos('Áreas do conhecimento', ATALHOS.filter(function (a) { return a.grupo === 'area'; }), 100) +
    '<div class="faixa-estado" id="faixa-estado">…</div>';
  conteudo.innerHTML = h;
  ligarBusca();
  var bj = $('#b-ja-sei');
  if (bj) bj.addEventListener('click', function () {
    try { localStorage.setItem('viu_ajuda', '1'); } catch (e) {}
    $('#boas-vindas').remove();
  });
  var c = $('#campo-busca'); if (c) c.focus();
  api('/api/situacao').then(function (s) {
    var e = [];
    e.push('<span>' + s.indice.documentos + ' documentos · ' + s.indice.paginas.toLocaleString('pt-BR') + ' páginas</span>');
    e.push('<span class="' + (s.kiwix.rodando ? '' : 'ruim') + '">' + (s.kiwix.rodando ? s.livros.length + ' enciclopédias' :
      (s.kiwix.iniciando ? 'enciclopédias abrindo…' : 'enciclopédias indisponíveis')) + '</span>');
    e.push('<span class="' + (s.ia.disponivel ? '' : 'ruim') + '">IA: ' + (s.ia.disponivel ? (s.ia.situacao === 'pronta' ? 'ligada' : 'pronta para ligar') : 'indisponível') + '</span>');
    if (s.progresso.rodando) e.push('<span>indexando ' + s.progresso.feitos + '/' + s.progresso.total + '…</span>');
    if (s.aviso_disco) e.push('<span class="ruim">' + esc(s.aviso_disco) + '</span>');
    var f = $('#faixa-estado'); if (f) f.innerHTML = e.join('');
  }).catch(function () {});
};

function blocoAtalhos(titulo, lista, base) {
  if (!lista.length) return '';
  return '<div class="rotulo">' + esc(titulo) + '</div><div class="atalhos">' + lista.map(function (a, i) {
    return '<a class="atalho' + (base ? ' area' : '') + '" href="#/busca?' + new URLSearchParams({ q: a.pt, en: a.en, atalho: a.nome }) + '">' +
      '<span class="num">' + (base ? 'A' : '') + String(i + 1).padStart(2, '0') + '</span><b>' + esc(a.nome) + '</b></a>';
  }).join('') + '</div>';
}

function formBusca(valor) {
  return '<form class="busca" id="form-busca" role="search">' +
    '<input type="search" id="campo-busca" name="q" value="' + esc(valor) + '" placeholder="Buscar em toda a biblioteca…" autocomplete="off" aria-label="Buscar">' +
    '<button class="botao" type="submit">Buscar</button></form>';
}
function ligarBusca() {
  var f = $('#form-busca');
  if (!f) return;
  f.addEventListener('submit', function (ev) {
    ev.preventDefault();
    var q = $('#campo-busca').value.trim();
    if (q) irPara('busca', { q: q });
  });
}

telas.busca = function (p) {
  var q = p.get('q') || '', en = p.get('en') || '', atalho = p.get('atalho') || '';
  var tema = p.get('tema') || '', idioma = p.get('idioma') || '', tipo = p.get('tipo') || '';
  conteudo.innerHTML = formBusca(q) +
    '<div class="filtros">' +
    (atalho ? '<span class="etiqueta">Atalho: ' + esc(atalho) + '</span>' : '') +
    '<label>Tema <select id="f-tema"><option value="">todos</option>' + TEMAS.map(function (t) {
      return '<option' + (t === tema ? ' selected' : '') + '>' + esc(t) + '</option>'; }).join('') + '</select></label>' +
    '<label>Idioma <select id="f-idioma"><option value="">todos</option><option value="pt"' + (idioma === 'pt' ? ' selected' : '') +
    '>português</option><option value="en"' + (idioma === 'en' ? ' selected' : '') + '>inglês</option></select></label>' +
    '<label>Tipo <select id="f-tipo"><option value="">tudo</option><option value="manual"' + (tipo === 'manual' ? ' selected' : '') +
    '>só manuais</option><option value="enciclopedia"' + (tipo === 'enciclopedia' ? ' selected' : '') + '>só enciclopédias</option></select></label>' +
    (en ? '<span class="etiqueta en">também em inglês: ' + esc(en) + '</span>' :
      '<button class="botao pequeno secundario" id="b-ingles" type="button">+ incluir fontes em inglês</button>') +
    '</div>' +
    '<div class="status-linha" id="st-busca"></div>' +
    '<div class="colunas">' +
    '<section class="coluna' + (tipo === 'enciclopedia' ? ' oculto' : '') + '"><h2>Manuais <small id="n-docs"></small></h2><div id="r-docs"><p class="status-linha">Buscando…</p></div></section>' +
    '<section class="coluna' + (tipo === 'manual' ? ' oculto' : '') + '"><h2>Enciclopédias <small id="n-zim"></small></h2><div id="r-zim"><p class="status-linha">Buscando…</p></div></section>' +
    '</div>';
  ligarBusca();
  ['f-tema', 'f-idioma', 'f-tipo'].forEach(function (id) {
    $('#' + id).addEventListener('change', function () {
      var n = { q: q };
      if (en) n.en = en; if (atalho) n.atalho = atalho;
      var t = $('#f-tema').value, i = $('#f-idioma').value, ti = $('#f-tipo').value;
      if (t) n.tema = t; if (i) n.idioma = i; if (ti) n.tipo = ti;
      irPara('busca', n);
    });
  });
  var bi = $('#b-ingles');
  if (bi) bi.addEventListener('click', function () {
    bi.disabled = true; bi.textContent = 'traduzindo os termos com a IA…';
    api('/api/termos_en?q=' + encodeURIComponent(q)).then(function (r) {
      if (!r.en) { bi.textContent = 'IA indisponível: ' + (r.erro || ''); return; }
      var n = { q: q, en: r.en }; if (tema) n.tema = tema; if (idioma) n.idioma = idioma; if (tipo) n.tipo = tipo;
      irPara('busca', n);
    }).catch(function (e) { bi.textContent = 'Falhou: ' + e.message; });
  });
  if (!q) return;
  var base = 'q=' + encodeURIComponent(q) + (en ? '&en=' + encodeURIComponent(en) : '') +
    (idioma ? '&idioma=' + idioma : '');
  if (tipo !== 'enciclopedia') {
    api('/api/busca/docs?' + base + (tema ? '&tema=' + encodeURIComponent(tema) : '')).then(function (r) {
      $('#n-docs').textContent = r.resultados.length + ' documentos · ' + r.ms + ' ms';
      $('#r-docs').innerHTML = r.resultados.length ? r.resultados.map(htmlDoc).join('') :
        '<p class="vazio">Nada encontrado nos manuais.' + (r.indexando ? ' (A biblioteca ainda está sendo indexada; tente de novo em instantes.)' : '') + '</p>';
      ligarTraduzir($('#r-docs'));
    }).catch(function (e) { $('#r-docs').innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
  }
  if (tipo !== 'manual') {
    if (tema) { $('#r-zim').innerHTML = '<p class="vazio">O filtro de tema vale só para os manuais.</p>'; }
    api('/api/busca/zim?' + base).then(function (r) {
      $('#n-zim').textContent = r.resultados.length + ' artigos · ' + r.ms + ' ms';
      $('#r-zim').innerHTML = r.resultados.length ? r.resultados.map(htmlZim).join('') :
        '<p class="vazio">' + esc(r.erro || 'Nada encontrado nas enciclopédias.') + '</p>';
      ligarTraduzir($('#r-zim'));
    }).catch(function (e) { $('#r-zim').innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
  }
};

function htmlDoc(g) {
  var semTexto = g.situacao !== 'ok';
  return '<article class="resultado">' +
    '<a class="titulo" href="' + esc(g.url) + '" target="_blank">' + esc(g.titulo) + '</a>' +
    '<div class="meta"><span class="etiqueta">' + esc(g.tema) + '</span>' + etiquetaIdioma(g.idioma) +
    '<span class="etiqueta">' + esc((g.tipo || '').toUpperCase()) + (g.total_paginas ? ' · ' + g.total_paginas + (g.tipo === 'pdf' ? ' p.' : ' seções') : '') + '</span>' +
    (semTexto ? '<span class="etiqueta">sem texto pesquisável</span>' +
      (g.tipo === 'pdf' ? ' <a class="botao pequeno secundario" href="/ocr/' + g.doc_id + '" target="_blank">ler com OCR</a>' : '') : '') + '</div>' +
    g.paginas.filter(function (p) { return p.pagina > 0; }).map(function (p) {
      return '<p class="trecho"><a class="pag" href="' + esc(p.url) + '" target="_blank">' +
        (g.tipo === 'pdf' ? 'p. ' : '§ ') + p.pagina + '</a>' + p.trecho + '</p>';
    }).join('') +
    (g.idioma === 'en' && g.paginas.length ? '<div class="acoes-trecho"><button class="botao pequeno secundario" data-traduzir>traduzir este trecho</button></div>' : '') +
    '</article>';
}
function htmlZim(r) {
  return '<article class="resultado">' +
    '<a class="titulo" href="' + esc(r.url) + '" target="_blank">' + esc(r.titulo) + '</a>' +
    '<div class="meta"><span class="etiqueta">' + esc(r.livro) + '</span>' + etiquetaIdioma(r.idioma) + '</div>' +
    (r.trecho ? '<p class="trecho">' + r.trecho + '</p>' : '') +
    (r.idioma === 'en' && r.trecho ? '<div class="acoes-trecho"><button class="botao pequeno secundario" data-traduzir>traduzir este trecho</button></div>' : '') +
    '</article>';
}
function ligarTraduzir(raiz) {
  raiz.querySelectorAll('[data-traduzir]').forEach(function (b) {
    b.addEventListener('click', function () {
      var art = b.closest('.resultado');
      var t = Array.prototype.map.call(art.querySelectorAll('.trecho'), function (x) {
        var c = x.cloneNode(true); var a = c.querySelector('.pag'); if (a) a.remove(); return c.textContent.trim();
      }).join('\n\n');
      gravarSessao('traduzir', t);
      irPara('traduzir', { auto: '1' });
    });
  });
}

var controleIA = null;

telas.pergunte = function (p) {
  var q = p.get('q') || '';
  conteudo.innerHTML = '<section class="painel">' +
    '<h1>Pergunte à biblioteca</h1>' +
    '<p class="meta">A IA local responde <b>só com base nos manuais e enciclopédias do SSD</b> e mostra de onde tirou cada informação. ' + linkAjuda('pergunte', 'Como perguntar bem') + '</p>' +
    '<form id="form-perg"><textarea id="perg" placeholder="Ex.: como purificar água sem fervura?" required>' + esc(q) + '</textarea>' +
    '<p><button class="botao" type="submit" id="b-perg">Perguntar</button> ' +
    '<button class="botao secundario oculto" type="button" id="b-parar">Parar</button></p></form>' +
    '<div class="status-linha" id="st-perg"></div>' +
    '<div id="aviso-perg"></div>' +
    '<div class="resposta" id="resp"></div>' +
    '<div id="bloco-fontes" class="oculto"><div class="rotulo" style="margin-top:1.4rem">Fontes usadas</div><ol class="fontes" id="fontes"></ol></div>' +
    '</section>';
  $('#form-perg').addEventListener('submit', function (ev) { ev.preventDefault(); perguntar(); });
  $('#perg').addEventListener('keydown', function (ev) {
    if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); perguntar(); }
  });
  $('#b-parar').addEventListener('click', function () { if (controleIA) controleIA.abort(); });
  if (q && p.get('auto')) perguntar();
  else $('#perg').focus();
  prepararIA('#st-perg');
};

// Liga a IA em segundo plano assim que a tela abre (ela leva de segundos a 2 minutos para carregar).
function prepararIA(seletor) {
  postar('/api/ia/preparar').then(function (e) {
    var st = $(seletor);
    if (!st || st.textContent) return;
    if (!e.disponivel) st.textContent = 'IA indisponível: ' + (e.motivo || '');
    else if (e.situacao !== 'pronta') st.textContent = 'Preparando a IA em segundo plano… pode ir digitando.';
  }).catch(function () {});
}

function perguntar() {
  var pergunta = $('#perg').value.trim();
  if (!pergunta) return;
  if (controleIA) controleIA.abort();
  controleIA = window.AbortController ? new AbortController() : null;
  var texto = '', fontes = [];
  var resp = $('#resp'), st = $('#st-perg');
  resp.innerHTML = ''; resp.classList.add('cursor');
  $('#aviso-perg').innerHTML = ''; $('#fontes').innerHTML = ''; $('#bloco-fontes').classList.add('oculto');
  $('#b-perg').disabled = true; $('#b-parar').classList.remove('oculto');
  var inicio = Date.now();
  lerStream('/api/perguntar', { pergunta: pergunta }, function (ev) {
    if (ev.tipo === 'status') st.textContent = ev.texto;
    else if (ev.tipo === 'consulta_en') st.textContent = 'Buscando também em inglês: “' + ev.texto + '”';
    else if (ev.tipo === 'aviso') $('#aviso-perg').innerHTML = '<div class="aviso saude">⚠ ' + esc(ev.texto) + '</div>';
    else if (ev.tipo === 'fontes') {
      fontes = ev.fontes;
      if (fontes.length) {
        $('#bloco-fontes').classList.remove('oculto');
        $('#fontes').innerHTML = fontes.map(function (f) {
          return '<li><span class="n">[' + f.n + ']</span><span><a href="' + esc(f.url) + '" target="_blank">' + esc(f.titulo) + '</a>' +
            (f.pagina ? ' <span class="meta">— página ' + f.pagina + '</span>' : '') + ' ' + etiquetaIdioma(f.idioma) + '</span></li>';
        }).join('');
      }
    } else if (ev.tipo === 'texto') {
      texto += ev.texto;
      resp.innerHTML = formatarResposta(texto, fontes);
    } else if (ev.tipo === 'erro') {
      resp.innerHTML = '<p class="erro">' + esc(ev.texto) + '</p>';
    } else if (ev.tipo === 'fim') {
      st.textContent = 'Pronto em ' + Math.round((Date.now() - inicio) / 1000) + ' s.';
    }
  }, controleIA && controleIA.signal).catch(function (e) {
    if (e.name === 'AbortError') st.textContent = 'Parado.';
    else resp.innerHTML += '<p class="erro">Falhou: ' + esc(e.message) + '</p>';
  }).then(function () {
    resp.classList.remove('cursor');
    var b = $('#b-perg'); if (b) b.disabled = false;
    var bp = $('#b-parar'); if (bp) bp.classList.add('oculto');
  });
}

telas.traduzir = function (p) {
  var inicial = p.get('auto') ? (lerSessao('traduzir') || '') : '';
  conteudo.innerHTML = '<section class="painel"><h1>Traduzir</h1>' +
    '<p class="meta">Cole um texto em inglês, ou tire uma foto / escolha uma imagem com texto: o computador lê as letras (sem internet) e a IA local traduz. ' + linkAjuda('traduzir') + '</p>' +
    '<div class="foto-ocr"><label class="botao secundario" for="foto-in">📷 Ler texto de uma foto ou imagem</label>' +
    '<input type="file" id="foto-in" accept="image/*" capture="environment" hidden>' +
    '<span class="status-linha" id="st-foto"></span>' +
    '<button class="botao pequeno secundario oculto" type="button" id="b-outro-leitor"></button></div>' +
    '<form id="form-trad"><textarea id="trad-in" placeholder="Cole aqui o texto em inglês…">' + esc(inicial) + '</textarea>' +
    '<p><button class="botao" type="submit" id="b-trad">Traduzir</button> ' +
    '<button class="botao secundario" type="button" id="b-copiar-trad">Copiar texto</button></p></form>' +
    '<div class="status-linha" id="st-trad"></div><div class="cartao resposta oculto" id="trad-out"></div></section>';
  $('#form-trad').addEventListener('submit', function (ev) { ev.preventDefault(); traduzir(); });
  $('#b-copiar-trad').addEventListener('click', function () {
    var t = $('#trad-in'); t.select();
    try { if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t.value); else document.execCommand('copy'); } catch (e) {}
    this.textContent = 'Copiado!';
  });
  // dois leitores: o do Windows (no computador onde o app roda; rápido) e o Tesseract (no próprio navegador;
  // funciona em qualquer aparelho e costuma ler inglês melhor). Lê com o melhor disponível e oferece o outro.
  var foto = null, windowsOk = null;
  api('/api/ocr').then(function (o) { windowsOk = !!o.disponivel; }).catch(function () { windowsOk = false; });
  function lerFoto(leitor) {
    var st = $('#st-foto'), bo = $('#b-outro-leitor');
    bo.classList.add('oculto');
    var nome = leitor === 'windows' ? 'leitor do Windows' : 'Tesseract';
    st.textContent = 'Lendo o texto da imagem (' + nome + ')…';
    var pronto = leitor === 'windows' ?
      fetch('/api/ocr/imagem', { method: 'POST', headers: { 'Content-Type': foto.type || 'image/jpeg' }, body: foto })
        .then(function (r) { return r.json(); })
        .then(function (r) { if (!r.ok) throw new Error(r.erro || 'Não consegui ler a imagem.'); return r; }) :
      LeitorOCR.tesseract(foto, function (status, p) { st.textContent = 'Tesseract: ' + LeitorOCR.progresso(status, p); });
    pronto.then(function (r) {
      var outro = leitor === 'windows' ? 'tesseract' : (windowsOk ? 'windows' : null);
      if (outro) {
        bo.textContent = 'Não ficou bom? Ler de novo com o ' + (outro === 'windows' ? 'leitor do Windows' : 'Tesseract');
        bo.dataset.leitor = outro;
        bo.classList.remove('oculto');
      }
      if (!r.texto) { st.textContent = 'O ' + nome + ' não achou texto na imagem. Tente uma foto mais de perto, reta e com boa luz.'; return; }
      $('#trad-in').value = r.texto;
      st.textContent = 'Lido pelo ' + nome + ' em ' + String(r.segundos).replace('.', ',') + ' s. Confira e clique em Traduzir (ou Copiar).';
    }).catch(function (e) {
      st.textContent = 'Erro no ' + nome + ': ' + e.message;
      if (leitor === 'windows') { bo.textContent = 'Ler com o Tesseract'; bo.dataset.leitor = 'tesseract'; bo.classList.remove('oculto'); }
    });
  }
  $('#b-outro-leitor').addEventListener('click', function () { if (foto) lerFoto(this.dataset.leitor); });
  $('#foto-in').addEventListener('change', function () {
    var arq = this.files && this.files[0];
    if (!arq) return;
    foto = arq;
    this.value = '';
    lerFoto(windowsOk === false ? 'tesseract' : 'windows');
  });
  if (inicial) traduzir();
  else prepararIA('#st-trad');
};

function traduzir() {
  var t = $('#trad-in').value.trim();
  if (!t) return;
  var out = $('#trad-out'), st = $('#st-trad'), acc = '';
  out.classList.remove('oculto'); out.innerHTML = ''; out.classList.add('cursor');
  $('#b-trad').disabled = true;
  lerStream('/api/traduzir', { texto: t }, function (ev) {
    if (ev.tipo === 'status') st.textContent = ev.texto;
    else if (ev.tipo === 'texto') { acc += ev.texto; out.innerHTML = formatarResposta(acc, []); }
    else if (ev.tipo === 'erro') out.innerHTML = '<p class="erro">' + esc(ev.texto) + '</p>';
    else if (ev.tipo === 'fim') st.textContent = 'Pronto.';
  }).catch(function (e) { out.innerHTML += '<p class="erro">Falhou: ' + esc(e.message) + '</p>'; })
    .then(function () { out.classList.remove('cursor'); var b = $('#b-trad'); if (b) b.disabled = false; });
}

telas.estante = function () {
  conteudo.innerHTML = '<h1>Estante</h1><p class="status-linha">Carregando…</p>';
  api('/api/estante').then(function (r) {
    var h = '<h1>Estante</h1><p class="meta">Tudo o que está no SSD, por tema. ' + linkAjuda('estante') + ' · ' + linkAjuda('enciclopedias', 'O que é cada enciclopédia') + '</p>';
    h += '<h2 style="margin-top:1rem">Manuais e livros <small class="meta">(pasta 5_Acervo)</small></h2>';
    h += r.temas.length ? r.temas.map(function (t) {
      return '<details class="tema" data-tema="' + esc(t.tema) + '"><summary><span>' + esc(t.tema) + '</span><span class="meta">' +
        t.n.toLocaleString('pt-BR') + '</span></summary><div class="docs-tema"><p class="status-linha">Carregando…</p></div></details>';
    }).join('') : '<p class="vazio">Nenhum documento indexado ainda.</p>';
    h += '<h2 style="margin-top:2rem">Enciclopédias <small class="meta">(pasta 1_Enciclopedia · ' + r.livros.length + ' livros)</small></h2>';
    var grupos = [['pt', 'Em português'], ['en', 'Em inglês'], ['outro', 'Outros idiomas']];
    h += r.livros.length ? grupos.map(function (g) {
      var ls = r.livros.filter(function (b) { return g[0] === 'outro' ? (b.idioma !== 'pt' && b.idioma !== 'en') : b.idioma === g[0]; })
        .sort(function (a, b) { return a.titulo.localeCompare(b.titulo, 'pt'); });
      if (!ls.length) return '';
      return '<h3 class="rotulo" style="margin:1.2rem 0 .5rem">' + g[1] + ' (' + ls.length + ')</h3><div class="grade-livros">' + ls.map(function (b) {
        return '<a class="cartao livro" href="' + esc(b.url) + '" target="_blank"><b>' + esc(b.titulo) + '</b>' +
          '<span class="meta">' + b.artigos.toLocaleString('pt-BR') + ' artigos</span></a>';
      }).join('') + '</div>';
    }).join('') : '<p class="vazio">' + esc(r.kiwix.erro || 'As enciclopédias ainda estão abrindo…') + '</p>';
    conteudo.innerHTML = h;
    conteudo.querySelectorAll('details.tema').forEach(function (det) {
      det.addEventListener('toggle', function () {
        if (!det.open || det.dataset.carregado) return;
        det.dataset.carregado = '1';
        api('/api/estante/tema?t=' + encodeURIComponent(det.dataset.tema)).then(function (x) {
          det.querySelector('.docs-tema').innerHTML = x.pastas.map(function (p) {
            return (x.pastas.length > 1 ? '<div class="rotulo" style="margin-top:.6rem">' + esc(p.pasta || '(raiz do tema)') + ' · ' + p.docs.length + '</div>' : '') +
              '<ul class="lista-docs">' + p.docs.map(function (d) {
                return '<li><a href="' + esc(d.url) + '" target="_blank">' + esc(d.titulo) + '</a> ' +
                  '<span class="meta">' + esc((d.tipo || '').toUpperCase()) + (d.situacao !== 'ok' ? ' · sem texto pesquisável' : '') + '</span>' +
                  (d.situacao === 'sem_texto' && d.tipo === 'pdf' ? ' <a class="meta" href="/ocr/' + d.id + '" target="_blank">ler com OCR</a>' : '') + '</li>';
              }).join('') + '</ul>';
          }).join('');
        }).catch(function (e) { det.querySelector('.docs-tema').innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
      });
    });
  }).catch(function (e) { conteudo.innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
};

// ---------------------------------------------------------------- ajuda

var GUIA = null;
function carregarGuia() {
  if (GUIA) return Promise.resolve(GUIA);
  return api('/static/ajuda/guia.json').then(function (g) { GUIA = g; return g; });
}
function linkAjuda(id, texto) {
  return '<a class="link-ajuda" href="#/ajuda?s=' + id + '" title="Como usar esta tela">? ' + esc(texto || 'Como usar esta tela') + '</a>';
}
var controleAjuda = null;

telas.ajuda = function (p) {
  try { localStorage.setItem('viu_ajuda', '1'); } catch (e) {}
  conteudo.innerHTML = '<section class="painel ajuda"><h1>Ajuda</h1>' +
    '<p class="meta">Guia de uso da biblioteca. Escreva sua dúvida (ex.: <i>como uso no celular?</i>, <i>o que é o Kiwix?</i>, ' +
    '<i>como ler um PDF escaneado?</i>) ou abra um dos assuntos abaixo.</p>' +
    '<form class="busca" id="form-ajuda"><input type="search" id="ajuda-q" placeholder="Pergunte como usar o app…" autocomplete="off" aria-label="Dúvida sobre o app">' +
    '<button class="botao" type="submit">Perguntar</button></form>' +
    '<div id="ajuda-res"></div><div class="cartao ajuda-numeros" id="ajuda-numeros">Carregando o que tem no SSD…</div>' +
    '<h2 style="margin-top:1.4rem">Assuntos</h2><div class="ajuda-indice" id="ajuda-indice"></div>' +
    '<div id="ajuda-secoes"><p class="status-linha">Carregando…</p></div></section>';
  carregarGuia().then(function (g) {
    $('#ajuda-indice').innerHTML = g.map(function (x) {
      return '<a class="cartao" href="#/ajuda?s=' + x.id + '"><b>' + esc(x.titulo) + '</b><span class="meta">' + esc(x.resumo) + '</span></a>';
    }).join('');
    $('#ajuda-secoes').innerHTML = g.map(function (x) {
      return '<section class="cartao ajuda-secao" id="aj-' + x.id + '"><h2>' + esc(x.titulo) + '</h2>' + x.html +
        '<p class="meta"><a href="#/ajuda">↑ voltar aos assuntos</a></p></section>';
    }).join('');
    var alvo = p.get('s') && document.getElementById('aj-' + p.get('s'));
    if (alvo) { alvo.classList.add('destaque'); alvo.scrollIntoView({ block: 'start' }); }
  }).catch(function (e) { $('#ajuda-secoes').innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
  api('/api/situacao').then(function (s) {
    var el = $('#ajuda-numeros');
    if (!el) return;
    el.innerHTML = '<b>Hoje o SSD tem:</b> ' + s.indice.documentos.toLocaleString('pt-BR') + ' manuais e livros (' +
      s.indice.paginas.toLocaleString('pt-BR') + ' páginas pesquisáveis) · ' + (s.livros.length || s.zims.length) + ' enciclopédias' +
      (s.mapa_pc && s.mapa_pc.ok ? ' · mapa do Brasil' : '') + (s.ia.disponivel ? ' · IA local' : '') +
      (s.disco && s.disco.livre ? ' · ' + tamanho(s.disco.livre) + ' livres no SSD' : '');
  }).catch(function () {});
  $('#form-ajuda').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var q = $('#ajuda-q').value.trim();
    if (!q) return;
    if (controleAjuda) controleAjuda.abort();
    controleAjuda = window.AbortController ? new AbortController() : null;
    var res = $('#ajuda-res'), texto = '';
    res.innerHTML = '<div class="cartao resposta"><div id="aj-partes"></div><p class="status-linha" id="aj-st">Procurando no guia…</p><div id="aj-txt"></div></div>';
    lerStream('/api/ajuda/perguntar', { pergunta: q }, function (ev) {
      if (ev.tipo === 'secoes') {
        $('#aj-partes').innerHTML = '<p class="meta">Partes do guia sobre isso:</p><ul>' + ev.secoes.map(function (x) {
          return '<li><a href="#/ajuda?s=' + x.id + '"><b>' + esc(x.titulo) + '</b></a> <span class="meta">— ' + esc(x.resumo) + '</span></li>';
        }).join('') + '</ul>';
      } else if (ev.tipo === 'status') { $('#aj-st').textContent = ev.texto; }
      else if (ev.tipo === 'texto') {
        texto += ev.texto;
        $('#aj-st').textContent = 'Assistente:';
        $('#aj-txt').innerHTML = formatarResposta(texto, []);
      } else if (ev.tipo === 'erro') { $('#aj-st').textContent = ev.texto; }
      else if (ev.tipo === 'fim') { if (!texto) $('#aj-st').textContent = ''; }
    }, controleAjuda && controleAjuda.signal).catch(function (e) {
      if (e.name !== 'AbortError') $('#aj-st').textContent = 'Erro: ' + e.message;
    });
  });
};

var timerSituacao = null;

telas.situacao = function () {
  conteudo.innerHTML = '<h1>Situação</h1><p class="status-linha">Carregando…</p>';
  Promise.all([api('/api/situacao'), api('/api/config')]).then(function (x) {
    var s = x[0], c = x[1];
    desenharSituacao(s, c);
  }).catch(function (e) { conteudo.innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; });
};

function linhaSit(ok, txt) { return '<tr><td class="' + (ok ? 'sit-ok' : 'sit-ruim') + '">' + (ok ? '✔' : '✖') + '</td><td>' + txt + '</td></tr>'; }

function desenharSituacao(s, c) {
  var cfg = c.config;
  var h = '<h1>Situação</h1><p class="meta">' + linkAjuda('situacao') + '</p>';
  if (s.faltas.length) h += '<div class="aviso"><b>O que falta:</b><ul>' + s.faltas.map(function (f) { return '<li>' + esc(f) + '</li>'; }).join('') + '</ul></div>';
  if (s.aviso_disco) h += '<div class="aviso">' + esc(s.aviso_disco) + '</div>';
  if (s.local) h += '<div class="cartao" id="cartao-atu-caixa" style="margin-bottom:1.2rem"><h2>Atualizar pela internet</h2><div id="cartao-atu"><p class="status-linha">Carregando…</p></div></div>';
  h += '<div class="grade-sit">';
  h += '<div class="cartao"><h2>Manuais</h2><table class="tabela">' +
    linhaSit(s.indice.documentos > 0, s.indice.documentos + ' documentos, ' + s.indice.paginas.toLocaleString('pt-BR') + ' páginas com texto') +
    (s.indice.sem_texto ? linhaSit(false, s.indice.sem_texto + ' sem texto pesquisável (escaneados)') : '') +
    (s.indice.protegidos ? linhaSit(false, s.indice.protegidos + ' protegidos por senha') : '') +
    (s.indice.erros ? linhaSit(false, s.indice.erros + ' com erro de leitura') : '') +
    '</table><div id="prog" style="margin:.8rem 0"></div>' +
    (s.local ? '<button class="botao secundario" id="b-atualizar">Procurar arquivos novos no SSD</button>' : '') +
    '<p class="meta">Só olha o próprio SSD (não usa internet): copie PDFs, EPUBs ou TXT para <b>5_Acervo\\&lt;Tema&gt;</b> e clique aqui. ' +
    'Arquivos novos também entram sozinhos ao abrir o app. Para baixar novidades, use <b>Atualizar pela internet</b> logo acima.</p></div>';
  h += '<div class="cartao"><h2>Enciclopédias</h2><table class="tabela">' +
    linhaSit(s.kiwix.rodando, s.kiwix.rodando ? 'Servidor Kiwix funcionando' : esc(s.kiwix.erro || (s.kiwix.iniciando ? 'abrindo…' : 'parado'))) +
    linhaSit(s.zims.length > 0, s.zims.length + ' arquivos .zim completos') +
    (s.zims_incompletos.length ? linhaSit(false, 'Incompletos/ignorados: ' + esc(s.zims_incompletos.join(', '))) : '') +
    '</table></div>';
  h += '<div class="cartao"><h2>IA local</h2><table class="tabela">' +
    linhaSit(s.ia.disponivel, s.ia.disponivel ? 'Disponível (' + s.ia.modelos.length + ' modelos)' : esc(s.ia.motivo)) +
    (s.ia.disponivel ? linhaSit(true, 'Agora: ' + esc({ desligada: 'desligada (liga ao perguntar)', carregando: 'carregando…', pronta: 'ligada', erro: 'erro' }[s.ia.situacao] || s.ia.situacao) +
      (s.ia.modelo ? ' — ' + esc(s.ia.modelo) : '')) : '') +
    (s.ia.erro ? linhaSit(false, esc(s.ia.erro)) : '') + '</table></div>';
  if (s.downloads && s.downloads.ativo) {
    h += '<div class="cartao"><h2>Downloads em andamento</h2>' +
      (s.downloads.falta_gb ? '<p>Faltam <b>' + s.downloads.falta_gb.toLocaleString('pt-BR') + ' GB</b> de enciclopédias (além de manuais em PDF).</p>' : '') +
      '<pre style="white-space:pre-wrap;font-size:.78rem;margin:0">' + esc(s.downloads.ultimas.join('\n')) + '</pre>' +
      '<p class="meta">Os baixadores rodam nas janelas da pasta _Baixador. O que chega entra na biblioteca ao clicar em "Atualizar biblioteca".</p></div>';
  }
  h += '<div class="cartao"><h2>Computador</h2><table class="tabela">' +
    '<tr><td>RAM</td><td>' + tamanho(s.ram.total) + ' (livre ' + tamanho(s.ram.livre) + ')</td></tr>' +
    '<tr><td>SSD (' + esc(s.raiz) + ')</td><td>' + tamanho(s.disco.livre) + ' livres de ' + tamanho(s.disco.total) + '</td></tr>' +
    (s.somente_leitura ? '<tr><td colspan="2" class="sit-ruim">SSD somente leitura</td></tr>' : '') + '</table></div>';
  h += '</div>';

  if (s.local) {
    h += '<h2 style="margin-top:1.6rem">Configurações</h2><div class="grade-sit"><div class="cartao">' +
      '<div class="campo"><label for="c-modelo">Modelo de IA</label><select id="c-modelo"><option value="auto">Automático: rápida e boa (recomendado)</option>' +
      c.modelos.map(function (m) { return '<option value="' + esc(m) + '"' + (cfg.modelo === m ? ' selected' : '') + '>' + esc(m) +
        (/8B/i.test(m) ? ' — caprichada, mas leva minutos por resposta' : '') + '</option>'; }).join('') + '</select>' +
      '<small>Automático usa o modelo 4B (responde em menos de 1 minuto). O 1.7B é o mais rápido, mas escreve pior; o 8B escreve melhor, mas sem placa de vídeo leva vários minutos.</small></div>' +
      '<div class="campo linha"><input type="checkbox" id="c-copia"' + (cfg.copia_pc ? ' checked' : '') + '><label for="c-copia">Guardar uma cópia da IA neste computador (' +
        (s.ia.copia_gb ? s.ia.copia_gb.toLocaleString('pt-BR') + ' GB guardados' : 'cerca de 2 GB') + '): ela carrega em segundos em vez de minutos</label></div>' +
      '<div class="campo linha"><input type="checkbox" id="c-abrir"' + (cfg.ia_ao_abrir ? ' checked' : '') + '><label for="c-abrir">Já ligar a IA ao abrir a biblioteca (a primeira pergunta não espera)</label></div>' +
      '<div class="campo"><label for="c-min">Desligar a IA após (minutos sem uso)</label><input type="number" id="c-min" min="1" max="240" value="' + cfg.minutos_ia + '"></div>' +
      '<div class="campo linha"><input type="checkbox" id="c-pensar"' + (cfg.pensar_mais ? ' checked' : '') + '><label for="c-pensar">“Pensar mais” (respostas mais cuidadosas, bem mais lentas)</label></div>' +
      '<button class="botao secundario" id="b-salvar">Salvar</button> <span class="status-linha" id="st-cfg"></span></div>' +
      '<div class="cartao"><h2>Modo rede da casa</h2>' +
      '<p class="meta">Deixa celulares conectados ao <b>Hotspot móvel do Windows</b> (ou ao mesmo Wi-Fi) usarem a biblioteca pelo navegador.</p>' +
      '<div class="campo linha"><input type="checkbox" id="c-rede"' + (s.rede.ligada ? ' checked' : '') + '><label for="c-rede">Ligar modo rede da casa</label></div>' +
      (s.rede.ligada ? '<p>No celular, abra o navegador e digite:</p>' + (s.rede.enderecos.length ? s.rede.enderecos.map(function (e) { return '<div class="endereco">' + esc(e) + '</div>'; }).join('') : '<p class="erro">Nenhuma rede encontrada. Ligue o Hotspot móvel do Windows.</p>') +
        '<p class="meta">Se o Windows perguntar sobre o Firewall, permita "Python" em redes privadas.</p>' : '') +
      '</div></div>';
  }
  if (s.ocr) {
    var of = s.ocr.fundo || {};
    h += '<h2 style="margin-top:1.6rem">PDFs escaneados (leitura de imagem)</h2><div class="cartao">' +
      '<p>Já lidos e pesquisáveis: <b>' + (s.ocr.lidos || 0).toLocaleString('pt-BR') + '</b> documentos' +
      (of.restantes_docs != null ? ' · faltam <b>' + of.restantes_docs.toLocaleString('pt-BR') + '</b> (' + (of.restantes_paginas || 0).toLocaleString('pt-BR') + ' páginas)' : '') + '.</p>' +
      (of.erro ? '<p class="erro">' + esc(of.erro) + '</p>' : '') +
      (of.rodando ? '<p class="status-linha">' + (of.pausa ? 'Pausado: ' + esc(of.pausa) : of.doc ? 'Lendo agora: ' + esc(of.doc) + (of.paginas ? ' (página ' + of.pagina + ' de ' + of.paginas + ')' : '') : 'Começando (espera o app terminar de abrir)…') +
        ' · nesta sessão: ' + of.paginas_feitas + ' páginas</p>' : '') +
      (s.local ? '<div class="campo linha"><input type="checkbox" id="c-ocr"' + (of.ligado ? ' checked' : '') + '><label for="c-ocr">Ler os escaneados aos poucos enquanto a biblioteca estiver aberta ' +
        '(começa por Sobrevivência e Saúde; para sozinho quando a IA está respondendo)</label></div>' : '') +
      '<p class="meta">Usa o leitor de texto que já vem no Windows (sem internet), cerca de meio segundo por página. ' +
      'Para ler uma página na hora, procure o documento e clique em <b>ler com OCR</b>; para fotos, use <b>Traduzir → Ler texto de uma foto</b>.</p></div>';
  }
  h += '<h2 style="margin-top:1.6rem">Outros recursos</h2><div class="grade-sit">' +
    '<div class="cartao"><h2>Mapas</h2>' +
    (s.mapa_pc && s.mapa_pc.ok ? '<p><a class="botao" href="/mapa">Abrir o mapa do Brasil</a></p><p class="meta">Dentro do app, sem instalar nada (' +
      s.mapa_pc.gb.toLocaleString('pt-BR') + ' GB: ruas até o nível de prédio e busca de cidades e lugares). Funciona também no celular pelo modo rede da casa.</p>' :
      '<p class="vazio">' + esc((s.mapa_pc && s.mapa_pc.motivo) || 'Mapa do app ainda não baixado.') + '</p>') +
    (s.mapas.length ? '<p class="meta">Também há <b>2_Mapas\\' + esc(s.mapas.join(', ')) + '</b> para o GPXSee (opcional).</p>' : '') +
    '<p class="meta">GPXSee no PC é opcional: instale e abra o arquivo .map' +
    (s.instaladores.some(function (i) { return /gpxsee/i.test(i.nome); }) ? ' (instalador em 6_Instaladores).' :
      ' — <b>o instalador do GPXSee não está em 6_Instaladores</b>; rode o _Baixador com internet.') +
    ' No celular: <b>Organic Maps</b>' + (s.instaladores.some(function (i) { return /organic/i.test(i.nome); }) ? ' (APK em 6_Instaladores)' : '') +
    (s.mapas_celular && s.mapas_celular.arquivos ?
      ': <b>os mapas offline do Brasil inteiro já estão no SSD</b> (' + s.mapas_celular.arquivos + ' arquivos, ' +
      s.mapas_celular.gb.toLocaleString('pt-BR') + ' GB, pasta ' + esc(s.mapas_celular.pasta) + '). ' +
      (s.mapas_celular.instrucoes ? '<a href="' + esc(s.mapas_celular.instrucoes) + '" target="_blank">Como passar para o celular</a>.' : '') :
      ' — baixe os mapas do Brasil nele enquanto houver internet.') + '</p></div>' +
    '<div class="cartao"><h2>Instaladores</h2>' + (s.instaladores.length ? '<table class="tabela">' + s.instaladores.map(function (i) {
      return '<tr><td><a href="' + esc(i.url) + '">' + esc(i.nome) + '</a></td><td class="meta">' + tamanho(i.tamanho) + '</td></tr>';
    }).join('') + '</table><p class="meta">Pasta 6_Instaladores. No celular (modo rede), toque num .apk para baixar e instalar.</p>' : '<p class="vazio">Nada em 6_Instaladores.</p>') + '</div></div>';
  conteudo.innerHTML = h;
  desenharProgresso(s.progresso);

  var ba = $('#b-atualizar');
  if (ba) ba.addEventListener('click', function () {
    ba.disabled = true;
    postar('/api/atualizar').then(function (r) { $('#prog').innerHTML = '<p class="status-linha">' + esc(r.mensagem) + '</p>'; acompanharProgresso(); })
      .catch(function (e) { $('#prog').innerHTML = '<p class="erro">' + esc(e.message) + '</p>'; ba.disabled = false; });
  });
  var bs = $('#b-salvar');
  if (bs) bs.addEventListener('click', function () {
    postar('/api/config', { modelo: $('#c-modelo').value, minutos_ia: $('#c-min').value, pensar_mais: $('#c-pensar').checked,
      copia_pc: $('#c-copia').checked, ia_ao_abrir: $('#c-abrir').checked })
      .then(function (r) { $('#st-cfg').textContent = r.salvo ? 'Salvo.' : 'Aplicado (não deu para salvar no SSD).'; })
      .catch(function (e) { $('#st-cfg').textContent = e.message; });
  });
  var co = $('#c-ocr');
  if (co) co.addEventListener('change', function () {
    postar('/api/ocr/fundo', { ligado: co.checked }).then(function () { setTimeout(telas.situacao, 800); });
  });
  var cr = $('#c-rede');
  if (cr) cr.addEventListener('change', function () {
    postar('/api/config', { rede_casa: cr.checked }).then(function () { telas.situacao(); });
  });
  if (s.progresso.rodando) acompanharProgresso();
  desenharAtualizacao();
}

// ---------------------------------------------------------------- atualizar pela internet

var timerAtu = null;
function nomeZim(arq) {
  return String(arq || '').replace(/_(maxi|nopic|mini)?_?\d{4}-\d{2}[a-z]?\.zim$/, '').replace(/_/g, ' ');
}
function resumoAtualizacao(e) {
  var r = e.resultado || {}, h = '';
  if (e.modo === 'verificar') {
    var z = r.zims || [];
    h += '<p><b>Verificado em ' + esc(e.fim || '') + '.</b> Espaço livre: ' + (r.livre_gb || 0).toLocaleString('pt-BR') + ' GB.</p>';
    if (!z.length) h += '<p>Enciclopédias: <b>todas em dia</b>.</p>';
    else h += '<p>Enciclopédias com versão nova: <b>' + z.length + '</b></p><table class="tabela">' + z.map(function (x) {
      return '<tr><td>' + esc(nomeZim(x.arquivo)) + '<br><small class="meta">' + esc(x.data_atual) + ' → ' + esc(x.data_nova) + '</small></td>' +
        '<td class="meta">' + x.gb.toLocaleString('pt-BR') + ' GB</td><td>' + (x.cabe ? 'cabe' : '<span class="erro">falta espaço</span>') + '</td></tr>';
    }).join('') + '</table>';
    h += '<p class="meta">Livros e manuais novos (Embrapa, Exército, Ministério da Saúde, Hesperian) são procurados na hora de atualizar.</p>';
  } else {
    var zz = r.zims, ac = r.acervo;
    h += '<p><b>Atualizado em ' + esc(e.fim || '') + '.</b></p><ul>';
    if (zz) {
      h += '<li>Enciclopédias atualizadas: <b>' + zz.atualizadas.length + '</b>' + (zz.atualizadas.length ? ' (' + esc(zz.atualizadas.map(nomeZim).join(', ')) + ')' : '') + '</li>';
      if (zz.sem_espaco.length) h += '<li class="erro">Ficaram para depois por falta de espaço: ' + esc(zz.sem_espaco.map(nomeZim).join(', ')) + '</li>';
      if (zz.falhas.length) h += '<li class="erro">Não baixaram (tente de novo): ' + esc(zz.falhas.map(nomeZim).join(', ')) + '</li>';
    }
    if (ac) {
      h += '<li>Livros e manuais novos: <b>' + ac.novos_arquivos + '</b></li>';
      if (ac.erros.length) h += '<li class="erro">Com problema: ' + esc(ac.erros.join(', ')) + ' (tente de novo depois)</li>';
    }
    h += '<li>Espaço livre agora: ' + (r.livre_gb || 0).toLocaleString('pt-BR') + ' GB</li></ul>';
  }
  return h;
}
function desenharAtualizacao() {
  clearTimeout(timerAtu);
  if (!$('#cartao-atu')) return;
  api('/api/atualizacao').then(function (e) {
    var el = $('#cartao-atu');
    if (!el) return;
    if (!e.disponivel) { el.innerHTML = '<p class="vazio">Falta o arquivo _Baixador\\atualizar.py.</p>'; return; }
    var h = '<p class="meta">Quando houver internet: procura versões novas das enciclopédias e livros e manuais novos ' +
      '(Embrapa, Exército, Ministério da Saúde, SciELO, Hesperian), baixa o que couber no SSD e já coloca tudo na busca. ' +
      'Se for interrompido, da próxima vez continua de onde parou.</p>';
    if (e.ultima_atualizacao) h += '<p>Última atualização completa: <b>' + esc(e.ultima_atualizacao) + '</b></p>';
    var encaixando = e.depois && /…$/.test(e.depois);
    if (e.rodando) {
      h += '<p class="status-linha"><b>' + (e.modo === 'verificar' ? 'Verificando' : 'Atualizando') + ':</b> ' + esc(e.etapa || '') + '</p>';
      if (e.progresso != null) h += '<div class="barra"><i style="width:' + Math.round(e.progresso * 100) + '%"></i></div>';
      h += '<p class="meta">' + esc(e.mensagem || '') + '</p>' +
        '<p><button class="botao secundario" id="b-atu-cancelar" type="button">Cancelar</button></p>' +
        '<p class="meta">Pode continuar usando a biblioteca enquanto isso. Não feche o app até terminar (se fechar, continua depois).</p>';
    } else {
      if (e.erro) h += '<p class="erro">' + esc(e.erro) + '</p>';
      else if (e.resultado) h += resumoAtualizacao(e);
      if (e.depois) h += '<p class="status-linha">' + esc(e.depois) + '</p>';
      if (!encaixando) h +=
        '<div class="campo linha"><input type="checkbox" id="atu-zims" checked><label for="atu-zims">Enciclopédias (versões novas)</label></div>' +
        '<div class="campo linha"><input type="checkbox" id="atu-acervo" checked><label for="atu-acervo">Livros e manuais novos</label></div>' +
        '<p><button class="botao secundario" id="b-atu-verificar" type="button">Verificar o que há de novo</button> ' +
        '<button class="botao" id="b-atu-iniciar" type="button">Atualizar agora</button></p><p class="meta" id="atu-msg"></p>';
    }
    el.innerHTML = h;
    function partes() {
      var p = [];
      if ($('#atu-zims').checked) p.push('zims');
      if ($('#atu-acervo').checked) p.push('acervo');
      return p;
    }
    function iniciar(url) {
      postar(url, { partes: partes() }).then(function (r) {
        if (!r.ok) $('#atu-msg').textContent = r.mensagem;
        desenharAtualizacao();
      }).catch(function (x) { $('#atu-msg').textContent = x.message; });
    }
    var bv = $('#b-atu-verificar'), bi = $('#b-atu-iniciar'), bc = $('#b-atu-cancelar');
    if (bv) bv.onclick = function () { bv.disabled = true; iniciar('/api/atualizacao/verificar'); };
    if (bi) bi.onclick = function () {
      if (!confirm('Atualizar pela internet agora?\n\nPode levar de minutos a algumas horas, conforme o que houver de novo e a velocidade da internet. ' +
        'Dá para usar a biblioteca enquanto isso.')) return;
      bi.disabled = true; iniciar('/api/atualizacao/iniciar');
    };
    if (bc) bc.onclick = function () {
      if (!confirm('Cancelar a atualização? O que já foi baixado fica guardado.')) return;
      postar('/api/atualizacao/cancelar').then(desenharAtualizacao);
    };
    if (e.rodando || encaixando) timerAtu = setTimeout(function () {
      if (parametros().rota === 'situacao') desenharAtualizacao();
    }, 2000);
  }).catch(function (x) {
    var el = $('#cartao-atu');
    if (el) el.innerHTML = '<p class="erro">Não consegui ler o estado da atualização (' + esc(x.message) + '). Feche e abra a biblioteca de novo.</p>';
  });
}

function desenharProgresso(p) {
  var el = $('#prog');
  if (!el) return;
  if (p.rodando) {
    var pct = p.total ? Math.round(100 * p.feitos / p.total) : 0;
    el.innerHTML = '<div class="barra"><i style="width:' + pct + '%"></i></div><p class="status-linha">Indexando ' + p.feitos + ' de ' + p.total + ' — ' + esc(p.atual) + '</p>';
  } else if (p.fim) {
    el.innerHTML = '<p class="status-linha">Última atualização: ' + p.novos + ' novos/alterados, ' + p.removidos + ' removidos' + (p.erros ? ', ' + p.erros + ' com erro' : '') + '.' + (p.mensagem ? ' ' + esc(p.mensagem) : '') + '</p>';
  }
}
function acompanharProgresso() {
  clearTimeout(timerSituacao);
  timerSituacao = setTimeout(function () {
    if (parametros().rota !== 'situacao') return;
    api('/api/progresso').then(function (p) {
      desenharProgresso(p);
      if (p.rodando) acompanharProgresso(); else telas.situacao();
    });
  }, 1000);
}

// ---------------------------------------------------------------- roteador

function rotear() {
  var r = parametros();
  var tela = telas[r.rota] || telas.inicio;
  document.querySelectorAll('.menu a').forEach(function (a) {
    var ativo = a.getAttribute('data-rota') === (r.rota === 'busca' ? 'inicio' : r.rota);
    if (ativo) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  window.scrollTo(0, 0);
  tela(r.p);
}

$('#botao-tema').addEventListener('click', function () {
  var atual = document.documentElement.getAttribute('data-tema') === 'escuro' ? 'claro' : 'escuro';
  document.documentElement.setAttribute('data-tema', atual);
  try { localStorage.setItem('tema', atual); } catch (e) {}
});

window.addEventListener('hashchange', rotear);
Promise.all([api('/api/atalhos'), api('/api/temas')]).then(function (x) {
  ATALHOS = x[0].atalhos; TEMAS = x[1].temas;
}).catch(function () {}).then(rotear);
