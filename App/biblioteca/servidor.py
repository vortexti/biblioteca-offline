"""Servidor web do app (biblioteca padrão): páginas, API, arquivos e proxy do Kiwix."""
import http.client
import json
import mimetypes
import os
import re
import threading
import time
import traceback
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import ajuda, atualizacao, config, ia, indice, kiwix, mapa, ocr, ocr_lote, sistema, textos

log = config.log

# pastas que podem ser servidas como arquivo
PASTAS_LIBERADAS = ('5_Acervo/', '6_Instaladores/', '2_Mapas/')

ATALHOS = [
    {'id': 'agua', 'nome': 'Água', 'pt': 'tratamento purificação de água potável', 'en': 'water purification disinfection'},
    {'id': 'fogo', 'nome': 'Fogo', 'pt': 'acender fogo fogueira', 'en': 'fire starting campfire'},
    {'id': 'abrigo', 'nome': 'Abrigo', 'pt': 'abrigo improvisado construção', 'en': 'survival shelter construction'},
    {'id': 'socorros', 'nome': 'Primeiros socorros', 'pt': 'primeiros socorros ferimento sangramento', 'en': 'first aid bleeding wound'},
    {'id': 'plantio', 'nome': 'Plantio', 'pt': 'plantio cultivo horta semeadura', 'en': 'vegetable garden planting'},
    {'id': 'animais', 'nome': 'Criação de animais', 'pt': 'criação de galinhas animais', 'en': 'raising livestock chickens'},
    {'id': 'conserva', 'nome': 'Conservação de alimentos', 'pt': 'conservação de alimentos conservas secagem', 'en': 'food preservation canning drying'},
    {'id': 'eletrica', 'nome': 'Elétrica', 'pt': 'instalação elétrica fiação', 'en': 'electrical wiring solar battery'},
    {'id': 'solda', 'nome': 'Solda e serralheria', 'pt': 'solda eletrodo soldagem', 'en': 'welding electrode arc'},
    {'id': 'marcenaria', 'nome': 'Marcenaria', 'pt': 'marcenaria madeira carpintaria', 'en': 'woodworking carpentry joinery'},
    {'id': 'navegacao', 'nome': 'Navegação', 'pt': 'orientação bússola mapa navegação', 'en': 'land navigation compass map'},
    {'id': 'sinais', 'nome': 'Sinais e comunicação', 'pt': 'sinais de socorro comunicação rádio', 'en': 'signaling rescue radio communication'},
    # áreas do conhecimento
    {'id': 'fisica', 'grupo': 'area', 'nome': 'Física', 'pt': 'física mecânica energia', 'en': 'physics mechanics energy'},
    {'id': 'quimica', 'grupo': 'area', 'nome': 'Química', 'pt': 'química reações substâncias', 'en': 'chemistry reactions compounds'},
    {'id': 'biologia', 'grupo': 'area', 'nome': 'Biologia', 'pt': 'biologia célula organismos', 'en': 'biology cell organisms'},
    {'id': 'fisiologia', 'grupo': 'area', 'nome': 'Anatomia e fisiologia', 'pt': 'anatomia fisiologia corpo humano', 'en': 'human anatomy physiology'},
    {'id': 'medicina', 'grupo': 'area', 'nome': 'Medicina', 'pt': 'doenças diagnóstico tratamento', 'en': 'disease diagnosis treatment'},
    {'id': 'matematica', 'grupo': 'area', 'nome': 'Matemática', 'pt': 'matemática álgebra geometria', 'en': 'mathematics algebra geometry'},
    {'id': 'meteorologia', 'grupo': 'area', 'nome': 'Meteorologia e clima', 'pt': 'meteorologia previsão do tempo clima', 'en': 'meteorology weather forecasting'},
    {'id': 'geologia', 'grupo': 'area', 'nome': 'Geologia e solos', 'pt': 'geologia rochas solo', 'en': 'geology rocks soil'},
    {'id': 'astronomia', 'grupo': 'area', 'nome': 'Astronomia', 'pt': 'astronomia estrelas planetas', 'en': 'astronomy stars planets'},
    {'id': 'mecanica', 'grupo': 'area', 'nome': 'Mecânica e motores', 'pt': 'motor mecânica manutenção', 'en': 'engine mechanics repair'},
    {'id': 'eletronica', 'grupo': 'area', 'nome': 'Eletrônica', 'pt': 'eletrônica circuitos componentes', 'en': 'electronics circuits components'},
    {'id': 'historia', 'grupo': 'area', 'nome': 'História', 'pt': 'história civilizações', 'en': 'history civilizations'},
]


def _local(endereco):
    return endereco in ('127.0.0.1', '::1', 'localhost')


class Manipulador(BaseHTTPRequestHandler):
    server_version = 'BibliotecaOffline/1.0'
    protocol_version = 'HTTP/1.0'

    def log_message(self, fmt, *args):  # silencioso; erros vão para o log
        pass

    # -------------------------------------------------------------- utilidades

    def _enviar(self, status, corpo, tipo='application/json; charset=utf-8', extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo, ensure_ascii=False).encode('utf-8')
        elif isinstance(corpo, str):
            corpo = corpo.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', tipo)
        self.send_header('Content-Length', str(len(corpo)))
        if 'Cache-Control' not in (extra or {}):
            self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(corpo)

    def _erro(self, status, msg):
        self._enviar(status, {'erro': msg})

    def _corpo_json(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > 2_000_000:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode('utf-8'))
        except ValueError:
            return {}

    def _stream(self, gerador):
        """NDJSON: um evento por linha, enviado assim que sai."""
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no')
        self.end_headers()
        try:
            for ev in gerador:
                self.wfile.write(json.dumps(ev, ensure_ascii=False).encode('utf-8') + b'\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log.info('cliente fechou a conexão no meio da resposta')
            gerador.close()
        except Exception as e:
            log.exception('erro no stream')
            try:
                msg = {'tipo': 'erro', 'texto': 'Erro: %s' % e}
                self.wfile.write(json.dumps(msg, ensure_ascii=False).encode('utf-8') + b'\n')
            except OSError:
                pass

    # -------------------------------------------------------------- rotas

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        try:
            self._rota_get()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            log.error('erro em GET %s\n%s', self.path, traceback.format_exc())
            try:
                self._erro(500, 'Erro interno. Veja App\\dados\\log.txt.')
            except OSError:
                pass

    def do_POST(self):
        try:
            self._rota_post()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception:
            log.error('erro em POST %s\n%s', self.path, traceback.format_exc())
            try:
                self._erro(500, 'Erro interno. Veja App\\dados\\log.txt.')
            except OSError:
                pass

    def _rota_get(self):
        u = urlsplit(self.path)
        caminho = unquote(u.path)
        q = {k: v[-1] for k, v in parse_qs(u.query).items()}

        if u.path.startswith(kiwix.RAIZ_URL + '/'):
            if u.path.lower().endswith('.epub') and self.command == 'GET':
                if q.get('baixar'):
                    self.path = u.path
                else:
                    return self._ler_epub_kiwix(u.path, q)
            return self._proxy_kiwix()
        if caminho in ('/', '/index.html'):
            return self._estatico('index.html')
        if caminho.startswith('/static/'):
            return self._estatico(caminho[len('/static/'):])
        if caminho.startswith('/arquivo/'):
            return self._arquivo(caminho[len('/arquivo/'):])
        m = re.match(r'^/ler/(\d+)$', caminho)
        if m:
            return self._ler(int(m.group(1)), q)
        m = re.match(r'^/api/doc/(\d+)/sumario$', caminho)
        if m:
            return self._enviar(200, {'secoes': indice.sumario_documento(int(m.group(1)))})
        m = re.match(r'^/api/doc/(\d+)/busca$', caminho)
        if m:
            return self._enviar(200, indice.buscar_no_documento(int(m.group(1)), q.get('q', '').strip()[:200]))
        if caminho in ('/mapa', '/mapa/'):
            return self._estatico('mapa/mapa.html')
        m = re.match(r'^/mapa/tiles/(\d+)/(\d+)/(\d+)\.mvt$', caminho)
        if m:
            return self._tile_mapa(*map(int, m.groups()))
        m = re.match(r'^/mapa/fontes/([^/]+)/(\d+-\d+)\.pbf$', caminho)
        if m:
            return self._fonte_mapa(m.group(1), m.group(2))
        m = re.match(r'^/ocr/(\d+)$', caminho)
        if m:
            return self._pagina_ocr(int(m.group(1)), q)
        m = re.match(r'^/ocr/img/(\d+)_(\d+)\.png$', caminho)
        if m:
            return self._imagem_ocr(int(m.group(1)), int(m.group(2)))
        if caminho == '/api/ocr/pagina':
            return self._ocr_pagina(q)
        if caminho == '/api/ocr':
            ok, motivo = ocr.disponivel()
            return self._enviar(200, {'disponivel': ok, 'motivo': motivo, 'idiomas': ocr.idiomas() if ok else [],
                                      'fundo': ocr_lote.resumo()})
        if caminho == '/api/mapa':
            return self._enviar(200, mapa.info())
        if caminho == '/api/lugares':
            return self._enviar(200, {'lugares': mapa.buscar_lugares(q.get('q', ''))})
        if caminho == '/api/situacao':
            return self._enviar(200, situacao(self.client_address[0]))
        if caminho == '/api/progresso':
            return self._enviar(200, dict(indice.progresso, aviso_disco=config.estado['aviso_disco']))
        if caminho == '/api/busca/docs':
            consulta = q.get('q', '').strip()[:300]
            if not consulta:
                return self._enviar(200, {'resultados': []})
            t = time.time()
            r = indice.buscar(consulta, tema=q.get('tema') or None, idioma=q.get('idioma') or None,
                              consulta_en=q.get('en') or None)
            return self._enviar(200, {'resultados': r, 'ms': int((time.time() - t) * 1000),
                                      'indexando': indice.progresso['rodando']})
        if caminho == '/api/busca/zim':
            consulta = q.get('q', '').strip()[:300]
            if not consulta:
                return self._enviar(200, {'resultados': []})
            idioma = q.get('idioma') or ''
            idiomas = (idioma,) if idioma in ('pt', 'en') else ('pt', 'en', 'mul')
            t = time.time()
            r = kiwix.buscar(consulta, idiomas=idiomas) if consulta else {'resultados': [], 'erro': ''}
            if q.get('en') and idioma in ('', 'en'):
                r2 = kiwix.buscar(q['en'], idiomas=('en',))
                urls = {x['url'] for x in r['resultados']}
                r['resultados'] += [x for x in r2['resultados'] if x['url'] not in urls]
            r['ms'] = int((time.time() - t) * 1000)
            r['estado'] = kiwix.estado()
            return self._enviar(200, r)
        if caminho == '/api/termos_en':
            consulta = q.get('q', '').strip()[:300]
            ok, motivo = ia.disponivel()
            if not ok:
                return self._enviar(200, {'en': '', 'erro': motivo})
            try:
                return self._enviar(200, {'en': ia.termos_em_ingles(consulta)})
            except Exception as e:
                return self._enviar(200, {'en': '', 'erro': str(e)})
        if caminho == '/api/estante':
            return self._enviar(200, {'temas': indice.estante(), 'livros': kiwix.livros(),
                                      'kiwix': kiwix.estado()})
        if caminho == '/api/estante/tema':
            return self._enviar(200, {'pastas': indice.estante_tema(q.get('t', ''))})
        if caminho == '/api/temas':
            return self._enviar(200, {'temas': indice.temas()})
        if caminho == '/api/atalhos':
            return self._enviar(200, {'atalhos': ATALHOS})
        if caminho == '/api/config':
            return self._enviar(200, {'config': config.obter(), 'modelos': ia.estado()['modelos']})
        if caminho == '/api/atualizacao':
            return self._enviar(200, atualizacao.estado())
        return self._erro(404, 'Página não encontrada.')

    def _rota_post(self):
        caminho = urlsplit(self.path).path
        local = _local(self.client_address[0])
        if caminho == '/api/ocr/imagem':
            return self._ocr_imagem()
        if caminho == '/api/ajuda/perguntar':
            return self._stream(ajuda.perguntar(self._corpo_json().get('pergunta', '')))
        if caminho == '/api/ia/preparar':
            # a tela "Pergunte" chama isto ao abrir: a IA carrega enquanto a pessoa digita
            ok, motivo = ia.disponivel()
            if ok and ia.estado()['situacao'] in ('desligada', 'erro'):
                threading.Thread(target=ia.garantir_rodando, daemon=True, name='ia-preparar').start()
            return self._enviar(200, ia.estado())
        if caminho == '/api/perguntar':
            dados = self._corpo_json()
            return self._stream(ia.perguntar(dados.get('pergunta', '')))
        if caminho == '/api/traduzir':
            dados = self._corpo_json()
            return self._stream(ia.traduzir(dados.get('texto', '')))
        # o resto só vale do próprio computador (não de celulares na rede)
        if not local:
            return self._erro(403, 'Só pode ser alterado no computador onde o app está rodando.')
        if caminho == '/api/atualizar':
            mudou_zim = kiwix.reiniciar_se_mudou()
            iniciou = indice.atualizar()
            msg = 'Atualizando a biblioteca…' if iniciou else (indice.progresso.get('mensagem') or
                                                              'A atualização já está em andamento.')
            return self._enviar(200, {'ok': True, 'mensagem': msg, 'kiwix_reiniciado': mudou_zim})
        if caminho == '/api/config':
            dados = self._corpo_json()
            novos = {}
            if dados.get('modelo') in (['auto'] + ia.estado()['modelos']):
                novos['modelo'] = dados['modelo']
            if 'minutos_ia' in dados:
                try:
                    novos['minutos_ia'] = max(1, min(240, int(dados['minutos_ia'])))
                except (TypeError, ValueError):
                    pass
            if 'pensar_mais' in dados:
                novos['pensar_mais'] = bool(dados['pensar_mais'])
            for k in ('copia_pc', 'ia_ao_abrir'):
                if k in dados:
                    novos[k] = bool(dados[k])
            antigo_modelo = config.obter('modelo')
            salvo = config.salvar(novos)
            if 'modelo' in novos and novos['modelo'] != antigo_modelo:
                ia.desligar('(troca de modelo)')
            if 'rede_casa' in dados:
                ligar_rede(bool(dados['rede_casa']))
            if novos.get('copia_pc') is False:
                ia.apagar_copia()
            elif novos.get('copia_pc'):
                m, _ = ia.escolher_modelo()
                if m:
                    threading.Thread(target=ia.preparar_copia, args=(m,), daemon=True).start()
            return self._enviar(200, {'ok': True, 'salvo': salvo, 'config': config.obter(),
                                      'rede': estado_rede()})
        if caminho in ('/api/atualizacao/verificar', '/api/atualizacao/iniciar'):
            dados = self._corpo_json()
            partes = [p for p in (dados.get('partes') or ['zims', 'acervo']) if p in ('zims', 'acervo')]
            if not partes:
                return self._enviar(200, {'ok': False, 'mensagem': 'Escolha pelo menos uma parte para atualizar.'})
            ok, msg = atualizacao.iniciar(caminho.endswith('verificar'), partes)
            return self._enviar(200, {'ok': ok, 'mensagem': msg, 'estado': atualizacao.estado()})
        if caminho == '/api/atualizacao/cancelar':
            return self._enviar(200, {'ok': atualizacao.cancelar(), 'estado': atualizacao.estado()})
        if caminho == '/api/ocr/fundo':
            ocr_lote.ligar(bool(self._corpo_json().get('ligado')))
            return self._enviar(200, {'ok': True, 'fundo': ocr_lote.resumo()})
        if caminho == '/api/ia/desligar':
            ia.desligar('(pedido do usuário)')
            return self._enviar(200, {'ok': True})
        return self._erro(404, 'Rota não encontrada.')

    # -------------------------------------------------------------- arquivos

    def _estatico(self, nome):
        p = os.path.normpath(os.path.join(config.STATIC, nome))
        if not p.startswith(os.path.normpath(config.STATIC) + os.sep) or not os.path.isfile(p):
            return self._erro(404, 'Arquivo não encontrado.')
        tipo = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if p.endswith('.mjs'):
            tipo = 'text/javascript'
        elif p.endswith('.wasm'):
            tipo = 'application/wasm'
        elif p.endswith('.gz'):
            tipo = 'application/octet-stream'   # o tesseract.js descompacta sozinho
        if tipo.startswith('text/') or tipo in ('application/javascript', 'image/svg+xml'):
            tipo += '; charset=utf-8'
        with open(p, 'rb') as f:
            dados = f.read()
        pesado = nome.replace('\\', '/').startswith(('ocr/core/', 'ocr/lang/', 'ocr/pdf', 'mapa/fonts/', 'mapa/maplibre'))
        self._enviar(200, dados, tipo, {'Cache-Control': 'max-age=604800' if pesado else 'no-cache'})

    # -------------------------------------------------------------- OCR

    def _ocr_imagem(self):
        """Foto ou imagem enviada pelo navegador (PC ou celular) -> texto."""
        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > 40 * 2**20:
            return self._enviar(200, {'ok': False, 'erro': 'Imagem vazia ou grande demais (máx. 40 MB).'})
        dados = self.rfile.read(n)
        tipo = (self.headers.get('Content-Type') or '').lower()
        ext = {'image/png': '.png', 'image/bmp': '.bmp', 'image/gif': '.gif', 'image/tiff': '.tif',
               'image/webp': '.webp', 'image/heic': '.heic'}.get(tipo.split(';')[0], '.jpg')
        os.makedirs(config.TMP, exist_ok=True)
        arq = os.path.join(config.TMP, 'ocr_envio_%d%s' % (threading.get_ident(), ext))
        with open(arq, 'wb') as f:
            f.write(dados)
        idioma = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}.get('idioma', '')
        try:
            r = ocr.ler_imagem(arq, idioma)
        finally:
            try:
                os.remove(arq)
            except OSError:
                pass
        if r.get('ok') and not r.get('texto', '').strip():
            r['aviso'] = 'Não achei texto nesta imagem. Tente uma foto mais de perto, reta e com boa luz.'
        return self._enviar(200, r)

    def _ocr_pagina(self, q):
        """Lê uma página de um PDF escaneado; guarda o texto no índice e a imagem para a tela."""
        try:
            doc_id, pagina = int(q.get('doc', 0)), max(1, int(q.get('p', 1)))
        except ValueError:
            return self._erro(400, 'Pedido inválido.')
        d = indice.documento(doc_id)
        if not d or d['tipo'] != 'pdf':
            return self._enviar(200, {'ok': False, 'erro': 'Documento não encontrado (a biblioteca foi atualizada?).'})
        os.makedirs(config.TMP, exist_ok=True)
        png = os.path.join(config.TMP, 'ocr_pag_%d_%d.png' % (doc_id, pagina))
        r = ocr.ler_pagina_pdf(config.absoluto(d['caminho']), pagina, q.get('idioma', ''), png)
        if r.get('ok'):
            r['img'] = '/ocr/img/%d_%d.png?t=%d' % (doc_id, pagina, int(time.time()))
            if not config.estado['somente_leitura']:
                try:
                    con = indice._conectar_escrita()
                    try:
                        indice.salvar_ocr(con, d['caminho'], d['tamanho'], pagina, r.get('texto', ''))
                    finally:
                        con.close()
                except Exception as e:
                    log.warning('não guardei o OCR: %s', e)
            _limpar_imagens_ocr()
        return self._enviar(200, r)

    def _imagem_ocr(self, doc_id, pagina):
        arq = os.path.join(config.TMP, 'ocr_pag_%d_%d.png' % (doc_id, pagina))
        if not os.path.isfile(arq):
            return self._erro(404, 'Imagem não encontrada.')
        with open(arq, 'rb') as f:
            self._enviar(200, f.read(), 'image/png')

    def _pagina_ocr(self, doc_id, q):
        d = indice.documento(doc_id)
        if not d:
            return self._enviar(404, pagina_simples('Não encontrado', '<p>Documento não está no índice.</p>'),
                                'text/html; charset=utf-8')
        from urllib.parse import quote
        try:
            p = max(1, int(q.get('p', 1)))
        except ValueError:
            p = 1
        original = '/arquivo/' + quote(d['caminho'])
        html = PAGINA_OCR % {'titulo': escape(d['titulo']), 'tema': escape(d['tema'] or ''), 'original': original,
                             'doc': doc_id, 'p': p, 'total': max(1, d['paginas'] or 1)}
        self._enviar(200, pagina_simples('Ler com OCR — ' + d['titulo'], html), 'text/html; charset=utf-8')

    def _tile_mapa(self, z, x, y):
        dados = mapa.tile(z, x, y)
        if not dados:
            self.send_response(204)  # mar aberto ou fora do Brasil: tile vazio
            self.send_header('Cache-Control', 'max-age=86400')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-protobuf')
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(dados)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(dados)

    def _fonte_mapa(self, pilha, faixa):
        # o estilo pode pedir 'Fonte A,Fonte B': usamos a primeira que existir; faixa sem arquivo = vazia
        dados = b''
        for nome in pilha.split(','):
            p = os.path.join(config.STATIC, 'mapa', 'fonts', os.path.basename(nome.strip()), faixa + '.pbf')
            if os.path.isfile(p):
                with open(p, 'rb') as f:
                    dados = f.read()
                break
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-protobuf')
        self.send_header('Content-Length', str(len(dados)))
        self.send_header('Cache-Control', 'max-age=86400')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(dados)

    def _arquivo(self, rel):
        rel = rel.replace('\\', '/')
        if not rel.startswith(PASTAS_LIBERADAS):
            return self._erro(403, 'Pasta não liberada.')
        try:
            p = config.absoluto(rel)
        except ValueError:
            return self._erro(403, 'Caminho inválido.')
        if not os.path.isfile(p):
            return self._erro(404, 'Arquivo não encontrado (foi removido do SSD?).')
        tam = os.path.getsize(p)
        tipo = mimetypes.guess_type(p)[0] or 'application/octet-stream'
        if p.lower().endswith('.epub'):
            tipo = 'application/epub+zip'
        elif p.lower().endswith('.apk'):
            tipo = 'application/vnd.android.package-archive'
        ini, fim, status = 0, tam - 1, 200
        rng = self.headers.get('Range')
        if rng:
            m = re.match(r'bytes=(\d*)-(\d*)', rng)
            if m:
                if m.group(1):
                    ini = int(m.group(1))
                    if m.group(2):
                        fim = min(int(m.group(2)), tam - 1)
                elif m.group(2):
                    ini = max(0, tam - int(m.group(2)))
                if ini > fim or ini >= tam:
                    self.send_response(416)
                    self.send_header('Content-Range', 'bytes */%d' % tam)
                    self.end_headers()
                    return
                status = 206
        self.send_response(status)
        self.send_header('Content-Type', tipo)
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(fim - ini + 1))
        if status == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (ini, fim, tam))
        nome = os.path.basename(p)
        disp = 'inline' if tipo in ('application/pdf', 'text/plain', 'text/html') else 'attachment'
        from urllib.parse import quote
        self.send_header('Content-Disposition', "%s; filename*=UTF-8''%s" % (disp, quote(nome)))
        self.end_headers()
        if self.command == 'HEAD':
            return
        with open(p, 'rb') as f:
            f.seek(ini)
            resta = fim - ini + 1
            while resta > 0:
                bloco = f.read(min(1 << 20, resta))
                if not bloco:
                    break
                self.wfile.write(bloco)
                resta -= len(bloco)

    def _ler(self, doc_id, q):
        """Leitor simples para EPUB/TXT/MD/HTML: mostra a seção com os termos destacados."""
        d = indice.documento(doc_id)
        if not d:
            return self._enviar(404, pagina_simples('Não encontrado', '<p>Documento não está no índice.</p>'),
                                'text/html; charset=utf-8')
        try:
            p = max(1, int(q.get('p', 1)))
        except ValueError:
            p = 1
        total = max(1, d['paginas'] or 1)
        p = min(p, total)
        texto = indice.texto_pagina(doc_id, p)
        consulta = q.get('q', '')
        corpo = destacar(texto, consulta) if texto else '<p class="vazio">Esta seção não tem texto pesquisável.</p>'
        from urllib.parse import quote
        qq = '&q=' + quote(consulta) if consulta else ''
        nome_secao = next((l.strip() for l in (texto or '').splitlines() if l.strip()), '')[:80]
        nav = '<nav class="leitor-nav">'
        if p > 1:
            nav += '<a class="botao" href="/ler/%d?p=%d">← Anterior</a>' % (doc_id, p - 1)
        nav += '<span><b>%s</b><br><small>seção %d de %d</small></span>' % (escape(nome_secao), p, total)
        if p < total:
            nav += '<a class="botao" href="/ler/%d?p=%d">Próxima →</a>' % (doc_id, p + 1)
        nav += '</nav>'
        ferramentas = LEITOR_FERRAMENTAS % {'doc': doc_id, 'p': p, 'total': total, 'q': escape(consulta)}
        original = '/arquivo/' + quote(d['caminho'])
        html = ('<header class="leitor-cab"><a href="/" class="voltar">← Biblioteca</a>'
                '<h1>%s</h1><p class="meta">%s · %s · <a href="%s">baixar o arquivo original</a></p></header>'
                '%s%s<article class="leitor">%s</article>%s'
                '<script>var m=document.querySelector("article mark");if(m)m.scrollIntoView({block:"center"});</script>'
                % (escape(d['titulo']), escape(d['tema']), escape(d['tipo'].upper()), original, ferramentas, nav, corpo, nav))
        self._enviar(200, pagina_simples(d['titulo'], html), 'text/html; charset=utf-8')

    # -------------------------------------------------------------- kiwix

    def _ler_epub_kiwix(self, caminho, q):
        """EPUB de dentro das enciclopédias (ex.: livros da Gutenberg): em vez de baixar um
        arquivo que o Windows não abre, mostra o livro no leitor, capítulo por capítulo."""
        st = kiwix.estado()
        livro = _epub_kiwix(st['porta'], caminho) if st['porta'] and kiwix.disponivel() else None
        if not livro or not livro['paginas']:
            self.path = caminho + '?baixar=1'
            return self._proxy_kiwix()
        try:
            p = max(1, int(q.get('p', 1)))
        except ValueError:
            p = 1
        total = len(livro['paginas'])
        p = min(p, total)
        texto = livro['paginas'][p - 1]
        corpo = destacar(texto, '') if texto.strip() else '<p class="vazio">Esta parte não tem texto.</p>'
        nomes = livro['nomes_cap']
        nav = '<nav class="leitor-nav">'
        if p > 1:
            nav += '<a class="botao" href="?p=%d">← Anterior</a>' % (p - 1)
        nav += '<span><b>%s</b><br><small>parte %d de %d</small></span>' % (escape(nomes[p - 1]), p, total)
        if p < total:
            nav += '<a class="botao" href="?p=%d">Próxima →</a>' % (p + 1)
        nav += '</nav>'
        opcoes = ''.join('<option value="%d"%s>%d. %s</option>' % (i + 1, ' selected' if i + 1 == p else '', i + 1, escape(n))
                         for i, n in enumerate(nomes))
        ir = ('<form class="leitor-ferramentas" onsubmit="return false"><label>Ir para: '
              '<select onchange="location.href=\'?p=\'+this.value" style="max-width:100%%">%s</select></label></form>' % opcoes)
        extras = ''
        if livro['html']:
            extras = ' · <a href="%s">ver com a formatação e as figuras originais</a>' % escape(livro['html'])
        html = ('<header class="leitor-cab"><a href="javascript:history.back()" class="voltar">← Voltar</a>'
                '<h1>%s</h1><p class="meta">Livro digital (EPUB) lido aqui mesmo, sem programa extra%s · '
                '<a href="%s?baixar=1">baixar o arquivo EPUB</a> (para ler no celular ou num leitor de livros)</p></header>'
                '%s%s<article class="leitor">%s</article>%s'
                % (escape(livro['titulo']), extras, escape(caminho), ir, nav, corpo, nav))
        self._enviar(200, pagina_simples(livro['titulo'], html), 'text/html; charset=utf-8')

    def _proxy_kiwix(self):
        """Repassa /kiwix/... para o kiwix-serve (que só escuta em 127.0.0.1).
        Assim os links funcionam também nos celulares, no modo rede da casa."""
        st = kiwix.estado()
        if not st['porta'] or not kiwix.disponivel():
            msg = st['erro'] or 'As enciclopédias ainda estão abrindo. Tente de novo em alguns segundos.'
            return self._enviar(503, pagina_simples('Enciclopédias', '<p>%s</p><p><a href="/">Voltar</a></p>'
                                                    % escape(msg)), 'text/html; charset=utf-8')
        c = http.client.HTTPConnection('127.0.0.1', st['porta'], timeout=60)
        cab = {}
        for k in ('Range', 'If-None-Match', 'If-Modified-Since', 'Accept-Encoding', 'Accept', 'Accept-Language'):
            if self.headers.get(k):
                cab[k] = self.headers[k]
        try:
            c.request(self.command, self.path, headers=cab)
            r = c.getresponse()
        except OSError as e:
            c.close()
            return self._erro(502, 'Kiwix não respondeu: %s' % e)
        try:
            self.send_response(r.status)
            for k, v in r.getheaders():
                if k.lower() in ('content-type', 'content-length', 'content-range', 'accept-ranges', 'etag',
                                 'cache-control', 'content-encoding', 'last-modified', 'location', 'vary',
                                 'content-security-policy'):
                    self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    bloco = r.read(65536)
                    if not bloco:
                        break
                    self.wfile.write(bloco)
        finally:
            c.close()


# ------------------------------------------------------------------ páginas e textos

_epubs = {}
_epubs_lock = threading.Lock()


def _epub_kiwix(porta, caminho, guardar=8):
    """Baixa o EPUB do kiwix-serve para a pasta temporária e separa os capítulos (guarda os últimos lidos)."""
    with _epubs_lock:
        if caminho in _epubs:
            return _epubs[caminho]
    import hashlib
    arq = os.path.join(config.TMP, 'epub_%s.epub' % hashlib.sha1(caminho.encode('utf-8')).hexdigest()[:16])
    try:
        if not os.path.isfile(arq):
            c = http.client.HTTPConnection('127.0.0.1', porta, timeout=60)
            try:
                c.request('GET', caminho)
                r = c.getresponse()
                if r.status != 200:
                    return None
                dados = r.read(300 * 2**20)
            finally:
                c.close()
            with open(arq + '.part', 'wb') as f:
                f.write(dados)
            os.replace(arq + '.part', arq)
        caps = textos.capitulos_epub(arq)
    except Exception:
        log.exception('não consegui abrir o EPUB %s', caminho)
        return None
    nomes = [n or 'Parte %d' % (i + 1) for i, (n, _) in enumerate(caps)]
    # a Gutenberg guarda também uma versão HTML ao lado do EPUB ("Livro.123.epub" -> "Livro.123.html"
    # nos ZIMs antigos, "Livro.123" nos novos)
    html_irmao = None
    for alvo in (caminho[:-5] + '.html', caminho[:-5]):
        c = http.client.HTTPConnection('127.0.0.1', porta, timeout=10)
        try:
            c.request('HEAD', alvo)
            r = c.getresponse()
            if r.status == 200 and 'html' in (r.getheader('Content-Type') or ''):
                html_irmao = alvo
                break
        except OSError:
            pass
        finally:
            c.close()
    titulo = re.sub(r'\.\d+$', '', unquote(caminho.rsplit('/', 1)[-1])[:-5])
    livro = {'titulo': titulo, 'paginas': [t for _, t in caps], 'nomes_cap': nomes, 'html': html_irmao}
    with _epubs_lock:
        _epubs[caminho] = livro
        while len(_epubs) > guardar:
            _epubs.pop(next(iter(_epubs)))
    try:
        arqs = sorted((os.path.join(config.TMP, f) for f in os.listdir(config.TMP) if f.startswith('epub_')),
                      key=os.path.getmtime, reverse=True)
        for a in arqs[30:]:
            os.remove(a)
    except OSError:
        pass
    return livro


def _limpar_imagens_ocr(manter=40):
    try:
        arqs = [os.path.join(config.TMP, f) for f in os.listdir(config.TMP) if f.startswith('ocr_pag_')]
        arqs.sort(key=os.path.getmtime, reverse=True)
        for a in arqs[manter:]:
            os.remove(a)
    except OSError:
        pass


PAGINA_OCR = """<style>body.pagina-simples{max-width:1400px}</style><header class="leitor-cab"><a href="/" class="voltar">← Biblioteca</a>
<h1>%(titulo)s</h1><p class="meta">%(tema)s · PDF escaneado · <a href="%(original)s#page=%(p)d" id="o-original" target="_blank">abrir o PDF original</a></p></header>
<nav class="leitor-nav"><button class="botao" id="o-ant" type="button">← Anterior</button>
<span>Página <input id="o-pag" type="number" min="1" max="%(total)d" value="%(p)d" style="width:5em"> de %(total)d</span>
<button class="botao" id="o-prox" type="button">Próxima →</button></nav>
<div class="ocr-grade"><figure class="ocr-img"><img id="o-img" alt="Imagem da página" hidden onload="this.hidden=false"></figure>
<section class="ocr-texto"><p class="status-linha" id="o-st">Lendo a página…</p>
<textarea id="o-txt" spellcheck="false" placeholder="O texto lido aparece aqui (dá para corrigir antes de copiar ou traduzir)."></textarea>
<p class="acoes-ocr"><button class="botao" id="o-copiar" type="button">Copiar texto</button>
<button class="botao secundario" id="o-traduzir" type="button">Traduzir</button>
<button class="botao secundario" id="o-perguntar" type="button">Buscar estas palavras</button>
<button class="botao secundario" id="o-tess" type="button">Ler de novo com o Tesseract</button></p>
<p class="meta" id="o-quem">Leitura feita pelo próprio Windows (OCR), sem internet. Confira números e doses na imagem ao lado.</p></section></div>
<script src="/static/ocr/leitor.js"></script>
<script>
(function () {
  var doc = %(doc)d, total = %(total)d, p = %(p)d;
  var $ = function (s) { return document.querySelector(s); };
  function ler(n) {
    p = Math.max(1, Math.min(total, n));
    $('#o-pag').value = p;
    $('#o-original').href = $('#o-original').href.split('#')[0] + '#page=' + p;
    history.replaceState(null, '', '/ocr/' + doc + '?p=' + p);
    $('#o-st').textContent = 'Lendo a página ' + p + '…'; $('#o-txt').value = '';
    fetch('/api/ocr/pagina?doc=' + doc + '&p=' + p).then(function (r) { return r.json(); }).then(function (r) {
      if (!r.ok) { semWindows(r.erro); return; }
      if (r.paginas) total = r.paginas;
      $('#o-img').src = r.img;
      $('#o-txt').value = r.texto || '';
      $('#o-quem').textContent = 'Leitura feita pelo próprio Windows (OCR), sem internet. Confira números e doses na imagem ao lado.';
      $('#o-st').textContent = r.texto ? 'Lida em ' + String(r.segundos).replace('.', ',') + ' s.' : 'Não achei texto nesta página (pode ser só imagem). Tente o Tesseract.';
    }).catch(function () { $('#o-st').textContent = 'Não consegui falar com o app. Ele está aberto?'; });
  }
  function tesseract(fonte) {
    $('#o-st').textContent = 'Tesseract: preparando…';
    return LeitorOCR.tesseract(fonte, function (st, x) { $('#o-st').textContent = 'Tesseract: ' + LeitorOCR.progresso(st, x); })
      .then(function (r) {
        $('#o-txt').value = r.texto || '';
        $('#o-quem').textContent = 'Leitura feita pelo Tesseract, dentro do navegador, sem internet. Confira números e doses na imagem ao lado.';
        $('#o-st').textContent = r.texto ? 'Lida pelo Tesseract em ' + String(r.segundos).replace('.', ',') + ' s.' : 'O Tesseract não achou texto nesta página.';
      }).catch(function (e) { $('#o-st').textContent = 'Erro no Tesseract: ' + e.message; });
  }
  function semWindows(motivo) {
    // sem o leitor do Windows: o navegador desenha a página (pdf.js) e o Tesseract lê
    $('#o-st').textContent = 'Desenhando a página no navegador…';
    LeitorOCR.pdfPagina($('#o-original').href.split('#')[0], p).then(function (r) {
      total = r.paginas;
      $('#o-img').src = r.canvas.toDataURL('image/png');
      return tesseract(r.canvas);
    }).catch(function (e) { $('#o-st').textContent = (motivo ? motivo + ' ' : '') + 'Também não consegui abrir o PDF no navegador: ' + e.message; });
  }
  $('#o-tess').onclick = function () {
    var img = $('#o-img');
    if (img.src && !img.hidden) tesseract(img); else semWindows('');
  };
  $('#o-ant').onclick = function () { ler(p - 1); };
  $('#o-prox').onclick = function () { ler(p + 1); };
  $('#o-pag').onchange = function () { ler(parseInt(this.value, 10) || 1); };
  $('#o-copiar').onclick = function () {
    var t = $('#o-txt'); t.select();
    try { if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(t.value); else document.execCommand('copy'); } catch (e) {}
    this.textContent = 'Copiado!';
    var b = this; setTimeout(function () { b.textContent = 'Copiar texto'; }, 1500);
  };
  $('#o-traduzir').onclick = function () {
    try { sessionStorage.setItem('traduzir', $('#o-txt').value); } catch (e) {}
    location.href = '/#/traduzir?auto=1';
  };
  $('#o-perguntar').onclick = function () {
    var t = (window.getSelection && String(window.getSelection())) || '';
    if (!t) { var v = $('#o-txt'); t = v.value.substring(v.selectionStart, v.selectionEnd) || v.value.split(/\\s+/).slice(0, 6).join(' '); }
    location.href = '/#/busca?q=' + encodeURIComponent(t.trim().slice(0, 200));
  };
  ler(p);
})();
</script>"""


LEITOR_FERRAMENTAS = r"""<div class="leitor-ferramentas">
<button class="botao secundario" id="b-sumario" type="button">☰ Sumário</button>
<form id="f-dentro" class="busca-dentro"><input type="search" id="q-dentro" value="%(q)s" placeholder="🔍 Procurar neste livro…" aria-label="Procurar neste livro">
<button class="botao" type="submit">Procurar</button></form>
<form id="f-ir" class="ir-para"><label>Ir para seção <input type="number" id="n-ir" min="1" max="%(total)d" value="%(p)d"></label></form>
</div>
<div id="painel-sumario" class="cartao painel-leitor" hidden></div>
<div id="res-dentro" class="cartao painel-leitor" hidden></div>
<script>
(function () {
  var doc = %(doc)d, atual = %(p)d, secoes = null;
  var $ = function (s) { return document.querySelector(s); };
  function esc(t) { return String(t).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function link(p, txt, cls) { return '<a class="' + (cls || '') + (p === atual ? ' atual' : '') + '" href="/ler/' + doc + '?p=' + p + '">' + txt + '</a>'; }
  function carregar() {
    if (secoes) return Promise.resolve(secoes);
    return fetch('/api/doc/' + doc + '/sumario').then(function (r) { return r.json(); }).then(function (r) { secoes = r.secoes; return secoes; });
  }
  // livros divididos em capítulos ("Gênesis 1", "Gênesis 2"...): agrupa por livro e mostra os números
  function agrupar(lista) {
    var grupos = [], porNome = {}, casam = 0;
    lista.forEach(function (s) {
      var m = s.t.match(/^(.+?)[,\s]+(?:cap[ií]tulo\s+)?(\d+)$/i);
      var nome = m ? m[1].replace(/[,\s]+cap[ií]tulo$/i, '') : null;
      if (m) casam++;
      if (!nome) { grupos.push({ nome: s.t || ('Seção ' + s.p), itens: [s], solto: true }); return; }
      if (!porNome[nome]) { porNome[nome] = { nome: nome, itens: [] }; grupos.push(porNome[nome]); }
      porNome[nome].itens.push({ p: s.p, t: m[2] });
    });
    return casam >= lista.length * 0.5 ? grupos : null;
  }
  function mostrarSumario() {
    var painel = $('#painel-sumario');
    if (!painel.hidden) { painel.hidden = true; return; }
    $('#res-dentro').hidden = true;
    painel.hidden = false; painel.innerHTML = '<p class="status-linha">Carregando o sumário…</p>';
    carregar().then(function (lista) {
      var g = agrupar(lista);
      if (!g) {
        painel.innerHTML = '<h2>Sumário</h2><ol class="sumario-lista">' + lista.map(function (s) {
          return '<li>' + link(s.p, esc(s.t || 'Seção ' + s.p)) + '</li>'; }).join('') + '</ol>';
      } else {
        painel.innerHTML = '<h2>Sumário <small class="meta">toque no livro para ver os capítulos</small></h2><div class="sumario-livros">' +
          g.map(function (x, i) {
            if (x.solto) return link(x.itens[0].p, esc(x.nome), 'livro-solto');
            var temAtual = x.itens.some(function (it) { return it.p === atual; });
            return '<button type="button" class="livro' + (temAtual ? ' aberto' : '') + '" data-i="' + i + '">' + esc(x.nome) + '</button>';
          }).join('') + '</div><div id="capitulos"></div>';
        var abrir = function (i) {
          var x = g[i];
          painel.querySelectorAll('button.livro').forEach(function (b) { b.classList.toggle('aberto', +b.dataset.i === i); });
          $('#capitulos').innerHTML = '<h3>' + esc(x.nome) + '</h3><div class="sumario-caps">' +
            x.itens.map(function (it) { return link(it.p, esc(it.t)); }).join('') + '</div>';
          $('#capitulos').scrollIntoView({ block: 'nearest' });
        };
        painel.querySelectorAll('button.livro').forEach(function (b) { b.onclick = function () { abrir(+b.dataset.i); }; });
        var aberto = painel.querySelector('button.livro.aberto');
        if (aberto) abrir(+aberto.dataset.i);
      }
    });
  }
  $('#b-sumario').onclick = mostrarSumario;
  $('#f-ir').onsubmit = function (ev) { ev.preventDefault(); location.href = '/ler/' + doc + '?p=' + Math.max(1, Math.min(%(total)d, parseInt($('#n-ir').value, 10) || 1)); };
  $('#n-ir').onchange = function () { $('#f-ir').onsubmit(new Event('submit')); };
  $('#f-dentro').onsubmit = function (ev) {
    ev.preventDefault();
    var q = $('#q-dentro').value.trim(), res = $('#res-dentro');
    if (!q) return;
    $('#painel-sumario').hidden = true;
    res.hidden = false; res.innerHTML = '<p class="status-linha">Procurando…</p>';
    fetch('/api/doc/' + doc + '/busca?q=' + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (r) {
      var l = r.resultados || [];
      if (!l.length) { res.innerHTML = '<p>Nada encontrado neste livro. Tente outras palavras.</p>'; return; }
      res.innerHTML = '<h2>' + l.length + (l.length >= 80 ? '+' : '') + ' trechos com “' + esc(q) + '”' +
        (r.modo === 'or' ? ' <small class="meta">(com qualquer uma das palavras)</small>' : '') + '</h2>' +
        l.map(function (x) {
          return '<p class="trecho"><a class="pag" href="/ler/' + doc + '?p=' + x.p + '&q=' + encodeURIComponent(q) + '">' +
            esc(x.t || 'Seção ' + x.p) + '</a> ' + x.trecho + '</p>';
        }).join('');
    }).catch(function (e) { res.innerHTML = '<p class="erro">Erro: ' + esc(e.message) + '</p>'; });
  };
  if ($('#q-dentro').value) { /* veio de uma busca: deixa a palavra pronta para procurar de novo */ }
})();
</script>"""


def pagina_simples(titulo, corpo):
    return ('<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s · Biblioteca Offline</title><link rel="stylesheet" href="/static/estilo.css">'
            '<link rel="icon" href="/static/icone.svg"><script src="/static/tema.js"></script></head>'
            '<body class="pagina-simples">%s</body></html>' % (escape(titulo), corpo))


def destacar(texto, consulta):
    ts = [textos.sem_acento(t)[:max(4, len(t) - 2)] for t in textos.termos(consulta)] if consulta else []
    pars = [x.strip() for x in re.split(r'\n\s*\n|\n(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ•\-\d])', texto) if x.strip()]
    saida = []
    for par in pars:
        h = escape(textos.limpar_controle(par)).replace('\n', ' ')
        if ts:
            def marca(m):
                w = m.group(0)
                base = textos.sem_acento(w.lower())
                return '<mark>%s</mark>' % w if any(base.startswith(t) for t in ts) else w
            h = re.sub(r'\w+', marca, h)
        saida.append('<p>%s</p>' % h)
    return '\n'.join(saida)


# ------------------------------------------------------------------ situação

def situacao(cliente='127.0.0.1'):
    total, livre = sistema.memoria()
    d = sistema.disco()
    zims_ok, zims_ruins = kiwix.listar_zims()
    instaladores = []
    for p in sistema.achar('6_Instaladores/*'):
        if os.path.isfile(p) and not p.endswith('.ok'):
            instaladores.append({'nome': os.path.basename(p), 'tamanho': os.path.getsize(p),
                                 'url': '/arquivo/' + config.rel(p)})
    mapas = [os.path.basename(p) for p in sistema.achar('2_Mapas/*.map')]
    # mapas offline do celular (Organic Maps): só os que terminaram de baixar
    mwm = [p for p in sistema.achar('2_Mapas/OrganicMaps_*/*.mwm') if os.path.exists(p + '.ok')]
    mapas_celular = {'arquivos': len(mwm), 'gb': round(sum(os.path.getsize(p) for p in mwm) / 2**30, 2),
                     'pasta': config.rel(os.path.dirname(mwm[0])) if mwm else '',
                     'instrucoes': '/arquivo/2_Mapas/COMO_USAR_NO_CELULAR.txt'
                     if os.path.exists(os.path.join(config.MAPAS, 'COMO_USAR_NO_CELULAR.txt')) else ''}
    st_ia = ia.estado()
    faltas = []
    if not zims_ok:
        faltas.append('Nenhuma enciclopédia (.zim) em 1_Enciclopedia: rode o _Baixador.')
    if not kiwix.achar_exe():
        faltas.append('Falta o kiwix-serve em Programas\\KiwixServe: rode o _Baixador.')
    if not st_ia['disponivel']:
        faltas.append(st_ia['motivo'] + ' Sem isso, a busca funciona, mas a "Pergunte" e a tradução não.')
    res = indice.resumo()
    downloads = estado_downloads()
    if res['documentos'] == 0 and not indice.progresso['rodando']:
        faltas.append('Nenhum documento em 5_Acervo. Copie PDFs/EPUBs para lá ou rode o _Baixador.')
    return {
        'kiwix': kiwix.estado(),
        'zims': [os.path.basename(z) for z in zims_ok],
        'zims_incompletos': zims_ruins,
        'livros': kiwix.livros(),
        'indice': res,
        'progresso': indice.progresso,
        'ia': st_ia,
        'ram': {'total': total, 'livre': livre},
        'disco': d,
        'somente_leitura': config.estado['somente_leitura'],
        'aviso_disco': config.estado['aviso_disco'],
        'instaladores': instaladores,
        'mapas': mapas,
        'mapas_celular': mapas_celular,
        'mapa_pc': mapa.info(),
        'ocr': {'fundo': ocr_lote.resumo(), 'lidos': res.get('ocr', 0)},
        'faltas': faltas,
        'rede': estado_rede(),
        'downloads': downloads,
        'local': _local(cliente),
        'raiz': os.path.splitdrive(config.RAIZ)[0] or config.RAIZ,
    }


def estado_downloads():
    """Se os baixadores (_Baixador) estiverem rodando: quanto falta e as últimas linhas do registro."""
    pasta = os.path.join(config.RAIZ, '_Baixador')
    out = {'ativo': False, 'falta_gb': 0, 'ultimas': []}
    try:
        pend, ts = open(os.path.join(pasta, 'reserva_downloads.txt')).read().split()
        if time.time() - int(ts) < 180:
            out['ativo'] = True
            out['falta_gb'] = round(int(pend) / 2**30, 1)
    except (OSError, ValueError):
        pass
    try:
        reg = os.path.join(pasta, 'registro.txt')
        if time.time() - os.path.getmtime(reg) < 600:
            out['ativo'] = True
            with open(reg, 'rb') as f:
                f.seek(max(0, os.path.getsize(reg) - 4000))
                linhas = f.read().decode('utf-8', 'replace').splitlines()[1:]
            out['ultimas'] = [l for l in linhas if l.strip()][-6:]
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ servidores

_servidores = {'local': None, 'rede': None}


class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128  # o mapa pede dezenas de tiles de uma vez
    allow_reuse_address = False  # no Windows, reuse permite duas instâncias na mesma porta


def iniciar(porta, host='127.0.0.1'):
    s = Servidor((host, porta), Manipulador)
    threading.Thread(target=s.serve_forever, daemon=True, name='http-%s' % host).start()
    return s


def ligar_rede(ligar):
    """Modo rede da casa: abre uma segunda porta em todas as interfaces (celulares no hotspot)."""
    atual = _servidores['rede']
    if ligar and atual is None:
        try:
            porta = sistema.porta_livre(8810, host='0.0.0.0')
            _servidores['rede'] = iniciar(porta, '0.0.0.0')
            config.estado['porta_rede'] = porta
            log.info('modo rede da casa ligado na porta %d', porta)
        except Exception as e:
            log.warning('não consegui ligar a rede: %s', e)
            config.estado['porta_rede'] = None
    elif not ligar and atual is not None:
        atual.shutdown()
        atual.server_close()
        _servidores['rede'] = None
        config.estado['porta_rede'] = None
        log.info('modo rede da casa desligado')
    config.salvar({'rede_casa': bool(_servidores['rede'])})


def estado_rede():
    porta = config.estado['porta_rede']
    if not porta:
        return {'ligada': False, 'enderecos': []}
    return {'ligada': True, 'porta': porta, 'enderecos': ['http://%s:%d' % (ip, porta) for ip in sistema.ips_locais()]}
