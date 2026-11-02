"""kiwix-serve: iniciar, catálogo dos ZIMs, busca em todos e texto de artigos (para a IA)."""
import concurrent.futures as cf
import http.client
import json
import os
import re
import threading
import time
from html import escape, unescape
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

from . import config, sistema, textos

log = config.log
RAIZ_URL = '/kiwix'

_estado = {'processo': None, 'porta': None, 'zims': [], 'livros': [], 'erro': '', 'pronto': False,
           'exe': None, 'iniciando': False, 'arquivos_ruins': []}
_lock = threading.Lock()
_pool = cf.ThreadPoolExecutor(max_workers=16, thread_name_prefix='kiwix')

NOMES_PT = {
    'wikipedia_pt_all': 'Wikipédia (português)', 'wikipedia_en_all': 'Wikipédia (inglês)',
    'wikipedia_en_medicine': 'WikiMed — medicina (inglês)', 'wikipedia_en_knots': 'Nós e amarras (Wikipédia, inglês)',
    'wikibooks_pt_all': 'Wikilivros', 'wikivoyage_pt_all': 'Wikivoyage (viagem)', 'wikisource_pt_all': 'Wikisource',
    'wikiversity_pt_all': 'Wikiversidade', 'wiktionary_pt_all': 'Wikcionário', 'appropedia_en_all': 'Appropedia (tecnologia apropriada)',
    'ifixit_en_all': 'iFixit (consertos)', 'mdwiki_en_all': 'MDWiki (medicina)', 'wikem_en_all': 'WikEM (emergência médica)',
    'ready.gov_en_all': 'Ready.gov (preparação para desastres)', 'irp.fas.org_en_military-medicine': 'Medicina militar (EUA)',
    'zimgit-water_en': 'Água — seleção preppers', 'zimgit-food-preparation_en': 'Alimentos — seleção preppers',
    'diy.stackexchange.com_en_all': 'Reformas e manutenção (Stack Exchange)',
    'mechanics.stackexchange.com_en_all': 'Mecânica de veículos (Stack Exchange)',
    'ham.stackexchange.com_en_all': 'Radioamador (Stack Exchange)',
    'woodworking.stackexchange.com_en_all': 'Marcenaria (Stack Exchange)',
    'electronics.stackexchange.com_en_all': 'Elétrica e eletrônica (Stack Exchange)',
    'devdocs_en_homebrew': 'Homebrew (documentação)', 'ted_mul_gardening': 'TED — jardinagem e horta (vídeos)',
    'usda-2015_en': 'Conservas caseiras — guia USDA', 'zimgit-medicine_en': 'Biblioteca médica (preppers)',
    'zimgit-knots_en': 'Biblioteca de nós', 'wikiciv_en_all': 'WikiCiv (reconstruir tecnologias básicas)',
    'nhs.uk_en_medicines': 'NHS — remédios de A a Z', 'cooking.stackexchange.com_en_all': 'Cozinha (Stack Exchange)',
    'cd3wdproject.org_en_all': 'CD3WD (tecnologia apropriada)', 'zimgit-post-disaster_en': 'Biblioteca pós-desastre',
    'energypedia_en_all': 'Energypedia (energia solar, biogás…)',
    'gardening.stackexchange.com_en_all': 'Horta e jardinagem (Stack Exchange)',
    'survivorlibrary.com_en_all': 'Survivor Library (livros antigos de ofícios)',
    'khanacademy_en_all': 'Khan Academy (aulas de todas as matérias, inglês)',
    'crashcourse_en_all': 'CrashCourse (videoaulas)', 'apod.nasa.gov_en_all': 'Astronomia — foto do dia da NASA',
    'medlineplus.gov_en_all': 'MedlinePlus (saúde, remédios e exames)', 'wwwnc.cdc.gov_en_all': 'CDC — saúde do viajante',
    'librepathology_en_all': 'Libre Pathology', 'wikivet.net_en_all': 'WikiVet (veterinária)',
    'quickguidesformedicine_en_all': 'Guias rápidos de medicina', 'genomicseducation_en_all': 'Genética e genômica',
    'skin-of-color-society_en_all': 'Dermatologia em peles negras e pardas',
    'wikibooks_en_all': 'Wikibooks (inglês)', 'wikiversity_en_all': 'Wikiversity (inglês)',
    'wikipedia_en-simple_all': 'Wikipédia em inglês simples', 'wikivoyage_en_all': 'Wikivoyage (inglês)',
    'wikiquote_en_all': 'Wikiquote (inglês)', 'wikiquote_pt_all': 'Wikiquote (português)',
    'wiktionary_en_all': 'Wiktionary (dicionário inglês)', 'wiktionary_pt_all': 'Wikcionário (dicionário)',
    'wikispecies_en_all': 'Wikispecies (seres vivos)', 'wikisource_en_all': 'Wikisource (inglês)',
    'vikidia_pt_all': 'Vikidia (enciclopédia para jovens)', 'vikidia_en_all': 'Vikidia (inglês)',
    'phet_pt_all': 'Simulações PhET (física, química, biologia)', 'phet_en_all': 'Simulações PhET (inglês)',
    'ifixit_pt_all': 'iFixit em português (consertos)', 'gutenberg_pt_all': 'Projeto Gutenberg (livros em português)',
    'pt.stackoverflow.com_mul_all': 'Stack Overflow em português', 'theworldfactbook_en_all': 'CIA World Factbook (países)',
    'internet-encyclopedia-philosophy_en_all': 'Enciclopédia de Filosofia',
    'encyclopedie-environnement.org_en_all': 'Enciclopédia do Meio Ambiente', 'citizendium.org_en_all': 'Citizendium',
    'armypubs_en_all': 'Manuais do Exército dos EUA', 'trueprepper.com_en_all': 'TruePrepper (preparação)',
    'lrnselfreliance_en_all': 'Autossuficiência (vídeos)', 'maps_en_south-america': 'Mapa da América do Sul',
    'stackoverflow.com_en_all': 'Stack Overflow (programação)',
}


# Aparecem na busca, mas não alimentam a IA: literatura, viagem, vídeos, mapas, programação,
# dicionários e citações (raramente têm a resposta prática de uma pergunta).
SECUNDARIOS = ('wikisource', 'wikivoyage', 'devdocs', 'ted_', 'wiktionary', 'wikiquote', 'gutenberg_',
               'khanacademy', 'crashcourse', 'apod.nasa', 'maps_', 'stackoverflow', 'pt.stackoverflow',
               'superuser', 'askubuntu', 'serverfault', 'unix.stackexchange', 'retrocomputing', 'lrnselfreliance',
               'phet_', 'vikidia', 'wikispecies', 'portuguese.stackexchange', 'english.stackexchange',
               'ell.stackexchange', 'linguistics.stackexchange')

_LIBRETEXTS = {'bio': 'Biologia', 'chem': 'Química', 'phys': 'Física', 'med': 'Medicina', 'geo': 'Geociências',
               'eng': 'Engenharia', 'math': 'Matemática', 'stats': 'Estatística', 'workforce': 'Ofícios técnicos',
               'k12': 'Ensino fundamental e médio', 'socialsci': 'Ciências sociais', 'biz': 'Administração',
               'human': 'Humanidades'}
_GUTENBERG = {'a': 'Obras gerais', 'b': 'Filosofia, psicologia e religião', 'c': 'Ciências auxiliares da história',
              'd': 'História mundial', 'e': 'História da América', 'f': 'História local da América',
              'g': 'Geografia e antropologia', 'h': 'Ciências sociais', 'j': 'Ciência política', 'k': 'Direito',
              'l': 'Educação', 'm': 'Música', 'n': 'Belas-artes', 'p': 'Línguas e literatura', 'q': 'Ciências',
              'r': 'Medicina', 's': 'Agricultura', 't': 'Tecnologia', 'u': 'Ciência militar', 'v': 'Ciência naval',
              'z': 'Bibliografia'}


def nome_pt(nome, titulo):
    if nome in NOMES_PT:
        return NOMES_PT[nome]
    m = re.match(r'libretexts\.org_en_(\w+)$', nome)
    if m and m.group(1) in _LIBRETEXTS:
        return 'LibreTexts — ' + _LIBRETEXTS[m.group(1)]
    m = re.match(r'gutenberg_en_lcc-(\w+)$', nome)
    if m:
        return 'Gutenberg — ' + _GUTENBERG.get(m.group(1), _GUTENBERG.get(m.group(1)[0], m.group(1).upper()))
    if nome.endswith('stackexchange.com_en_all') or nome in ('superuser.com_en_all', 'askubuntu.com_en_all',
                                                              'serverfault.com_en_all', 'mathoverflow.net_en_all'):
        return re.sub(r'\s*Q&A$', '', unescape(titulo)) + ' (Stack Exchange)'
    return unescape(titulo)


def estado():
    p = _estado['processo']
    rodando = p is not None and p.poll() is None
    return {'rodando': rodando and _estado['pronto'], 'iniciando': _estado['iniciando'],
            'porta': _estado['porta'], 'erro': _estado['erro'], 'zims': len(_estado['zims']),
            'livros': len(_estado['livros']), 'arquivos_ruins': _estado['arquivos_ruins'],
            'exe': bool(_estado['exe'])}


def livros():
    return list(_estado['livros'])


def disponivel():
    p = _estado['processo']
    return _estado['pronto'] and p is not None and p.poll() is None


# ------------------------------------------------------------------ iniciar

def achar_exe():
    for padrao in ('Programas/KiwixServe/**/kiwix-serve.exe', 'Programas/**/kiwix-serve.exe'):
        achados = sistema.achar(padrao)
        if achados:
            return achados[0]
    return None


_completos = {}  # (caminho, tamanho, data) -> True: ZIM já conferido não é relido


def listar_zims():
    bons, ruins = [], []
    for z in sistema.achar('1_Enciclopedia/**/*.zim'):
        try:
            st = os.stat(z)
        except OSError:
            continue
        chave = (z, st.st_size, st.st_mtime)
        if chave in _completos or sistema.zim_completo(z):
            _completos[chave] = True
            bons.append(z)
        else:
            ruins.append(os.path.basename(z))
    return bons, ruins


def iniciar(porta_inicial=8801, evitar=(), lista=None):
    """Inicia o kiwix-serve em segundo plano (não bloqueia a abertura do app)."""
    with _lock:
        if _estado['iniciando']:
            return
        _estado['iniciando'] = True
    threading.Thread(target=_iniciar, args=(porta_inicial, evitar, lista), daemon=True, name='kiwix-inicio').start()


def _iniciar(porta_inicial, evitar, lista=None):
    try:
        _iniciar_bloq(porta_inicial, evitar, lista)
    except Exception as e:
        log.exception('erro ao iniciar kiwix')
        _estado['erro'] = 'Não consegui abrir as enciclopédias: %s' % e
    finally:
        _estado['iniciando'] = False


def _iniciar_bloq(porta_inicial, evitar, lista=None):
    _estado['pronto'] = False
    _estado['erro'] = ''
    exe = achar_exe()
    _estado['exe'] = exe
    zims, ruins = lista or listar_zims()
    _estado['zims'] = zims
    _estado['arquivos_ruins'] = ruins
    if ruins:
        log.info('ZIMs incompletos ou inválidos (ignorados): %s', ruins)
    if not exe:
        _estado['erro'] = 'Programa kiwix-serve não encontrado em Programas\\KiwixServe. Rode o _Baixador.'
        return
    if not zims:
        _estado['erro'] = 'Nenhuma enciclopédia (.zim) completa em 1_Enciclopedia.'
        return
    sistema.preparar_runtime_kiwix(exe)
    porta = sistema.porta_livre(porta_inicial, evitar=evitar)
    args = [exe, '-i', '127.0.0.1', '-p', str(porta), '-r', RAIZ_URL, '-k', '-b',
            '-t', str(max(4, min(12, os.cpu_count() or 4))), '-a', str(os.getpid())]
    biblioteca = _library_xml(exe, zims)
    if biblioteca:
        # com o library.xml o kiwix-serve abre cada ZIM só quando é usado: inicia em ~1 s em vez de ~1 min
        args += ['--library', biblioteca]
    else:
        args += [os.path.relpath(z, config.RAIZ) for z in zims]
    log.info('iniciando kiwix-serve na porta %d com %d ZIMs%s', porta, len(zims), ' (library.xml)' if biblioteca else '')
    p = sistema.iniciar_processo(args, cwd=config.RAIZ, log_arquivo=os.path.join(config.DADOS, 'kiwix_log.txt'))
    _estado['processo'] = p
    _estado['porta'] = porta
    # espera abrir (ZIMs grandes num SSD externo levam alguns segundos)
    for _ in range(240):
        if p.poll() is not None:
            break
        try:
            c = http.client.HTTPConnection('127.0.0.1', porta, timeout=2)
            c.request('GET', RAIZ_URL + '/catalog/v2/entries?count=-1')
            r = c.getresponse()
            dados = r.read()
            c.close()
            if r.status == 200:
                _estado['livros'] = _ler_catalogo(dados)
                _estado['pronto'] = True
                log.info('kiwix-serve pronto: %d livros', len(_estado['livros']))
                return
        except OSError:
            pass
        time.sleep(0.5)
    cod = p.poll()
    if cod is not None:
        cod32 = cod & 0xFFFFFFFF
        if cod32 == 0xC0000135:
            _estado['erro'] = ('O kiwix-serve não abriu: falta uma DLL do Windows (Visual C++). '
                               'Instale Programas\\Kiwix\\vc_redist.x64.exe ou confira App\\runtime.')
        elif cod32 == 0xC0000005:
            _estado['erro'] = ('O kiwix-serve travou ao abrir (runtime do Visual C++ antigo). '
                               'Instale Programas\\Kiwix\\vc_redist.x64.exe.')
        else:
            _estado['erro'] = 'O kiwix-serve fechou sozinho (código %s). Veja App\\dados\\kiwix_log.txt.' % cod
    else:
        _estado['erro'] = 'O kiwix-serve não respondeu a tempo.'
    log.warning(_estado['erro'])


def _library_xml(exe, zims):
    """Gera/atualiza App\\dados\\library.xml (caminhos relativos: funciona com qualquer letra de drive).
    Retorna o caminho, ou None se não der (SSD somente leitura, kiwix-manage ausente...)."""
    if config.estado['somente_leitura']:
        return None
    manage = os.path.join(os.path.dirname(exe), 'kiwix-manage.exe')
    if not os.path.exists(manage):
        return None
    lib = os.path.join(config.DADOS, 'library.xml')
    lista = os.path.join(config.DADOS, 'library.lista')
    assinatura = '\n'.join('%s|%d' % (os.path.relpath(z, config.RAIZ), os.path.getsize(z)) for z in zims)
    antiga = ''
    try:
        antiga = open(lista, encoding='utf-8').read() if os.path.exists(lib) else ''
        if antiga == assinatura:
            return lib
    except OSError:
        pass
    tmp = lib + '.novo'
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
        # só chegaram ZIMs novos? adiciona apenas eles (abrir 150+ ZIMs a frio leva minutos)
        velhos = set(antiga.split('\n')) if antiga else set()
        novos_itens = assinatura.split('\n')
        if velhos and velhos <= set(novos_itens):
            import shutil
            shutil.copy2(lib, tmp)
            alvo = [os.path.join(config.RAIZ, x.split('|')[0]) for x in novos_itens if x not in velhos]
        else:
            alvo = zims
        r = sistema.subprocess.run([manage, tmp, 'add'] + alvo, capture_output=True, timeout=600,
                                   creationflags=0x08000000 if os.name == 'nt' else 0)
        if r.returncode != 0 or not os.path.exists(tmp):
            log.warning('kiwix-manage falhou (%s): %s', r.returncode, r.stderr[:300])
            return None
        os.replace(tmp, lib)
        with open(lista, 'w', encoding='utf-8') as f:
            f.write(assinatura)
        log.info('library.xml gerado com %d ZIMs', len(zims))
        return lib
    except Exception as e:
        log.warning('não gerei o library.xml: %s', e)
        return None


def reiniciar_se_mudou():
    """Botão 'Atualizar biblioteca': se a lista de ZIMs mudou, reinicia o kiwix-serve."""
    zims, ruins = listar_zims()   # o servidor antigo continua atendendo enquanto isto roda
    if sorted(zims) != sorted(_estado['zims']) or not disponivel():
        exe = _estado['exe'] or achar_exe()
        if exe:
            _library_xml(exe, zims)   # prepara antes de parar: a troca leva ~1 s
        parar()
        iniciar(_estado['porta'] or 8801, evitar=(config.estado['porta_app'],), lista=(zims, ruins))
        return True
    return False


def parar():
    sistema.encerrar(_estado['processo'])
    _estado['processo'] = None
    _estado['pronto'] = False


def _ler_catalogo(dados):
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    raiz = ElementTree.fromstring(dados)
    out = []
    for e in raiz.findall('a:entry', ns):
        def txt(tag):
            el = e.find('a:' + tag, ns)
            return (el.text or '').strip() if el is not None else ''
        uid = txt('id').replace('urn:uuid:', '')
        link = ''
        for l in e.findall('a:link', ns):
            if l.get('type') == 'text/html':
                link = l.get('href')
        nome = txt('name')
        langs = txt('language').split(',')
        tags = txt('tags')
        # Stack Exchange bilíngue (pt.stackoverflow, portuguese.stackexchange): conta como português
        if len(langs) == 2 and 'por' in langs:
            idioma = 'pt'
        else:
            idioma = 'mul' if len(langs) > 1 else textos.normalizar_idioma(langs[0])
        out.append({'id': uid, 'nome': nome, 'titulo': nome_pt(nome, txt('title')), 'lang_kiwix': txt('language'),
                    'titulo_original': txt('title'), 'resumo': txt('summary'), 'idioma': idioma,
                    'artigos': int(txt('articleCount') or 0), 'url': link,
                    'texto_completo': '_ftindex:yes' in tags,
                    'saude': (any(t in tags for t in ('medic', 'wikem', 'mdwiki')) or 'medic' in nome
                              or nome.startswith(('medlineplus', 'wwwnc.cdc', 'librepathology', 'nhs.uk',
                                                  'quickguidesformedicine', 'skin-of-color', 'libretexts.org_en_med'))),
                    'secundario': nome.startswith(SECUNDARIOS)})
    ordem = {'pt': 0, 'en': 1}
    out.sort(key=lambda b: (ordem.get(b['idioma'], 2), -b['artigos']))
    return out


# ------------------------------------------------------------------ HTTP

def _get(caminho, timeout=6.0):
    porta = _estado['porta']
    c = http.client.HTTPConnection('127.0.0.1', porta, timeout=timeout)
    try:
        c.request('GET', caminho, headers={'Accept-Encoding': 'identity'})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


# ------------------------------------------------------------------ busca

def _marcar(desc):
    """Descrição do kiwix (HTML com <b>) -> HTML seguro com <mark>."""
    t = re.sub(r'<b>(.*?)</b>', '\x02\\1\x03', desc or '', flags=re.S)
    t = unescape(re.sub(r'<[^>]+>', '', t))
    t = re.sub(r'\s+', ' ', t).strip(' .')
    if len(t) > 360:
        t = t[:360].rsplit(' ', 1)[0] + ' …'
    return escape(t).replace('\x02', '<mark>').replace('\x03', '</mark>')


def _buscar_grupo(padrao, ids, n, timeout):
    q = [('pattern', padrao), ('format', 'xml'), ('pageLength', str(n))] + [('books.id', i) for i in ids]
    st, dados = _get(RAIZ_URL + '/search?' + urlencode(q), timeout)
    if st != 200:
        raise RuntimeError('kiwix %s' % st)
    raiz = ElementTree.fromstring(dados)
    itens = []
    for it in raiz.iter('item'):
        link = (it.findtext('link') or '').strip()
        livro_titulo = (it.findtext('book/title') or '').strip()
        itens.append({'titulo': (it.findtext('title') or '').strip(), 'url': link,
                      'trecho': _marcar(it.findtext('description') or ''), 'livro_titulo_kiwix': livro_titulo})
    return itens


def _sugestoes(padrao, livro, n, timeout):
    q = urlencode({'content': livro['url'].rsplit('/', 1)[-1], 'term': padrao, 'count': n})
    st, dados = _get(RAIZ_URL + '/suggest?' + q, timeout)
    if st != 200:
        return []
    itens = []
    for s in json.loads(dados.decode('utf-8', 'replace')):
        if s.get('kind') != 'path' or not s.get('path'):
            continue
        itens.append({'titulo': re.sub(r'<[^>]+>', '', unescape(s.get('label') or s.get('value') or '')),
                      'url': RAIZ_URL + '/content/' + livro['url'].rsplit('/', 1)[-1] + '/' + quote(s['path']),
                      'trecho': ''})
    return itens


def _livro_da_url(url):
    m = re.match(re.escape(RAIZ_URL) + r'/content/([^/]+)/', url)
    nome_url = m.group(1) if m else ''
    for b in _estado['livros']:
        if b['url'].rsplit('/', 1)[-1] == nome_url:
            return b
    return None


def buscar(consulta, idiomas=('pt', 'en', 'mul'), por_idioma=10, timeout=6.0, livro_ids=None, limite=40):
    """Busca em todos os ZIMs, agrupando por idioma (o kiwix-serve não mistura idiomas)."""
    if not disponivel():
        return {'resultados': [], 'erro': _estado['erro'] or 'Enciclopédias ainda abrindo...'}
    ts = textos.termos(consulta)
    padrao = ' '.join(ts) if ts else consulta
    grupos = {}
    sem_ft = []
    for b in _estado['livros']:
        if livro_ids and b['id'] not in livro_ids:
            continue
        if b['idioma'] not in idiomas and not (b['idioma'] not in ('pt', 'en') and 'mul' in idiomas):
            continue
        if b['texto_completo']:
            # o kiwix-serve recusa uma busca que mistura idiomas: agrupa pelo idioma EXATO declarado
            chave = b['lang_kiwix'] if ',' not in b['lang_kiwix'] else 'outro:' + b['id']
            grupos.setdefault(chave, []).append(b)
        else:
            sem_ft.append(b)
    futuros = {}
    for chave, bs in grupos.items():
        if chave.startswith('outro:'):
            futuros[_pool.submit(_buscar_grupo, padrao, [bs[0]['id']], 3, timeout)] = chave
            continue
        # os maiores livros (Wikipédias, Wikibooks...) são consultados sozinhos, para não perderem
        # as vagas para livros menores; o resto vai em lotes paralelos
        bs = sorted(bs, key=lambda b: -b['artigos'])
        grandes, resto = bs[:5], bs[5:]
        for b in grandes:
            futuros[_pool.submit(_buscar_grupo, padrao, [b['id']], por_idioma, timeout)] = chave
        # verbete exato pelo título (a busca de texto às vezes não traz "Meteorologia" no topo)
        for b in grandes[:2]:
            futuros[_pool.submit(_sugestoes, padrao, b, 3, timeout)] = 'sug:' + b['id']
        for i in range(0, len(resto), 15):
            futuros[_pool.submit(_buscar_grupo, padrao, [b['id'] for b in resto[i:i + 15]], 6, timeout)] = chave
    for b in sem_ft:
        futuros[_pool.submit(_sugestoes, padrao, b, 3, timeout)] = 'sug:' + b['id']
    resultados = []
    erros = []
    try:
        for f in cf.as_completed(futuros, timeout=timeout + 1):
            try:
                itens = f.result()
            except Exception as e:
                erros.append(str(e))
                continue
            for it in itens:
                b = _livro_da_url(it['url'])
                it['livro'] = b['titulo'] if b else it.get('livro_titulo_kiwix', '')
                it['livro_id'] = b['id'] if b else ''
                it['idioma'] = b['idioma'] if b else ''
                it['saude'] = bool(b and b['saude'])
                it['secundario'] = bool(b and b['secundario'])
                it.pop('livro_titulo_kiwix', None)
                resultados.append(it)
    except cf.TimeoutError:
        erros.append('algumas enciclopédias demoraram demais')
    # relevância: termos da busca no título valem mais; depois trechos com mais termos
    raizes = [textos.sem_acento(t)[:max(4, len(t) - 2)] for t in ts]
    alvo = textos.sem_acento(' '.join(ts).lower())
    consulta_en = bool(re.search(r'\b(the|how|what|why|of|and|to|for|with|is)\b', consulta.lower()))
    for i, it in enumerate(resultados):
        tit = textos.sem_acento(it['titulo'].lower())
        tit_limpo = re.sub(r'\s*\(.*?\)\s*$', '', tit).strip()
        it['_nota'] = (3 * sum(1 for r in raizes if r in tit) + min(4, it['trecho'].count('<mark>'))
                       - (2 if it.get('secundario') else 0)
                       + (4 if tit_limpo == alvo and not consulta_en else 0)              # título = busca
                       + (1.5 if it['idioma'] == 'pt' and not consulta_en else 0)         # prefere português
                       - (1 if len(tit) > 90 else 0))                                     # títulos-lista longos
        it['_i'] = i
    resultados.sort(key=lambda r: (-r['_nota'], r['_i']))
    # sem repetir o mesmo artigo vindo de duas cópias/versões
    vistos, unicos = set(), []
    for it in resultados:
        it.pop('_nota', None)
        it.pop('_i', None)
        chave = (it['titulo'].lower(), it['livro'])
        if chave not in vistos:
            vistos.add(chave)
            unicos.append(it)
    return {'resultados': unicos[:limite], 'erro': '; '.join(erros[:2]) if erros and not unicos else ''}


# ------------------------------------------------------------------ texto de artigo (RAG)

PULAR = ('infobox', 'navbox', 'reference', 'mw-editsection', 'toc', 'catlinks', 'metadata', 'sidebar',
         'hatnote', 'thumbcaption', 'mw-references', 'reflist', 'vertical-navbox', 'ambox', 'gallery',
         'noprint', 'mw-jump', 'printfooter', 'post-signature', 'comments', 'question-status')


def texto_artigo(url, timeout=6.0):
    if not url.startswith(RAIZ_URL + '/content/'):
        return ''
    st, dados = _get(url, timeout)
    if st in (301, 302):
        return ''
    if st != 200:
        return ''
    t = textos.html_para_texto(dados, PULAR)
    t = re.sub(r'\[\d+\]|\[editar\]|\[edit\]', '', t)
    return t


def trechos_para_ia(consulta, n=3, idiomas=('pt',), tamanho=1400, timeout=5.0):
    ids = [b['id'] for b in _estado['livros'] if not b['secundario']]
    limpa = ' '.join(textos.termos(consulta, tirar_negados=True))
    r = buscar(limpa or consulta, idiomas=idiomas, por_idioma=max(n, 5), timeout=timeout, livro_ids=ids)
    saida = []
    futuros = []
    for it in [x for x in r['resultados'] if x['trecho'] or x['titulo']][:n]:
        futuros.append((it, _pool.submit(texto_artigo, it['url'], timeout)))
    for it, f in futuros:
        try:
            t = f.result(timeout=timeout + 1)
        except Exception:
            continue
        if len(t) < 120:
            continue
        saida.append({'fonte': 'zim', 'titulo': '%s — %s' % (it['titulo'], it['livro']), 'tema': it['livro'],
                      'idioma': it['idioma'], 'pagina': None, 'url': it['url'], 'saude': it.get('saude'),
                      'texto': textos.trecho_relevante(t, consulta, tamanho)})
    return saida
