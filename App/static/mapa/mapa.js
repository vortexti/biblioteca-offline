// Mapa do Brasil offline: MapLibre + estilo Protomaps, tiles servidos pelo próprio app (2_Mapas\brasil.pmtiles).
(function () {
  'use strict';
  var $ = function (s) { return document.querySelector(s); };
  var ORIGEM = location.origin;
  var mapa = null, info = null;
  var marcaBusca = null, marcaClique = null;
  var medindo = false, pontosMedida = [];
  var marcasSalvas = [];

  function esc(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function temaEscuro() { return document.documentElement.getAttribute('data-tema') === 'escuro'; }
  function lerMarcas() {
    try { marcasSalvas = JSON.parse(localStorage.getItem('mapa_marcas') || '[]') || []; } catch (e) { marcasSalvas = []; }
  }
  function gravarMarcas() {
    try { localStorage.setItem('mapa_marcas', JSON.stringify(marcasSalvas)); } catch (e) {}
  }

  // ---------------------------------------------------------------- coordenadas e distâncias
  function gms(v, pos, neg) {
    var s = v < 0 ? neg : pos; v = Math.abs(v);
    var g = Math.floor(v), mf = (v - g) * 60, m = Math.floor(mf), seg = (mf - m) * 60;
    return g + '°' + String(m).padStart(2, '0') + "'" + seg.toFixed(1).padStart(4, '0') + '"' + s;
  }
  function textoCoord(ll) {
    return ll.lat.toFixed(5) + ', ' + ll.lng.toFixed(5);
  }
  function distancia(a, b) {   // metros (haversine)
    var R = 6371008.8, r = Math.PI / 180;
    var dLat = (b.lat - a.lat) * r, dLon = (b.lng - a.lng) * r;
    var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(h));
  }
  function rumo(a, b) {         // graus a partir do norte
    var r = Math.PI / 180;
    var y = Math.sin((b.lng - a.lng) * r) * Math.cos(b.lat * r);
    var x = Math.cos(a.lat * r) * Math.sin(b.lat * r) - Math.sin(a.lat * r) * Math.cos(b.lat * r) * Math.cos((b.lng - a.lng) * r);
    return (Math.atan2(y, x) / r + 360) % 360;
  }
  function textoDist(m) {
    return m < 1000 ? Math.round(m) + ' m' : (m / 1000).toLocaleString('pt-BR', { maximumFractionDigits: m < 10000 ? 2 : 1 }) + ' km';
  }
  function direcao(g) {
    return ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'][Math.round(g / 45) % 8];
  }

  // ---------------------------------------------------------------- painel
  function painel(html) {
    var p = $('#painel');
    if (!html) { p.hidden = true; p.innerHTML = ''; return; }
    p.innerHTML = '<button class="fechar" type="button" aria-label="Fechar">×</button>' + html;
    p.hidden = false;
    p.querySelector('.fechar').onclick = function () {
      painel('');
      if (marcaClique) { marcaClique.remove(); marcaClique = null; }
      if (medindo) alternarMedida();
    };
  }

  function mostrarPonto(ll, titulo) {
    if (marcaClique) marcaClique.remove();
    var el = document.createElement('div'); el.className = 'marca-ponto';
    marcaClique = new maplibregl.Marker({ element: el }).setLngLat(ll).addTo(mapa);
    painel('<h3>' + esc(titulo || 'Ponto no mapa') + '</h3>' +
      '<div class="coord">' + textoCoord(ll) + '</div>' +
      '<div class="coord">' + gms(ll.lat, 'N', 'S') + ' ' + gms(ll.lng, 'L', 'O') + '</div>' +
      '<p class="meta">Latitude, longitude (graus decimais e graus/minutos/segundos). Serve para passar a posição por rádio ou anotar no papel.</p>' +
      '<div class="linha-botoes"><button class="botao pequeno" id="b-salvar" type="button">📍 Marcar este ponto</button>' +
      '<button class="botao secundario pequeno" id="b-copiar" type="button">Copiar coordenadas</button></div>');
    $('#b-salvar').onclick = function () {
      var nome = prompt('Nome do ponto (ex.: nascente, abrigo, posto de saúde):', titulo || '');
      if (nome === null) return;
      marcasSalvas.push({ nome: nome.trim() || 'Ponto', lat: +ll.lat.toFixed(6), lon: +ll.lng.toFixed(6), quando: new Date().toISOString().slice(0, 10) });
      gravarMarcas(); desenharMarcas(); listarMarcas();
    };
    $('#b-copiar').onclick = function () {
      var t = textoCoord(ll);
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t);
      else { var i = document.createElement('textarea'); i.value = t; document.body.appendChild(i); i.select(); try { document.execCommand('copy'); } catch (e) {} i.remove(); }
      this.textContent = 'Copiado!';
    };
  }

  // ---------------------------------------------------------------- marcas salvas
  var objetosMarcas = [];
  function desenharMarcas() {
    objetosMarcas.forEach(function (m) { m.remove(); });
    objetosMarcas = marcasSalvas.map(function (m) {
      var el = document.createElement('div'); el.className = 'marca-ponto salva'; el.title = m.nome;
      return new maplibregl.Marker({ element: el }).setLngLat([m.lon, m.lat])
        .setPopup(new maplibregl.Popup({ offset: 12 }).setText(m.nome)).addTo(mapa);
    });
  }
  function listarMarcas() {
    if (!marcasSalvas.length) {
      painel('<h3>Suas marcas</h3><p class="meta">Nenhum ponto marcado ainda. Clique no mapa e use "Marcar este ponto". ' +
        'As marcas ficam guardadas neste navegador.</p>');
      return;
    }
    painel('<h3>Suas marcas (' + marcasSalvas.length + ')</h3><ul>' + marcasSalvas.map(function (m, i) {
      return '<li><button class="ir" data-i="' + i + '" type="button"><b>' + esc(m.nome) + '</b><br><small class="coord">' +
        m.lat.toFixed(5) + ', ' + m.lon.toFixed(5) + '</small></button>' +
        '<button class="botao secundario pequeno" data-apagar="' + i + '" type="button" title="Apagar">✕</button></li>';
    }).join('') + '</ul><p class="meta">Guardadas neste navegador (no PC e no celular são listas separadas).</p>');
    $('#painel').querySelectorAll('button.ir').forEach(function (b) {
      b.onclick = function () { var m = marcasSalvas[+b.dataset.i]; mapa.flyTo({ center: [m.lon, m.lat], zoom: Math.max(mapa.getZoom(), 14) }); };
    });
    $('#painel').querySelectorAll('[data-apagar]').forEach(function (b) {
      b.onclick = function () {
        var m = marcasSalvas[+b.dataset.apagar];
        if (!confirm('Apagar a marca "' + m.nome + '"?')) return;
        marcasSalvas.splice(+b.dataset.apagar, 1); gravarMarcas(); desenharMarcas(); listarMarcas();
      };
    });
  }

  // ---------------------------------------------------------------- medir
  function dadosMedida() {
    var dados = { type: 'FeatureCollection', features: [] };
    if (pontosMedida.length) {
      dados.features.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: pontosMedida.map(function (p) { return [p.lng, p.lat]; }) } });
      pontosMedida.forEach(function (p) { dados.features.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.lng, p.lat] } }); });
    }
    return dados;
  }
  function atualizarMedida() {
    mapa.getSource('medida').setData(dadosMedida());
    var total = 0, trechos = [];
    for (var i = 1; i < pontosMedida.length; i++) {
      var d = distancia(pontosMedida[i - 1], pontosMedida[i]); total += d;
      trechos.push(textoDist(d) + ' rumo ' + Math.round(rumo(pontosMedida[i - 1], pontosMedida[i])) + '° (' + direcao(rumo(pontosMedida[i - 1], pontosMedida[i])) + ')');
    }
    painel('<h3>Medir distância</h3>' +
      (pontosMedida.length < 2 ? '<p>Clique no mapa os pontos do caminho.</p>' :
        '<p style="font-size:1.3rem;margin:.2rem 0"><b>' + textoDist(total) + '</b></p>' +
        '<p class="meta">A pé, em terreno plano, ~4 km/h: <b>' + tempoAPe(total) + '</b>. Distância em linha reta entre os pontos; rumo pela bússola (norte verdadeiro).</p>' +
        '<ol class="meta">' + trechos.map(function (t) { return '<li>' + t + '</li>'; }).join('') + '</ol>') +
      '<div class="linha-botoes"><button class="botao secundario pequeno" id="b-desfazer" type="button">Desfazer ponto</button>' +
      '<button class="botao secundario pequeno" id="b-limpar" type="button">Limpar</button></div>');
    $('#b-desfazer').onclick = function () { pontosMedida.pop(); atualizarMedida(); };
    $('#b-limpar').onclick = function () { pontosMedida = []; atualizarMedida(); };
  }
  function tempoAPe(m) {
    var h = m / 4000, hh = Math.floor(h), mm = Math.round((h - hh) * 60);
    return hh ? hh + ' h ' + mm + ' min' : mm + ' min';
  }
  function alternarMedida() {
    medindo = !medindo;
    $('#b-medir').classList.toggle('ativo', medindo);
    mapa.getCanvas().style.cursor = medindo ? 'crosshair' : '';
    if (medindo) { if (marcaClique) { marcaClique.remove(); marcaClique = null; } pontosMedida = []; atualizarMedida(); }
    else { pontosMedida = []; mapa.getSource('medida').setData({ type: 'FeatureCollection', features: [] }); painel(''); }
  }

  // ---------------------------------------------------------------- busca de lugares
  function buscar(q) {
    var caixa = $('#resultados');
    q = q.trim();
    var m = q.match(/^\s*(-?\d{1,2}(?:[.,]\d+)?)\s*[,; ]\s*(-?\d{1,3}(?:[.,]\d+)?)\s*$/);
    if (m) {   // digitou coordenadas: "-23.55, -46.63"
      var ll = { lat: parseFloat(m[1].replace(',', '.')), lng: parseFloat(m[2].replace(',', '.')) };
      caixa.hidden = true;
      mapa.flyTo({ center: [ll.lng, ll.lat], zoom: 14 }); mostrarPonto(ll, 'Coordenada digitada');
      return;
    }
    if (q.length < 2) { caixa.hidden = true; return; }
    caixa.hidden = false; caixa.innerHTML = '<p>Procurando…</p>';
    fetch('/api/lugares?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (d) {
      var l = d.lugares || [];
      if (!l.length) { caixa.innerHTML = '<p>Nada encontrado. Tente sem acento, só o começo do nome, ou "Nome, Estado".</p>'; return; }
      caixa.innerHTML = l.map(function (x, i) {
        return '<button type="button" data-i="' + i + '"><b>' + esc(x.nome) + '</b> <small>— ' + esc(x.tipo) +
          (x.estado ? ', ' + esc(x.estado) : '') + (x.populacao ? ' · ' + x.populacao.toLocaleString('pt-BR') + ' hab.' : '') + '</small></button>';
      }).join('');
      caixa.querySelectorAll('button').forEach(function (b) {
        b.onclick = function () {
          var x = l[+b.dataset.i]; caixa.hidden = true;
          var z = /capital|cidade/.test(x.tipo) ? 12 : 14;
          mapa.flyTo({ center: [x.lon, x.lat], zoom: z });
          if (marcaBusca) marcaBusca.remove();
          var el = document.createElement('div'); el.className = 'marca-ponto';
          marcaBusca = new maplibregl.Marker({ element: el }).setLngLat([x.lon, x.lat]).addTo(mapa);
          mostrarPonto({ lat: x.lat, lng: x.lon }, x.nome + (x.estado ? ' (' + x.estado + ')' : ''));
        };
      });
    }).catch(function () { caixa.innerHTML = '<p>Erro ao buscar. O app ainda está aberto?</p>'; });
  }

  // ---------------------------------------------------------------- estilo
  function estilo() {
    var sabor = temaEscuro() ? 'dark' : 'light';
    return {
      version: 8,
      glyphs: ORIGEM + '/mapa/fontes/{fontstack}/{range}.pbf',
      sprite: ORIGEM + '/static/mapa/sprites/' + sabor,
      sources: {
        protomaps: {
          type: 'vector', tiles: [ORIGEM + '/mapa/tiles/{z}/{x}/{y}.mvt'], minzoom: 0, maxzoom: info.maxzoom,
          bounds: info.limites,
          attribution: '© <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> · Protomaps'
        },
        medida: { type: 'geojson', data: { type: 'FeatureCollection', features: [] } }
      },
      layers: basemaps.layers('protomaps', basemaps.namedFlavor(sabor), { lang: 'pt' }).concat([
        { id: 'medida-linha', type: 'line', source: 'medida', filter: ['==', '$type', 'LineString'],
          paint: { 'line-color': '#e0531f', 'line-width': 3, 'line-dasharray': [2, 1] } },
        { id: 'medida-pontos', type: 'circle', source: 'medida', filter: ['==', '$type', 'Point'],
          paint: { 'circle-radius': 5, 'circle-color': '#e0531f', 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } }
      ])
    };
  }

  function iniciar() {
    lerMarcas();
    var guardado = null;
    try { guardado = JSON.parse(localStorage.getItem('mapa_vista') || 'null'); } catch (e) {}
    mapa = new maplibregl.Map({
      container: 'mapa', style: estilo(),
      center: guardado ? guardado.c : [-51.9, -14.2], zoom: guardado ? guardado.z : 3.6,
      maxZoom: 19, minZoom: 2, hash: false, attributionControl: { compact: true }
    });
    window.mapaBiblioteca = mapa;  // para inspeção pelo console
    mapa.on('error', function (e) { if (window.console) console.warn('mapa:', e && e.error && e.error.message); });
    mapa.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-right');
    mapa.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: 'metric' }), 'bottom-left');
    if (window.isSecureContext && navigator.geolocation) {
      mapa.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: true }), 'top-right');
    }
    mapa.on('load', desenharMarcas);
    mapa.on('moveend', function () {
      try { localStorage.setItem('mapa_vista', JSON.stringify({ c: mapa.getCenter().toArray(), z: mapa.getZoom() })); } catch (e) {}
    });
    mapa.on('click', function (e) {
      $('#resultados').hidden = true;
      if (medindo) { pontosMedida.push(e.lngLat); atualizarMedida(); return; }
      var nome = '';
      var f = mapa.queryRenderedFeatures(e.point).filter(function (x) { return x.properties && (x.properties['name:pt'] || x.properties.name); })[0];
      if (f) nome = f.properties['name:pt'] || f.properties.name;
      mostrarPonto(e.lngLat, nome);
    });
    $('#b-medir').onclick = alternarMedida;
    $('#b-marcas').onclick = function () { if (medindo) alternarMedida(); listarMarcas(); };
    $('#b-tema').onclick = function () {
      var atual = temaEscuro() ? 'claro' : 'escuro';
      document.documentElement.setAttribute('data-tema', atual);
      try { localStorage.setItem('tema', atual); } catch (e) {}
      mapa.setStyle(estilo());
      mapa.once('style.load', function () { mapa.getSource('medida').setData(dadosMedida()); });
    };
    $('#f-busca').onsubmit = function (e) { e.preventDefault(); buscar($('#q').value); };
    var espera = null;
    $('#q').addEventListener('input', function () {
      clearTimeout(espera);
      var v = this.value;
      espera = setTimeout(function () { if (v.trim().length >= 3) buscar(v); }, 350);
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') $('#resultados').hidden = true; });
  }

  fetch('/api/mapa').then(function (r) { return r.json(); }).then(function (d) {
    info = d;
    if (!d.ok) {
      var a = $('#aviso'); a.hidden = false;
      a.innerHTML = '<h2>Mapa ainda não disponível</h2><p>' + esc(d.motivo) + '</p><p><a href="/">Voltar para a biblioteca</a></p>';
      return;
    }
    var teste = document.createElement('canvas');
    if (!(teste.getContext('webgl2') || teste.getContext('webgl'))) {
      var a2 = $('#aviso'); a2.hidden = false;
      a2.innerHTML = '<h2>Este navegador não desenha o mapa</h2><p>O mapa precisa de WebGL. Use o Chrome, Edge ou Firefox atualizados ' +
        '(o instalador do Firefox está em 6_Instaladores).</p>';
      return;
    }
    iniciar();
  }).catch(function () {
    var a = $('#aviso'); a.hidden = false; a.textContent = 'Não consegui falar com o app. Ele está aberto?';
  });
})();
