"""Índice de documentos do 5_Acervo em SQLite FTS5 (busca sem acento), incremental."""
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import quote

from . import config, sistema, textos

log = config.log

EXTENSOES = {'.pdf', '.epub', '.txt', '.md', '.html', '.htm'}
TEMPO_MAX_ARQUIVO = 600  # segundos por arquivo antes de desistir (mapas enormes levam minutos)
VERSAO_EXTRATOR = 3       # 2: lê PDFs protegidos (RC4/AES-128); 3: refaz os que estouraram o tempo

ESQUEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY,
    caminho TEXT UNIQUE NOT NULL,
    tamanho INTEGER, mtime REAL,
    titulo TEXT, tema TEXT, idioma TEXT, tipo TEXT,
    paginas INTEGER DEFAULT 0,
    situacao TEXT, erro TEXT,
    indexado REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS pag USING fts5(
    titulo, texto, doc_id UNINDEXED, pagina UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT);
CREATE TABLE IF NOT EXISTS ocr (
    caminho TEXT, tamanho INTEGER, pagina INTEGER, texto TEXT,
    PRIMARY KEY (caminho, pagina)
);
CREATE VIRTUAL TABLE IF NOT EXISTS titulos USING fts5(
    titulo, doc_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""

progresso = {'rodando': False, 'total': 0, 'feitos': 0, 'atual': '', 'inicio': 0, 'fim': 0,
             'novos': 0, 'removidos': 0, 'erros': 0, 'mensagem': ''}
_lock_idx = threading.Lock()
_local = threading.local()
_memoria = None  # conexão em memória quando o SSD é somente leitura e não há índice


def _conectar_escrita():
    con = sqlite3.connect(config.ARQ_INDICE, timeout=30, check_same_thread=False)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.executescript(ESQUEMA)
    for col in ('pag_ini', 'pag_fim'):
        try:
            con.execute('ALTER TABLE docs ADD COLUMN %s INTEGER' % col)
        except sqlite3.OperationalError:
            pass   # já existe
    con.commit()
    return con


def _faixa(con, doc_id):
    """Condição SQL para as páginas de um documento. A coluna doc_id do FTS não tem índice
    (consultá-la varre o índice inteiro); a faixa de rowids guardada em docs é instantânea."""
    try:
        r = con.execute('SELECT pag_ini, pag_fim FROM docs WHERE id = ?', (doc_id,)).fetchone()
    except sqlite3.OperationalError:
        r = None
    if r and r[0] is not None:
        return 'rowid BETWEEN ? AND ? AND doc_id = ?', [r[0], r[1], doc_id]
    return 'doc_id = ?', [doc_id]


def _preencher_titulos(con):
    """Uma vez só: índice de títulos (uma linha por documento) para bases criadas antes dele."""
    if con.execute('SELECT 1 FROM titulos LIMIT 1').fetchone() or not con.execute('SELECT 1 FROM docs LIMIT 1').fetchone():
        return
    with con:
        con.execute('INSERT INTO titulos (titulo, doc_id) SELECT titulo, id FROM docs')
    log.info('índice de títulos criado')


def _titulo_mudou(con, doc_id, novo):
    con.execute('UPDATE titulos SET titulo = ? WHERE doc_id = ?', (novo, doc_id))


def _preencher_faixas(con):
    """Uma vez só: calcula a faixa de rowids dos documentos indexados antes desta versão."""
    if not con.execute('SELECT 1 FROM docs WHERE pag_ini IS NULL AND situacao IS NOT NULL LIMIT 1').fetchone():
        return
    progresso['atual'] = 'Organizando o índice (uma vez só)...'
    t = time.time()
    faixas = con.execute('SELECT doc_id, MIN(rowid), MAX(rowid) FROM pag GROUP BY doc_id').fetchall()
    with con:
        con.executemany('UPDATE docs SET pag_ini = ?, pag_fim = ? WHERE id = ?', [(a, b, d) for d, a, b in faixas])
    log.info('faixas de páginas calculadas para %d documentos em %.0fs', len(faixas), time.time() - t)


def conexao():
    """Conexão de leitura por thread."""
    global _memoria
    con = getattr(_local, 'con', None)
    if con is not None:
        return con
    if config.estado['somente_leitura']:
        if os.path.exists(config.ARQ_INDICE):
            uri = 'file:%s?mode=ro&immutable=1' % quote(config.ARQ_INDICE.replace('\\', '/'))
            con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        else:
            if _memoria is None:
                _memoria = sqlite3.connect(':memory:', check_same_thread=False)
                _memoria.executescript(ESQUEMA)
            con = _memoria
    else:
        con = sqlite3.connect(config.ARQ_INDICE, timeout=30, check_same_thread=False)
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript(ESQUEMA)
    con.row_factory = sqlite3.Row
    _local.con = con
    return con


# ------------------------------------------------------------------ varredura

def _titulos_indice():
    """_indice.txt (formato 'arquivo<TAB>Título') de qualquer pasta do acervo."""
    t = {}
    for arq in sistema.achar('5_Acervo/**/_indice.txt'):
        pasta = os.path.dirname(arq)
        try:
            with open(arq, encoding='utf-8-sig', errors='replace') as f:
                for linha in f:
                    if '\t' in linha:
                        nome, tit = linha.rstrip('\r\n').split('\t', 1)
                        tit = tit.strip().rstrip('.')
                        if tit:
                            t[os.path.normcase(os.path.join(pasta, nome.strip()))] = tit
        except OSError:
            pass
    return t


def varrer():
    """Lista (caminho_abs, rel, tamanho, mtime) dos documentos prontos do acervo."""
    achados = []
    if not os.path.isdir(config.ACERVO):
        return achados
    # os.scandir já traz tamanho e data (no Windows sem consultar o disco de novo): rápido mesmo
    # com dezenas de milhares de arquivos numa pasta
    pilha = [config.ACERVO]
    while pilha:
        pasta = pilha.pop()
        try:
            entradas = list(os.scandir(pasta))
        except OSError:
            continue
        nomes = {e.name for e in entradas}
        for e in entradas:
            if e.is_dir(follow_symlinks=False):
                if not e.name.startswith(('.', '$')):
                    pilha.append(e.path)
                continue
            ext = os.path.splitext(e.name)[1].lower()
            if ext not in EXTENSOES or e.name.startswith('_indice'):
                continue
            try:
                st = e.stat()
            except OSError:
                continue
            if not documento_completo(e.path, ext, tem_ok=(e.name + '.ok') in nomes, st=st):
                continue
            achados.append((e.path, config.rel(e.path), st.st_size, round(st.st_mtime, 1)))
    return achados


def documento_completo(p, ext, tem_ok=None, st=None):
    """Ignora arquivos ainda sendo baixados/copiados (o baixador grava direto no nome final)."""
    if st is None:
        try:
            st = os.stat(p)
        except OSError:
            return False
    if st.st_size == 0:
        return False
    if tem_ok if tem_ok is not None else os.path.exists(p + '.ok'):
        return True
    try:
        if ext == '.pdf':
            with open(p, 'rb') as f:
                f.seek(max(0, st.st_size - 2048))
                return b'%%EOF' in f.read()
        if ext == '.epub':
            import zipfile
            return zipfile.is_zipfile(p)
    except OSError:
        return False
    return time.time() - st.st_mtime > 15


def tema_de(rel):
    partes = rel.split('/')
    # 5_Acervo/<Tema>/...
    return partes[1].replace('_', ' ') if len(partes) > 2 else 'Geral'


# ------------------------------------------------------------------ trabalhadores

class Trabalhador:
    def __init__(self):
        py = sys.executable
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'trabalhador.py')
        flags = 0x08000000 if os.name == 'nt' else 0  # sem janela
        # prioridade baixa: indexar não deve deixar o computador lento
        flags |= 0x00004000 if os.name == 'nt' else 0  # BELOW_NORMAL_PRIORITY_CLASS
        self.p = subprocess.Popen([py, '-I', '-X', 'utf8', script], cwd=config.APP, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
        sistema.prender_ao_job(self.p)
        self.respostas = queue.Queue()
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        try:
            for linha in self.p.stdout:
                self.respostas.put(linha)
        except Exception:
            pass
        self.respostas.put(None)

    def extrair(self, caminho, ext):
        try:
            self.p.stdin.write(json.dumps({'caminho': caminho, 'ext': ext}).encode('ascii') + b'\n')
            self.p.stdin.flush()
        except OSError:
            return None
        try:
            linha = self.respostas.get(timeout=TEMPO_MAX_ARQUIVO)
        except queue.Empty:
            return {'ok': False, 'erro': 'demorou demais (mais de %d s)' % TEMPO_MAX_ARQUIVO, 'travou': True}
        if linha is None:
            return {'ok': False, 'erro': 'o extrator fechou (arquivo problemático)', 'travou': True}
        try:
            return json.loads(linha)
        except ValueError:
            return {'ok': False, 'erro': 'resposta inválida do extrator'}

    def fechar(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        sistema.encerrar(self.p, 2)


# ------------------------------------------------------------------ indexação

def atualizar(bloquear=False):
    """Atualiza o índice em segundo plano. Retorna False se já estiver rodando."""
    if config.estado['somente_leitura']:
        progresso['mensagem'] = 'SSD somente leitura: o índice não pode ser atualizado.'
        return False
    if not _lock_idx.acquire(blocking=False):
        return False
    t = threading.Thread(target=_rodar, daemon=True, name='indexador')
    t.start()
    if bloquear:
        t.join()
    return True


def _rodar():
    try:
        _indexar()
    except Exception as e:
        log.exception('erro na indexação')
        progresso['mensagem'] = 'Erro ao atualizar o índice: %s' % e
    finally:
        progresso['rodando'] = False
        progresso['fim'] = time.time()
        _lock_idx.release()


def _indexar():
    progresso.update(rodando=True, total=0, feitos=0, atual='Procurando arquivos...', inicio=time.time(),
                     novos=0, removidos=0, erros=0, mensagem='')
    con = _conectar_escrita()
    achados = varrer()
    existentes = {r[0]: (r[1], r[2], r[3], r[4]) for r in
                  con.execute('SELECT caminho, id, tamanho, mtime, situacao FROM docs')}
    # extrator novo (ex.: passou a ler PDFs protegidos só por permissões): reprocessa esses
    m = con.execute("SELECT valor FROM meta WHERE chave = 'versao_extrator'").fetchone()
    versao = int(m[0]) if m else 1
    refazer = set()
    if versao < 2:
        refazer.add('protegido')
    if versao < 3:
        refazer.add('erro')
    vistos = set()
    pendentes = []
    for p, rel, tam, mt in achados:
        vistos.add(rel)
        e = existentes.get(rel)
        if e is None or e[1] != tam or abs((e[2] or 0) - mt) > 2 or e[3] in refazer:
            pendentes.append((p, rel, tam, mt))
    # removidos do disco
    for rel, (doc_id, _, _, _) in existentes.items():
        if rel not in vistos:
            onde, args = _faixa(con, doc_id)
            con.execute('DELETE FROM pag WHERE ' + onde, args)
            con.execute('DELETE FROM titulos WHERE doc_id = ?', (doc_id,))
            con.execute('DELETE FROM docs WHERE id = ?', (doc_id,))
            progresso['removidos'] += 1
    con.commit()
    # arquivos pequenos primeiro: a busca fica útil mais rápido
    pendentes.sort(key=lambda x: x[2])
    progresso['total'] = len(pendentes)
    _preencher_faixas(con)
    _preencher_titulos(con)
    _corrigir_titulos(con)
    if not pendentes:
        progresso['atual'] = ''
        con.close()
        log.info('índice em dia (%d documentos)', len(achados))
        return
    log.info('indexando %d documentos', len(pendentes))
    titulos = _titulos_indice()
    n = max(1, min(3, sistema.nucleos_fisicos() - 1, len(pendentes)))
    fila = queue.Queue()
    for item in pendentes:
        fila.put(item)
    resultados = queue.Queue()

    def trabalhar():
        tr = Trabalhador()
        try:
            while True:
                try:
                    item = fila.get_nowait()
                except queue.Empty:
                    break
                p, rel, tam, mt = item
                progresso['atual'] = os.path.basename(p)
                r = tr.extrair(p, os.path.splitext(p)[1].lower())
                if r is None or r.get('travou'):
                    tr.fechar()
                    tr = Trabalhador()
                    if r is None:
                        r = {'ok': False, 'erro': 'o extrator fechou'}
                resultados.put((item, r))
        finally:
            tr.fechar()
            resultados.put(None)

    for _ in range(n):
        threading.Thread(target=trabalhar, daemon=True).start()
    ativos = n
    while ativos:
        x = resultados.get()
        if x is None:
            ativos -= 1
            continue
        item, r = x
        try:
            _gravar(con, item, r, titulos)
        except sqlite3.Error as e:
            log.warning('não gravei %s: %s', item[1], e)
            if 'full' in str(e).lower() or 'disk' in str(e).lower():
                config.estado['aviso_disco'] = 'O SSD encheu durante a indexação. Libere espaço e clique em Atualizar.'
                break
        progresso['feitos'] += 1
    con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('ultima_atualizacao', str(time.time())))
    con.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', ('versao_extrator', str(VERSAO_EXTRATOR)))
    con.commit()
    # 'optimize' reescreve o índice inteiro: só vale a pena depois de lotes grandes
    # (no dia a dia o FTS5 já funde os segmentos sozinho)
    if progresso['novos'] >= 2000:
        try:
            con.execute("INSERT INTO pag(pag) VALUES('optimize')")
            con.commit()
        except sqlite3.Error:
            pass
    con.close()
    progresso['atual'] = ''
    log.info('indexação concluída: %d novos/alterados, %d removidos, %d com erro em %.0fs',
             progresso['novos'], progresso['removidos'], progresso['erros'], time.time() - progresso['inicio'])


def _corrigir_titulos(con):
    """Documentos indexados com título genérico ('SciELO Books...'): tira o título da capa, sem reextrair."""
    ruins = con.execute("SELECT id FROM docs WHERE titulo LIKE '%SciELO%' OR "
                        "(caminho LIKE '5_Acervo/Livros_SciELO/%' AND titulo NOT LIKE '% %')").fetchall()
    n = 0
    # títulos genéricos de metadado ('Distribution restriction', 'Livromiolo'...): volta ao nome do arquivo
    for doc_id, titulo, caminho in con.execute('SELECT id, titulo, caminho FROM docs').fetchall():
        if titulo and textos.titulo_generico(titulo):
            novo = textos.titulo_de_arquivo(caminho.rsplit('/', 1)[-1])
            if textos.parece_codigo(novo):
                onde, args = _faixa(con, doc_id)
                pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2, 3) '
                                                  'ORDER BY pagina', args)]
                novo = textos.titulo_scielo(pags) or textos.titulo_do_texto(pags)
            if novo and novo != titulo and not textos.parece_codigo(novo) and not textos.titulo_generico(novo):
                with con:
                    con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                    onde, args = _faixa(con, doc_id)
                    con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                    _titulo_mudou(con, doc_id, novo)
                n += 1
    for (doc_id,) in ruins:
        onde, args = _faixa(con, doc_id)
        pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2) '
                                          'ORDER BY pagina', args)]
        novo = textos.titulo_scielo(pags)
        if novo:
            with con:
                con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                _titulo_mudou(con, doc_id, novo)
            n += 1
    m = con.execute("SELECT valor FROM meta WHERE chave = 'versao_titulos'").fetchone()
    if not m or int(m[0]) < 2:
        progresso['atual'] = 'Melhorando títulos (uma vez só)...'  # versão 2: título da capa
        for doc_id, titulo, caminho in con.execute("SELECT id, titulo, caminho FROM docs WHERE situacao = 'ok'").fetchall():
            if titulo != textos.titulo_de_arquivo(caminho.rsplit('/', 1)[-1]) or textos.parece_codigo(titulo):
                continue
            onde, args = _faixa(con, doc_id)
            pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2, 3, 4) '
                                              'ORDER BY pagina', args)]
            novo = textos.titulo_pela_capa(pags, titulo)
            if novo and novo != titulo:
                with con:
                    con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                    con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                    _titulo_mudou(con, doc_id, novo)
                n += 1
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('versao_titulos', '2')")
    if not m or int(m[0]) < 3:
        # manuais do Exercito (título do catálogo com '?') e títulos tirados de cabeçalho/licença
        progresso['atual'] = 'Melhorando títulos (uma vez só)...'
        titulos_idx = _titulos_indice()
        ruim = re.compile(r'^minist[eé]rio d[ae] |licen[cç]a|tesauro|vers[aã]o preliminar|[^\w\s.,:;()\-–—/\'"ºª°&]', re.I)
        for doc_id, titulo, caminho in con.execute(
                "SELECT id, titulo, caminho FROM docs WHERE situacao = 'ok' AND (caminho LIKE '5_Acervo/Exercito_Brasileiro/%' "
                "OR titulo LIKE '% — Ministerio da Saude')").fetchall():
            if not (caminho.startswith('5_Acervo/Exercito_Brasileiro/') or ruim.search(titulo)):
                continue
            onde, args = _faixa(con, doc_id)
            pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2, 3, 4, 5, 6) '
                                              'ORDER BY pagina', args)]
            nome = caminho.rsplit('/', 1)[-1]
            do_arquivo = textos.titulo_de_arquivo(nome)
            cat = titulos_idx.get(os.path.normcase(config.absoluto(caminho)), '')
            novo = (textos.reparar_interrogacoes(cat, pags) if cat else '') or textos.titulo_pela_capa(pags, do_arquivo)
            if not novo and not textos.parece_codigo(do_arquivo):
                novo = do_arquivo
            if not novo:
                novo = textos.titulo_do_texto(pags)
            if novo and novo != titulo:
                with con:
                    con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                    con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                    _titulo_mudou(con, doc_id, novo)
                n += 1
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('versao_titulos', '3')")
    if not m or int(m[0]) < 4:
        # títulos-lixo: e-mail, caminho de pasta, 'Microsoft Word - ...', símbolos, instruções de software
        lixo = re.compile(r'\w@\w+\.\w|:\\|microsoft (word|powerpoint)|¢|utilize as teclas|\.(doc|pdf|cdr)\b', re.I)
        for doc_id, titulo, caminho in con.execute("SELECT id, titulo, caminho FROM docs WHERE situacao = 'ok'").fetchall():
            if not titulo or not lixo.search(titulo):
                continue
            onde, args = _faixa(con, doc_id)
            pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2, 3, 4) '
                                              'ORDER BY pagina', args)]
            do_arquivo = textos.titulo_de_arquivo(caminho.rsplit('/', 1)[-1])
            novo = (textos.titulo_scielo(pags) or textos.titulo_pela_capa(pags, do_arquivo)
                    or textos.titulo_do_texto(pags) or ('' if textos.parece_codigo(do_arquivo) else do_arquivo))
            if novo and novo != titulo and not lixo.search(novo):
                with con:
                    con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                    con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                    _titulo_mudou(con, doc_id, novo)
                n += 1
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('versao_titulos', '4')")
    if not m or int(m[0]) < 5:
        # capítulos da Hesperian em inglês ('Chapter') e capas que começam por autoridades
        ruim = re.compile(r'^\W*c\s?h\s?apter\b|^\W*cap[ií]tulo\b|presidente da rep|^\W*\d+\W*(—|$)', re.I)
        titulos_idx = _titulos_indice()
        for doc_id, titulo, caminho in con.execute("SELECT id, titulo, caminho FROM docs WHERE situacao = 'ok'").fetchall():
            novo = titulos_idx.get(os.path.normcase(config.absoluto(caminho)), '')
            if '?' in novo or textos.titulo_generico(novo):
                novo = ''  # catálogo do Exército com '?' (já reparado antes) ou 'Apresentação'
            if not novo:
                if not titulo or not ruim.search(titulo):
                    continue
                onde, args = _faixa(con, doc_id)
                pags = [x[0] for x in con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina IN (1, 2, 3) '
                                                  'ORDER BY pagina', args)]
                do_texto = textos.juntar_capitulares(textos.titulo_do_texto(pags))
                # frase solta do meio do texto não serve de título
                if not do_texto or len(do_texto) > 70 or do_texto[0].islower() or re.match(r'in this chapter', do_texto, re.I):
                    continue
                sufixo = titulo.split(' — ', 1)[1] if ' — ' in titulo else ''
                novo = do_texto + (' — ' + sufixo if sufixo else '')
            if novo != titulo:
                onde, args = _faixa(con, doc_id)
                with con:
                    con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (novo, doc_id))
                    con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [novo] + args)
                    _titulo_mudou(con, doc_id, novo)
                n += 1
        with con:
            con.execute("INSERT OR REPLACE INTO meta VALUES ('versao_titulos', '5')")
    if n:
        log.info('títulos corrigidos: %d', n)


def _gravar(con, item, r, titulos):
    p, rel, tam, mt = item
    ext = os.path.splitext(p)[1].lower()
    nome = os.path.basename(p)
    paginas = (r.get('paginas') or []) if r.get('ok') else []
    texto_total = sum(len(x) for x in paginas)
    if r.get('ok') and not r.get('protegido') and texto_total < 2000 and ext == '.pdf':
        lidas = _ocr_de(con, rel, tam)
        if lidas and len(lidas) >= len(paginas) * 0.9:
            paginas = [lidas.get(i + 1, '') for i in range(max(len(paginas), max(lidas)))]
            texto_total = sum(len(x) for x in paginas)
            r = dict(r, ocr=True)
    titulo = titulos.get(os.path.normcase(p)) or ''
    if '?' in titulo:
        titulo = textos.reparar_interrogacoes(titulo, paginas)
    titulo = titulo or textos.titulo_bom(r.get('titulo', ''))
    if not titulo or 'scielo' in titulo.lower():
        titulo = textos.titulo_scielo(paginas) or ('' if titulo and 'scielo' in titulo.lower() else titulo)
    if not titulo:
        titulo = textos.titulo_de_arquivo(nome)
        if textos.parece_codigo(titulo):
            do_texto = textos.titulo_do_texto(paginas)
            if do_texto:
                pasta = os.path.basename(os.path.dirname(p))
                tema_pasta = rel.split('/')[1] if rel.count('/') >= 2 else ''
                titulo = do_texto + (' — ' + pasta.replace('_', ' ') if pasta and pasta != tema_pasta else '')
        else:
            titulo = textos.titulo_pela_capa(paginas, titulo) or titulo
    amostra = ' '.join(paginas[:15])[:20000]
    idioma = textos.normalizar_idioma(r.get('idioma')) or textos.idioma(amostra) or textos.idioma(titulo) or ''
    if not r.get('ok'):
        situacao, erro = 'erro', r.get('erro', '')
        progresso['erros'] += 1
    elif r.get('protegido'):
        situacao, erro = 'protegido', 'PDF protegido: texto não pode ser lido'
    elif texto_total < 30 * max(1, len(paginas)) * 0.3 and texto_total < 2000:
        situacao, erro = 'sem_texto', 'sem texto pesquisável (provavelmente escaneado)'
    else:
        situacao, erro = 'ok', ('texto lido por OCR' if r.get('ocr') else '')
    tipo = 'html' if ext == '.htm' else ext.lstrip('.')
    tema = tema_de(rel)
    with con:
        velho = con.execute('SELECT id FROM docs WHERE caminho = ?', (rel,)).fetchone()
        if velho:
            onde, args = _faixa(con, velho[0])
            con.execute('DELETE FROM pag WHERE ' + onde, args)
            con.execute('DELETE FROM titulos WHERE doc_id = ?', (velho[0],))
            con.execute('DELETE FROM docs WHERE id = ?', (velho[0],))
        cur = con.execute('INSERT INTO docs (caminho, tamanho, mtime, titulo, tema, idioma, tipo, paginas, '
                          'situacao, erro, indexado) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                          (rel, tam, mt, titulo, tema, idioma, tipo, len(paginas), situacao, erro, time.time()))
        doc_id = cur.lastrowid
        nome_busca = textos.titulo_de_arquivo(nome)
        tit_busca = titulo if nome_busca.lower() in titulo.lower() else titulo + ' — ' + nome_busca
        ids = []
        if situacao == 'ok':
            for i, t in enumerate(paginas):
                if t.strip():
                    ids.append(con.execute('INSERT INTO pag (titulo, texto, doc_id, pagina) VALUES (?,?,?,?)',
                                           (tit_busca, t, doc_id, i + 1)).lastrowid)
        else:
            # ainda achável pelo nome / título
            ids.append(con.execute('INSERT INTO pag (titulo, texto, doc_id, pagina) VALUES (?,?,?,?)',
                                   (tit_busca, tema, doc_id, 0)).lastrowid)
        if ids:
            con.execute('UPDATE docs SET pag_ini = ?, pag_fim = ? WHERE id = ?', (min(ids), max(ids), doc_id))
        con.execute('INSERT INTO titulos (titulo, doc_id) VALUES (?, ?)', (titulo, doc_id))
    progresso['novos'] += 1


# ------------------------------------------------------------------ OCR (PDFs escaneados)

def _ocr_de(con, rel, tam):
    """Páginas já lidas por OCR deste arquivo (só valem se o arquivo não mudou de tamanho)."""
    try:
        return {pg: tx for pg, tx in con.execute('SELECT pagina, texto FROM ocr WHERE caminho = ? AND tamanho = ? '
                                                  'AND pagina > 0', (rel, tam))}
    except sqlite3.Error:
        return {}


def salvar_ocr(con, rel, tam, pagina, texto):
    with con:
        con.execute('INSERT OR REPLACE INTO ocr VALUES (?, ?, ?, ?)', (rel, tam, pagina, texto or ''))


def ocr_feito(con, rel, tam, pagina):
    r = con.execute('SELECT texto FROM ocr WHERE caminho = ? AND tamanho = ? AND pagina = ?', (rel, tam, pagina)).fetchone()
    return r[0] if r else None


def aplicar_ocr(con, doc_id, titulos=None):
    """Regrava um documento escaneado com o texto lido por OCR (fica pesquisável). Devolve o novo id."""
    d = con.execute('SELECT caminho, tamanho, mtime, paginas, titulo FROM docs WHERE id = ?', (doc_id,)).fetchone()
    if not d:
        return None
    rel, tam, mt, n, titulo = d
    # o título que já existe (catálogo ou nome do arquivo) costuma ser melhor que o texto lido da capa
    bom = titulo if titulo and not textos.parece_codigo(titulo) and not textos.titulo_generico(titulo) else ''

    lidas = _ocr_de(con, rel, tam)
    if not lidas:
        return doc_id
    paginas = [lidas.get(i + 1, '') for i in range(max(n or 0, max(lidas)))]
    _gravar(con, (config.absoluto(rel), rel, tam, mt), {'ok': True, 'paginas': paginas, 'titulo': bom, 'ocr': True},
            titulos if titulos is not None else _titulos_indice())
    novo = con.execute('SELECT id, titulo FROM docs WHERE caminho = ?', (rel,)).fetchone()
    if novo and bom and novo[1] != bom:
        onde, args = _faixa(con, novo[0])
        with con:
            con.execute('UPDATE docs SET titulo = ? WHERE id = ?', (bom, novo[0]))
            con.execute('UPDATE pag SET titulo = ? WHERE ' + onde, [bom] + args)
            _titulo_mudou(con, novo[0], bom)
    if novo and novo[0] != doc_id:
        # mantém o número do documento (links abertos, como a tela de OCR, continuam valendo)
        onde, args = _faixa(con, novo[0])
        with con:
            con.execute('UPDATE pag SET doc_id = ? WHERE ' + onde, [doc_id] + args)
            con.execute('UPDATE titulos SET doc_id = ? WHERE doc_id = ?', (doc_id, novo[0]))
            con.execute('UPDATE docs SET id = ? WHERE id = ?', (doc_id, novo[0]))
        return doc_id
    return novo[0] if novo else None


# ------------------------------------------------------------------ consultas

def url_documento(caminho, tipo, doc_id, pagina, consulta=''):
    if tipo == 'pdf':
        u = '/arquivo/' + quote(caminho)
        return u + ('#page=%d' % pagina if pagina and pagina > 1 else '')
    u = '/ler/%d?p=%d' % (doc_id, max(1, pagina or 1))
    if consulta:
        u += '&q=' + quote(consulta)
    return u


def _marcar(trecho):
    from html import escape
    t = textos.limpar_controle((trecho or '').replace('\x02', '⁠[').replace('\x03', ']⁠'))
    t = escape(t).replace('⁠[', '<mark>').replace(']⁠', '</mark>')
    return re.sub(r'\s+', ' ', t).replace('. . . ', '').replace('....', '…')


def _consultar(con, sql, args, limite, rotulo='', tempo=4):
    """Consulta ordenada por relevância; se passar do tempo (termo muito comum em 1 milhão de
    páginas), pega as primeiras ocorrências e ordena só elas: nunca volta vazio por lentidão."""
    try:
        with tempo_maximo(tempo):
            return con.execute(sql + ' ORDER BY nota LIMIT %d' % limite, args).fetchall()
    except sqlite3.OperationalError as e:
        if 'interrupt' not in str(e).lower():
            log.info('consulta inválida %r: %s', rotulo, e)
            return None
    try:
        with tempo_maximo(3):
            linhas = con.execute(sql + ' LIMIT %d' % (limite * 2), args).fetchall()
        return sorted(linhas, key=lambda r: r['nota'])[:limite]
    except sqlite3.OperationalError:
        return []


def buscar(consulta, tema=None, idioma=None, limite_docs=25, consulta_en=None):
    """Resultados agrupados por documento, cada um com até 3 páginas."""
    con = conexao()
    grupos = {}
    gemeos = {}
    ordem = []

    def rodar(q_fts, idioma_filtro, peso):
        if not q_fts:
            return
        sql = ('SELECT pag.doc_id, pag.pagina, snippet(pag, 1, char(2), char(3), " … ", 30) AS trecho, '
               'bm25(pag, 6.0, 1.0) AS nota, docs.titulo, docs.tema, docs.idioma, docs.tipo, docs.caminho, '
               'docs.paginas, docs.situacao FROM pag JOIN docs ON docs.id = pag.doc_id WHERE pag MATCH ?')
        args = [q_fts]
        if tema:
            sql += ' AND docs.tema = ?'
            args.append(tema)
        if idioma_filtro:
            sql += ' AND docs.idioma = ?'
            args.append(idioma_filtro)
        # se os títulos já trouxeram bons documentos, não vale esperar a ordenação completa
        linhas = _consultar(con, sql, args, 300, q_fts, tempo=1.5 if len(ordem) >= 8 else 4)
        if linhas is None:
            return
        for r in linhas:
            g = grupos.get(r['doc_id'])
            if g is None:
                # mesmo documento baixado duas vezes (mesmo título e nº de páginas): mostra um só
                gemeo = gemeos.get(r['titulo'])
                if gemeo is not None:
                    grupos[r['doc_id']] = gemeo
                    continue
                if len(ordem) >= limite_docs:
                    continue
                g = {'doc_id': r['doc_id'], 'titulo': r['titulo'], 'tema': r['tema'], 'idioma': r['idioma'],
                     'tipo': r['tipo'], 'caminho': r['caminho'], 'total_paginas': r['paginas'],
                     'situacao': r['situacao'], 'nota': r['nota'] * peso, 'paginas': [],
                     'url': url_documento(r['caminho'], r['tipo'], r['doc_id'], 1, consulta)}
                grupos[r['doc_id']] = g
                gemeos[r['titulo']] = g
                ordem.append(g)
            elif g['doc_id'] != r['doc_id']:
                continue
            if len(g['paginas']) < 3 and all(x['pagina'] != r['pagina'] for x in g['paginas']):
                g['paginas'].append({'pagina': r['pagina'], 'trecho': _marcar(r['trecho']),
                                     'url': url_documento(r['caminho'], r['tipo'], r['doc_id'], r['pagina'],
                                                          consulta)})

    def por_titulo(q_fts, idioma_filtro):
        """Documentos cujo título tem todos os termos: índice pequeno, instantâneo; vêm primeiro."""
        if not q_fts:
            return
        sql = ('SELECT titulos.doc_id, bm25(titulos) AS n FROM titulos JOIN docs ON docs.id = titulos.doc_id '
               'WHERE titulos MATCH ?')
        args = [q_fts]
        if tema:
            sql += ' AND docs.tema = ?'
            args.append(tema)
        if idioma_filtro:
            sql += ' AND docs.idioma = ?'
            args.append(idioma_filtro)
        try:
            with tempo_maximo(2):
                achados = con.execute(sql + ' ORDER BY n LIMIT 12', args).fetchall()
        except sqlite3.OperationalError:
            return
        for doc_id, n in achados:
            d = con.execute('SELECT * FROM docs WHERE id = ?', (doc_id,)).fetchone()
            if d is None or doc_id in grupos or d['titulo'] in gemeos:
                continue
            g = {'doc_id': doc_id, 'titulo': d['titulo'], 'tema': d['tema'], 'idioma': d['idioma'], 'tipo': d['tipo'],
                 'caminho': d['caminho'], 'total_paginas': d['paginas'], 'situacao': d['situacao'],
                 'nota': -50.0 + n, 'paginas': [], 'url': url_documento(d['caminho'], d['tipo'], doc_id, 1, consulta)}
            # melhores páginas do próprio documento (busca restrita à faixa dele: rápida)
            if d['pag_ini'] is not None:
                try:
                    with tempo_maximo(1):
                        pags = con.execute(
                            'SELECT pagina, snippet(pag, 1, char(2), char(3), " … ", 30) FROM pag '
                            'WHERE pag MATCH ? AND rowid BETWEEN ? AND ? AND pagina > 0 ORDER BY bm25(pag) LIMIT 3',
                            (textos.consulta_fts(consulta, 'or') or q_fts, d['pag_ini'], d['pag_fim'])).fetchall()
                except sqlite3.OperationalError:
                    pags = []
                for pg, tr in pags:
                    g['paginas'].append({'pagina': pg, 'trecho': _marcar(tr),
                                         'url': url_documento(d['caminho'], d['tipo'], doc_id, pg, consulta)})
            grupos[doc_id] = g
            gemeos[d['titulo']] = g
            ordem.append(g)

    por_titulo(textos.consulta_fts(consulta, 'and'), idioma)
    # bm25 é negativo (mais negativo = melhor); peso > 1 favorece, < 1 rebaixa
    rodar(textos.consulta_fts(consulta, 'perto'), idioma, 2.0)
    rodar(textos.consulta_fts(consulta, 'and'), idioma, 1.0)
    if len(ordem) < 8:
        rodar(textos.consulta_fts(sem_comuns(consulta) or consulta, 'or'), idioma, 0.4)
    if consulta_en and idioma in (None, '', 'en'):
        rodar(textos.consulta_fts(consulta_en, 'perto'), 'en', 1.8)
        rodar(textos.consulta_fts(consulta_en, 'and'), 'en', 0.9)
        if len(ordem) < 12:
            rodar(textos.consulta_fts(consulta_en, 'or'), 'en', 0.35)
    # documento cujo TÍTULO tem os termos da busca sobe (com milhares de PDFs, o texto de páginas
    # avulsas que só citam os termos não deve passar na frente do manual sobre o assunto)
    raizes = [textos.raiz(t) for t in textos.termos(consulta)]
    if raizes:
        for g in ordem:
            tit = textos.sem_acento((g['titulo'] or '').lower())
            cobertura = sum(1 for r in raizes if r in tit) / len(raizes)
            g['nota'] *= (1 + 2.0 * cobertura)
    ordem.sort(key=lambda g: g['nota'])  # bm25: menor é melhor
    return ordem


def trechos_para_ia(consulta, n=6, idioma=None, tamanho=1400):
    """Melhores páginas, com a janela de texto mais relevante de cada uma (para o RAG)."""
    con = conexao()
    saida = []
    vistos = set()
    por_doc = {}
    # 1º) documentos cujo TÍTULO trata do assunto ("Queimaduras", "Manual de ..."): a melhor página
    # de cada um. Evita que a IA receba páginas avulsas que só citam a palavra de passagem.
    raros = sem_comuns(consulta) or consulta
    q_tit = textos.consulta_fts(raros, 'or', tirar_negados=True)
    q_pag = textos.consulta_fts(consulta, 'or', tirar_negados=True)
    if q_tit and q_pag:
        sql = ('SELECT titulos.doc_id FROM titulos JOIN docs ON docs.id = titulos.doc_id '
               'WHERE titulos MATCH ? AND docs.situacao = \'ok\'')
        args = [q_tit]
        if idioma:
            sql += ' AND docs.idioma = ?'
            args.append(idioma)
        try:
            with tempo_maximo(2):
                cand = con.execute(sql + ' ORDER BY bm25(titulos) LIMIT 20', args).fetchall()
        except sqlite3.OperationalError:
            cand = []
        # o título precisa cobrir a maior parte do "peso" da pergunta (palavras raras valem mais):
        # 'Pega leve' não serve para 'queimadura leve'
        ts = textos.termos(raros, tirar_negados=True)
        pesos = peso_termos(ts)
        total = sum(pesos.values()) or 1
        docs_tit = []
        for (doc_id,) in cand:
            t = con.execute('SELECT titulo FROM docs WHERE id = ?', (doc_id,)).fetchone()
            tit = textos.sem_acento((t[0] if t else '').lower())
            cobre = sum(p for termo, p in pesos.items() if textos.raiz(termo) in tit)
            if cobre / total >= 0.6:
                docs_tit.append(doc_id)
            if len(docs_tit) >= 4:
                break
        for doc_id in docs_tit:
            d = con.execute('SELECT * FROM docs WHERE id = ?', (doc_id,)).fetchone()
            if d is None or d['pag_ini'] is None or por_doc.get(d['titulo'], 0):
                continue
            try:
                with tempo_maximo(1):
                    r = con.execute('SELECT pagina, texto FROM pag WHERE pag MATCH ? AND rowid BETWEEN ? AND ? '
                                    'AND pagina > 0 ORDER BY bm25(pag) LIMIT 1',
                                    (q_pag, d['pag_ini'], d['pag_fim'])).fetchone()
            except sqlite3.OperationalError:
                r = None
            if not r:
                continue
            vistos.add((d['titulo'], r['pagina']))
            por_doc[d['titulo']] = 1
            saida.append({'fonte': 'doc', 'titulo': d['titulo'], 'tema': d['tema'], 'idioma': d['idioma'],
                          'pagina': r['pagina'], 'texto': textos.trecho_relevante(r['texto'], consulta, tamanho),
                          'url': url_documento(d['caminho'], d['tipo'], doc_id, r['pagina'], consulta)})
            if len(saida) >= min(3, n):
                break
    for modo in ('perto', 'and', 'or'):
        base = sem_comuns(consulta) if modo == 'or' else consulta
        q = textos.consulta_fts(base, modo, tirar_negados=True) if base else ''
        if not q:
            continue
        sql = ('SELECT pag.doc_id, pag.pagina, pag.texto, docs.titulo, docs.tema, docs.idioma, docs.tipo, '
               'docs.caminho FROM pag JOIN docs ON docs.id = pag.doc_id WHERE pag MATCH ? AND pag.pagina > 0')
        args = [q]
        if idioma:
            sql += ' AND docs.idioma = ?'
            args.append(idioma)
        sql = sql.replace('SELECT pag.doc_id,', 'SELECT bm25(pag, 6.0, 1.0) AS nota, pag.doc_id,', 1)
        linhas = _consultar(con, sql, args, 40, q)
        if not linhas:
            continue
        for r in linhas:
            chave = (r['titulo'], r['pagina'])  # por título: ignora o mesmo PDF baixado 2x
            if chave in vistos or por_doc.get(r['titulo'], 0) >= 2:
                continue
            vistos.add(chave)
            por_doc[r['titulo']] = por_doc.get(r['titulo'], 0) + 1
            saida.append({'fonte': 'doc', 'titulo': r['titulo'], 'tema': r['tema'], 'idioma': r['idioma'],
                          'pagina': r['pagina'], 'texto': textos.trecho_relevante(r['texto'], consulta, tamanho),
                          'url': url_documento(r['caminho'], r['tipo'], r['doc_id'], r['pagina'], consulta)})
            if len(saida) >= n:
                return saida
        if len(saida) >= n:
            break
    return saida


_cache_df = {}
_cache_total = [0, 0.0]
DF_TETO = 20000   # acima disso o termo é "comum": não precisa contar exato


def total_paginas():
    """Nº de páginas no índice (da tabela docs: instantâneo; COUNT(*) no FTS varre tudo)."""
    if time.time() - _cache_total[1] > 600:
        try:
            _cache_total[0] = conexao().execute('SELECT COALESCE(SUM(paginas), 0) + COUNT(*) FROM docs').fetchone()[0] or 1
        except sqlite3.Error:
            _cache_total[0] = _cache_total[0] or 1
        _cache_total[1] = time.time()
    return _cache_total[0]


def freq_termo(t):
    """Em quantas páginas o termo aparece (com teto e cache)."""
    q = textos.consulta_fts(t, 'and')
    if not q:
        return 0
    if q not in _cache_df:
        try:
            with tempo_maximo(4):
                _cache_df[q] = conexao().execute(
                    'SELECT COUNT(*) FROM (SELECT 1 FROM pag WHERE pag MATCH ? LIMIT %d)' % (DF_TETO + 1), (q,)).fetchone()[0]
        except sqlite3.Error:
            return DF_TETO
    return _cache_df[q]


def peso_termos(ts):
    """IDF de cada termo (termos raros valem mais), pelo nº de páginas que o contêm."""
    import math
    total = total_paginas()
    return {t: max(0.3, math.log((total + 1) / (freq_termo(t) + 1))) for t in ts}


def sem_comuns(consulta):
    """Tira da consulta 'OU' as palavras muito comuns ('casa', 'fazer'...): pontuar centenas de
    milhares de páginas trava a busca e quase nunca ajuda."""
    ts = textos.termos(consulta, tirar_negados=True)
    raros = [t for t in ts if freq_termo(t) < DF_TETO]
    return ' '.join(raros) if raros else ''


class tempo_maximo:
    """Interrompe consultas SQLite que passarem do limite (a busca nunca trava o app)."""

    def __init__(self, segundos):
        self.limite = segundos

    def __enter__(self):
        fim = time.time() + self.limite
        conexao().set_progress_handler(lambda: 1 if time.time() > fim else 0, 20000)
        return self

    def __exit__(self, *a):
        conexao().set_progress_handler(None, 0)
        return False


def documento(doc_id):
    r = conexao().execute('SELECT * FROM docs WHERE id = ?', (doc_id,)).fetchone()
    return dict(r) if r else None


def texto_pagina(doc_id, pagina):
    con = conexao()
    onde, args = _faixa(con, doc_id)
    r = con.execute('SELECT texto FROM pag WHERE ' + onde + ' AND pagina = ?', args + [pagina]).fetchone()
    return r[0] if r else ''


def sumario_documento(doc_id):
    """Título de cada seção (a primeira linha do texto), para o sumário do leitor."""
    con = conexao()
    onde, args = _faixa(con, doc_id)
    saida = []
    for pg, ini in con.execute('SELECT pagina, substr(texto, 1, 160) FROM pag WHERE ' + onde + ' AND pagina > 0 '
                               'ORDER BY pagina', args):
        primeira = next((l.strip() for l in (ini or '').splitlines() if l.strip()), '')
        saida.append({'p': pg, 't': textos.limpar_controle(primeira)[:90]})
    return saida


def buscar_no_documento(doc_id, consulta, limite=80):
    """Busca só dentro de um documento (a 'lupa' do leitor). Frase próxima primeiro; depois todas as
    palavras; por fim qualquer uma."""
    con = conexao()
    onde, args = _faixa(con, doc_id)
    titulos = {s['p']: s['t'] for s in sumario_documento(doc_id)}
    for modo in ('perto', 'and', 'or'):
        q = textos.consulta_fts(consulta, modo)
        if not q:
            continue
        try:
            with tempo_maximo(4):
                linhas = con.execute('SELECT pagina, snippet(pag, 1, char(2), char(3), " … ", 18) AS trecho FROM pag '
                                     'WHERE pag MATCH ? AND ' + onde + ' AND pagina > 0 ORDER BY rank LIMIT ?',
                                     ['texto : (%s)' % q] + args + [limite]).fetchall()
        except sqlite3.OperationalError:
            linhas = []
        if linhas:
            if modo != 'or' and len(linhas) > 1:
                linhas = _ordenar_por_proximidade(con, onde, args, linhas, consulta)
            return {'modo': modo, 'resultados': [{'p': r[0], 't': titulos.get(r[0], ''), 'trecho': _marcar(r[1])}
                                                 for r in linhas]}
    return {'modo': '', 'resultados': []}


def _ordenar_por_proximidade(con, onde, args, linhas, consulta):
    """Põe primeiro a seção em que as palavras procuradas aparecem mais juntas ('o senhor é o meu
    pastor' -> Salmo 23 antes de um capítulo que só cita 'senhor' e 'pastores' longe um do outro)."""
    ts = [textos.raiz(t) for t in textos.termos(consulta, True)]
    if len(ts) < 2:
        return linhas
    paginas = [r[0] for r in linhas]
    textos_pg = dict(con.execute('SELECT pagina, texto FROM pag WHERE ' + onde + ' AND pagina IN (%s)'
                                 % ','.join('?' * len(paginas)), args + paginas).fetchall())

    def janela(pg):
        palavras = re.findall(r'\w+', textos.sem_acento((textos_pg.get(pg) or '').lower()))
        ultimo, melhor = {}, 10 ** 9
        for i, w in enumerate(palavras):
            for t in ts:
                if w.startswith(t):
                    ultimo[t] = i
            if len(ultimo) == len(ts):
                melhor = min(melhor, i - min(ultimo.values()))
        return melhor

    pos = {r[0]: i for i, r in enumerate(linhas)}
    return sorted(linhas, key=lambda r: (janela(r[0]), pos[r[0]]))


def estante():
    """Só os temas e contagens (a lista de documentos de cada tema vem sob demanda)."""
    con = conexao()
    return [{'tema': t['tema'], 'n': t['n']}
            for t in con.execute('SELECT tema, COUNT(*) n FROM docs GROUP BY tema ORDER BY tema')]


def estante_tema(tema):
    """Documentos de um tema, agrupados por subpasta."""
    con = conexao()
    pastas = {}
    for r in con.execute('SELECT id, titulo, tipo, idioma, paginas, situacao, caminho FROM docs WHERE tema = ? '
                         'ORDER BY titulo COLLATE NOCASE', (tema,)):
        d = dict(r)
        d['url'] = url_documento(d['caminho'], d['tipo'], d['id'], 1)
        pasta = '/'.join(d['caminho'].split('/')[2:-1]).replace('_', ' ')
        d.pop('caminho', None)
        pastas.setdefault(pasta, []).append(d)
    return [{'pasta': p, 'docs': docs} for p, docs in sorted(pastas.items())]


def temas():
    return [r[0] for r in conexao().execute('SELECT DISTINCT tema FROM docs ORDER BY tema')]


def resumo():
    con = conexao()
    r = {'documentos': 0, 'paginas': 0, 'sem_texto': 0, 'protegidos': 0, 'erros': 0, 'ocr': 0}
    try:
        r['documentos'] = con.execute('SELECT COUNT(*) FROM docs').fetchone()[0]
        r['paginas'] = con.execute('SELECT COALESCE(SUM(paginas),0) FROM docs WHERE situacao="ok"').fetchone()[0]
        for sit, chave in (('sem_texto', 'sem_texto'), ('protegido', 'protegidos'), ('erro', 'erros')):
            r[chave] = con.execute('SELECT COUNT(*) FROM docs WHERE situacao = ?', (sit,)).fetchone()[0]
        r['ocr'] = con.execute("SELECT COUNT(*) FROM docs WHERE erro = 'texto lido por OCR'").fetchone()[0]
        m = con.execute("SELECT valor FROM meta WHERE chave='ultima_atualizacao'").fetchone()
        r['ultima_atualizacao'] = float(m[0]) if m else None
    except sqlite3.Error:
        pass
    return r
