"""IA local (llamafile + Qwen3): sobe sob demanda, desliga quando ociosa; RAG e tradução."""
import concurrent.futures
import http.client
import json
import os
import re
import shutil
import threading
import time

from . import config, indice, kiwix, sistema, textos

log = config.log

_estado = {'processo': None, 'porta': None, 'modelo': None, 'situacao': 'desligada', 'erro': '',
           'ultimo_uso': 0.0, 'inicio_carga': 0.0, 'copiando': None}
_lock_inicio = threading.RLock()   # RLock: a troca de motor tenta de novo por dentro
_lock_geracao = threading.Lock()   # um pedido por vez (o modelo roda em 1 slot)
_cache_en = {}

AVISO_SAUDE = 'Confira no manual original. Não substitui atendimento médico.'
NAO_ENCONTREI = 'Não encontrei isso nas fontes da biblioteca.'

_PALAVRAS_SAUDE = re.compile(
    r'\b(ferid|febre|rem[eé]di|doen[cç]|sangr|queimad|fratur|picad|veneno|intoxic|diarr|v[oô]mit|infec|'
    r'parto|gr[aá]vid|beb[eê]|crian[cç]a doente|dor|press[aã]o|diabet|medica|primeiros socorros|desmaio|'
    r'convuls|alergi|mordid|cobra|escorpi|aranha|desidrat|soro|ressuscit|rcp|afogam|hemorr|corte|pontos)',
    re.I)


# ------------------------------------------------------------------ descoberta

_motor = {'evitar_llamacpp': False}


def achar_exe():
    """Prefere o llama-server oficial (Programas\\LlamaCpp): lê a pergunta e escreve ~2x mais rápido.
    O llamafile fica de reserva (usado se o llama-server não abrir neste computador)."""
    if not _motor['evitar_llamacpp']:
        for p in sistema.achar('Programas/LlamaCpp/**/llama-server.exe'):
            if os.path.isfile(p):
                return p
    return achar_llamafile()


def achar_llamafile():
    for padrao in ('Programas/IA/**/llamafile*.exe', 'Programas/IA/**/llamafile*', 'Programas/**/llamafile*.exe'):
        for p in sistema.achar(padrao):
            if os.path.isfile(p) and not p.endswith('.ok') and os.path.getsize(p) > 1_000_000:
                return p
    return None


_validos = {}  # (caminho, tamanho, data) -> bool; evita reabrir o .gguf enquanto a IA o lê


def modelos():
    out = []
    for p in sistema.achar('4_IA/**/*.gguf'):
        try:
            st = os.stat(p)
        except OSError:
            continue
        chave = (p, st.st_size, st.st_mtime)
        if chave not in _validos:
            if not (sistema.arquivo_pronto(p) and sistema.gguf_valido(p)):
                continue  # não guarda o "não": pode ficar pronto daqui a pouco
            _validos[chave] = True
        nome = os.path.basename(p)
        tam = os.path.getsize(p)
        m = re.search(r'(\d+(?:\.\d+)?)[bB]', nome)
        bilhoes = float(m.group(1)) if m else tam / 6e8
        out.append({'arquivo': nome, 'caminho': p, 'tamanho': tam, 'bilhoes': bilhoes})
    out.sort(key=lambda x: x['tamanho'])
    return out


def escolher_modelo():
    """'auto' = o equilíbrio entre rapidez e qualidade: o maior modelo de até ~4 bilhões de parâmetros
    (o 4B escreve bem melhor que o 1.7B e ainda responde em menos de 1 minuto num notebook sem placa de vídeo;
    o 8B leva vários minutos)."""
    ms = modelos()
    if not ms:
        return None, 'Nenhum modelo de IA (.gguf) em 4_IA.'
    pref = config.obter('modelo')
    if pref and pref != 'auto':
        for m in ms:
            if m['arquivo'] == pref:
                total, livre = sistema.memoria()
                if livre and livre < m['tamanho'] * 1.15 and m is not ms[0]:
                    return ms[0], 'Pouca memória livre agora; usando o modelo rápido.'
                return m, ''
    medios = [m for m in ms if m['bilhoes'] <= 4.5]
    return (medios[-1] if medios else ms[0]), ''


# ------------------------------------------------------------------ cópia no computador

def _caminho_copia(m):
    return os.path.join(config.CACHE_PC, m['arquivo'])


def copia_pronta(m):
    c = _caminho_copia(m)
    try:
        return os.path.getsize(c) == m['tamanho']
    except OSError:
        return False


def caminho_para_carregar(m):
    """A cópia no disco do computador, se existir e estiver inteira; senão o arquivo do SSD."""
    if config.obter('copia_pc') and copia_pronta(m):
        return _caminho_copia(m)
    return m['caminho']


_copiando = threading.Lock()


def preparar_copia(m):
    """Copia o modelo do SSD para o disco do computador (uma vez). Só o modelo em uso fica guardado."""
    if not config.obter('copia_pc') or copia_pronta(m):
        return copia_pronta(m)
    if not _copiando.acquire(blocking=False):
        return False
    try:
        os.makedirs(config.CACHE_PC, exist_ok=True)
        if shutil.disk_usage(config.CACHE_PC).free < m['tamanho'] + 5 * 2**30:
            log.info('cópia da IA no computador: pouco espaço no disco interno; seguindo pelo SSD')
            return False
        destino = _caminho_copia(m)
        t = time.time()
        _estado['copiando'] = m['arquivo']
        with open(m['caminho'], 'rb') as a, open(destino + '.part', 'wb') as b:
            shutil.copyfileobj(a, b, 8 << 20)
        os.replace(destino + '.part', destino)
        for velho in os.listdir(config.CACHE_PC):
            if velho != m['arquivo'] and velho.endswith(('.gguf', '.part')):
                try:
                    os.remove(os.path.join(config.CACHE_PC, velho))
                except OSError:
                    pass
        log.info('IA copiada para o computador (%s) em %.0fs', config.CACHE_PC, time.time() - t)
        return True
    except OSError as e:
        log.warning('cópia da IA no computador falhou: %s', e)
        return False
    finally:
        _estado['copiando'] = None
        _copiando.release()


def apagar_copia():
    if os.path.isdir(config.CACHE_PC):
        for f in os.listdir(config.CACHE_PC):
            if f.endswith(('.gguf', '.part')):
                try:
                    os.remove(os.path.join(config.CACHE_PC, f))
                except OSError:
                    pass


def tamanho_copia():
    try:
        return sum(os.path.getsize(os.path.join(config.CACHE_PC, f)) for f in os.listdir(config.CACHE_PC)
                   if f.endswith('.gguf'))
    except OSError:
        return 0


def aquecer():
    """Ao abrir o app: guarda a cópia no PC (se preciso) e já liga a IA em segundo plano."""
    def aquecer_buscas():
        # a 1ª busca nas enciclopédias lê os índices do SSD (lento); fazendo uma agora, a pergunta não espera
        for _ in range(60):
            if kiwix.disponivel():
                break
            time.sleep(2)
        for q, idi in (('água ferida febre', 'pt'), ('water wound fever', 'en')):
            try:
                _candidatos(q, idi)
            except Exception:
                pass
        log.info('buscas da IA aquecidas')

    threading.Thread(target=aquecer_buscas, daemon=True, name='ia-aquecer-buscas').start()

    def laco():
        try:
            m, _ = escolher_modelo()
            if not m or not disponivel()[0]:
                return
            preparar_copia(m)
            if config.obter('ia_ao_abrir'):
                garantir_rodando(timeout=600)
        except Exception:
            log.exception('erro ao preparar a IA')
    threading.Thread(target=laco, daemon=True, name='ia-aquecer').start()


def disponivel():
    """Há programa e modelo para rodar a IA? (não liga nada)"""
    if not achar_exe():
        return False, 'Programa da IA (llamafile) não encontrado em Programas\\IA.'
    if not modelos():
        return False, 'Nenhum modelo de IA (.gguf) encontrado na pasta 4_IA.'
    return True, ''


def estado():
    ok, motivo = disponivel()
    p = _estado['processo']
    rodando = p is not None and p.poll() is None
    if _estado['situacao'] in ('pronta', 'carregando') and not rodando:
        _estado['situacao'] = 'desligada'
    m = _estado['modelo']
    return {'disponivel': ok, 'motivo': motivo, 'situacao': _estado['situacao'], 'erro': _estado['erro'],
            'modelo': m['arquivo'] if m else None, 'modelos': [x['arquivo'] for x in modelos()],
            'copiando': _estado['copiando'], 'copia_gb': round(tamanho_copia() / 2**30, 2),
            'segundos_carregando': int(time.time() - _estado['inicio_carga']) if _estado['situacao'] == 'carregando' else 0}


# ------------------------------------------------------------------ ciclo de vida

def garantir_rodando(timeout=300, aviso=None):
    """Liga a IA (llama-server ou llamafile) se preciso e espera ficar pronto. Retorna (ok, mensagem)."""
    _estado['ultimo_uso'] = time.time()
    with _lock_inicio:
        p = _estado['processo']
        if p is not None and p.poll() is None and _estado['situacao'] == 'pronta':
            return True, ''
        if p is None or p.poll() is not None:
            ok, motivo = disponivel()
            if not ok:
                _estado['erro'] = motivo
                return False, motivo
            m, nota = escolher_modelo()
            if not m:
                return False, nota
            exe = achar_exe()
            porta = sistema.porta_livre(8802, evitar=(config.estado['porta_app'], kiwix.estado()['porta']))
            threads = max(2, min(8, sistema.nucleos_rapidos()))
            origem = caminho_para_carregar(m)
            # --no-mmap: lê o modelo inteiro para a RAM de uma vez (sem isso, a 1ª resposta fica lenta
            # puxando pedaços do disco)
            if os.path.basename(exe).lower().startswith('llama-server'):
                sistema.preparar_runtime(exe)
                args = [exe, '-m', origem, '--host', '127.0.0.1', '--port', str(porta), '-c', '8192',
                        '-t', str(threads), '-np', '1', '--no-webui', '--jinja', '--load-mode', 'none']
            else:
                args = [exe, '-m', origem, '--server', '--host', '127.0.0.1', '--port', str(porta),
                        '-c', '8192', '-t', str(threads), '-np', '1', '--nologo', '--no-webui', '--gpu', 'disable',
                        '--jinja', '--no-mmap']
            log.info('ligando a IA: %s com %s (%d threads, porta %d, %s)', m['arquivo'], os.path.basename(exe),
                     threads, porta, 'cópia no computador' if origem != m['caminho'] else 'do SSD')
            env = {}
            if not config.estado['somente_leitura']:
                env = {'HOME': config.TMP, 'LLAMA_CACHE': config.TMP, 'XDG_CACHE_HOME': config.TMP}
            _estado.update(processo=sistema.iniciar_processo(args, cwd=config.RAIZ, env_extra=env,
                                                             log_arquivo=os.path.join(config.DADOS, 'ia_log.txt')),
                           porta=porta, modelo=m, situacao='carregando', erro='', inicio_carga=time.time(), exe=exe)
            if nota:
                log.info(nota)
        p = _estado['processo']
        limite = time.time() + timeout
        while time.time() < limite:
            if p.poll() is not None:
                exe = _estado.get('exe') or ''
                if not _motor['evitar_llamacpp'] and 'llama-server' in os.path.basename(exe).lower() \
                        and time.time() - _estado['inicio_carga'] < 30 and achar_llamafile():
                    # o llama-server não abriu neste PC (DLL, processador antigo...): volta ao llamafile
                    log.warning('llama-server fechou ao abrir (código %s); usando o llamafile', p.poll())
                    _motor['evitar_llamacpp'] = True
                    _estado.update(processo=None, situacao='desligada')
                    return garantir_rodando(timeout, aviso)
                _estado['situacao'] = 'erro'
                _estado['erro'] = ('A IA fechou ao carregar (código %s). Pode faltar memória: tente o modelo '
                                   'menor nas Configurações. Detalhes em App\\dados\\ia_log.txt.' % p.poll())
                return False, _estado['erro']
            try:
                st, _ = _req('GET', '/health', timeout=2)
                if st == 200:
                    _estado['situacao'] = 'pronta'
                    log.info('IA pronta em %.0fs', time.time() - _estado['inicio_carga'])
                    return True, ''
            except OSError:
                pass
            if aviso:
                aviso(int(time.time() - _estado['inicio_carga']))
            time.sleep(1)
        _estado['erro'] = 'A IA demorou demais para carregar.'
        return False, _estado['erro']


def desligar(motivo=''):
    p = _estado['processo']
    if p is not None and p.poll() is None:
        log.info('desligando a IA %s', motivo)
    sistema.encerrar(p)
    _estado.update(processo=None, situacao='desligada')


def vigiar_ociosidade():
    """Thread: desliga a IA depois de X minutos sem uso (economiza bateria e RAM)."""
    def laco():
        while True:
            time.sleep(30)
            p = _estado['processo']
            if p is None or p.poll() is not None or _lock_geracao.locked():
                continue
            minutos = float(config.obter('minutos_ia') or 15)
            if time.time() - _estado['ultimo_uso'] > minutos * 60:
                desligar('(%g min sem uso)' % minutos)
    threading.Thread(target=laco, daemon=True, name='ia-ociosa').start()


# ------------------------------------------------------------------ HTTP com o llamafile

def _req(metodo, caminho, corpo=None, timeout=30):
    c = http.client.HTTPConnection('127.0.0.1', _estado['porta'], timeout=timeout)
    try:
        c.request(metodo, caminho, body=json.dumps(corpo) if corpo is not None else None,
                  headers={'Content-Type': 'application/json'})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def conversar(mensagens, max_tokens=900, temperatura=0.3, pensar=None):
    """Gera resposta em streaming. Rende ('texto'|'pensando', pedaço)."""
    if pensar is None:
        pensar = bool(config.obter('pensar_mais'))
    msgs = [dict(m) for m in mensagens]
    instruct = 'instruct' in ((_estado['modelo'] or {}).get('arquivo') or '').lower()   # modelos sem raciocínio
    if instruct:
        pensar = False
    if not pensar and not instruct and msgs and msgs[-1]['role'] == 'user':
        msgs[-1]['content'] += ' /no_think'
    corpo = {'messages': msgs, 'stream': True, 'max_tokens': max_tokens if not pensar else max_tokens + 1500,
             'temperature': temperatura, 'top_p': 0.9, 'repeat_penalty': 1.05,
             'chat_template_kwargs': {'enable_thinking': bool(pensar)}}
    with _lock_geracao:
        _estado['ultimo_uso'] = time.time()
        c = http.client.HTTPConnection('127.0.0.1', _estado['porta'], timeout=600)
        try:
            c.request('POST', '/v1/chat/completions', body=json.dumps(corpo),
                      headers={'Content-Type': 'application/json'})
            r = c.getresponse()
            if r.status != 200:
                raise RuntimeError('IA respondeu %s: %s' % (r.status, r.read()[:200]))
            dentro_think = False
            while True:
                linha = r.readline()
                if not linha:
                    break
                linha = linha.strip()
                if not linha.startswith(b'data:'):
                    continue
                dado = linha[5:].strip()
                if dado == b'[DONE]':
                    break
                try:
                    ev = json.loads(dado)
                except ValueError:
                    continue
                for ch in ev.get('choices') or []:
                    d = ch.get('delta') or {}
                    if d.get('reasoning_content'):
                        yield ('pensando', d['reasoning_content'])
                    t = d.get('content')
                    if not t:
                        continue
                    # se o servidor não separar o raciocínio, filtra <think>...</think>
                    while t:
                        if dentro_think:
                            k = t.find('</think>')
                            if k < 0:
                                yield ('pensando', t)
                                t = ''
                            else:
                                dentro_think = False
                                t = t[k + 8:]
                        else:
                            k = t.find('<think>')
                            if k < 0:
                                yield ('texto', t)
                                t = ''
                            else:
                                if k:
                                    yield ('texto', t[:k])
                                dentro_think = True
                                t = t[k + 7:]
                _estado['ultimo_uso'] = time.time()
        finally:
            c.close()
            _estado['ultimo_uso'] = time.time()


def completar(mensagens, max_tokens=120, temperatura=0.1):
    return ''.join(t for tipo, t in conversar(mensagens, max_tokens, temperatura, pensar=False)
                   if tipo == 'texto').strip()


# ------------------------------------------------------------------ tarefas

def termos_em_ingles(consulta):
    """Traduz a busca/pergunta para termos de busca em inglês (com cache)."""
    chave = consulta.strip().lower()
    if chave in _cache_en:
        return _cache_en[chave]
    ok, _ = garantir_rodando()
    if not ok:
        return ''
    r = completar([
        {'role': 'system', 'content': 'You translate Brazilian Portuguese questions into short English search '
                                      'queries. Reply with only the English query, 2 to 8 words, no quotes.'},
        {'role': 'user', 'content': consulta}], max_tokens=40)
    r = re.sub(r'[\r\n].*', '', r, flags=re.S).strip(' "\'.')
    r = r[:120]
    _cache_en[chave] = r
    return r


def palavras_chave(pergunta):
    """Uma chamada curta à IA: palavras-chave com sinônimos em PT e EN (melhora a recuperação)."""
    chave = 'kw:' + pergunta.strip().lower()
    if chave in _cache_en:
        return _cache_en[chave]
    r = completar([
        {'role': 'system', 'content':
            'Você gera palavras-chave de busca para uma biblioteca de manuais práticos. Responda exatamente '
            'em duas linhas, sem explicações:\nPT: 3 a 6 palavras soltas em português (sinônimos e termos técnicos '
            'do assunto, nada genérico como "métodos" ou "técnicas")\n'
            'EN: 3 to 6 single English keywords (synonyms and technical terms, nothing generic)'},
        {'role': 'user', 'content': pergunta}], max_tokens=40)
    pt = en = ''
    for linha in r.splitlines():
        l = linha.strip().strip('*').strip()
        if l.upper().startswith('PT:'):
            pt = l[3:].strip()
        elif l.upper().startswith('EN:'):
            en = l[3:].strip()
    pt, en = re.sub(r'[,;]', ' ', pt)[:200], re.sub(r'[,;]', ' ', en)[:200]
    _cache_en[chave] = (pt, en)
    return pt, en


def _ligar_com_status():
    """Liga a IA numa thread e vai informando os segundos de carga."""
    p = _estado['processo']
    if p is not None and p.poll() is None and _estado['situacao'] == 'pronta':
        _estado['ultimo_uso'] = time.time()
        return
    res = {}
    t = threading.Thread(target=lambda: res.update(r=garantir_rodando()), daemon=True)
    t.start()
    ini = time.time()
    while t.is_alive():
        s = int(time.time() - ini)
        yield {'tipo': 'status', 'texto': 'Carregando a IA… %d s (com a cópia no computador leva poucos segundos)' % s}
        t.join(2)
    ok, motivo = res.get('r', (False, 'falha ao ligar a IA'))
    if not ok:
        yield {'tipo': 'erro', 'texto': motivo}


def eh_saude(pergunta, fontes):
    if _PALAVRAS_SAUDE.search(pergunta or ''):
        return True
    return any((f.get('tema') or '').lower().startswith('saude') or (f.get('tema') or '').lower().startswith('saúde')
               or f.get('saude') for f in fontes)


SISTEMA_RAG = (
    'Você é o assistente da Biblioteca Offline, usada em situações de emergência sem internet. '
    'Responda SEMPRE em português do Brasil, de forma prática, clara e curta: se for um procedimento, no '
    'máximo 8 passos numerados de uma ou duas frases. Use SOMENTE as informações dos trechos numerados fornecidos '
    '(alguns estão em inglês: traduza). No fim de cada frase, cite a fonte com o número entre colchetes, por '
    'exemplo [1] ou [2][3]; não escreva "Fonte:" nem liste as fontes no fim. Ignore os trechos que não tratam '
    'do assunto da pergunta. Não use conhecimento de fora dos trechos e não invente. Somente se NENHUM trecho '
    'tratar do assunto, responda apenas: "' + NAO_ENCONTREI + '" e, numa segunda linha, sugira palavras para '
    'buscar. Se você respondeu com base nos trechos, termine na última informação, sem avisos nem sugestões.'
)


TAM_TRECHO = 750


def _candidatos_docs(consulta, idioma):
    try:
        c = indice.trechos_para_ia(consulta, n=8, idioma=idioma if idioma == 'en' else None, tamanho=TAM_TRECHO)
    except Exception as e:
        log.warning('trechos docs %s: %s', idioma, e)
        c = []
    return _marcar_posicao(c, consulta)


def _candidatos_zim(consulta, idioma):
    c = []
    if kiwix.disponivel():
        try:
            c = kiwix.trechos_para_ia(consulta, n=3, idiomas=(idioma,), tamanho=TAM_TRECHO)
        except Exception as e:
            log.warning('trechos kiwix %s: %s', idioma, e)
    return _marcar_posicao(c, consulta)


def _candidatos(consulta, idioma):
    """Trechos candidatos de manuais e enciclopédias num idioma (mais do que cabem no prompt)."""
    return _candidatos_docs(consulta, idioma) + _candidatos_zim(consulta, idioma)


def _marcar_posicao(c, consulta):
    posicao = {}
    for f in c:
        f['consulta'] = consulta
        f['_pos'] = posicao.get(f['fonte'], 0)   # colocação na busca de origem (0 = primeiro)
        posicao[f['fonte']] = f['_pos'] + 1
    return c


def _escolher(fontes, pergunta, kw_pt='', kw_en=''):
    """Reordena os candidatos: os que cobrem mais termos da pergunta e das palavras-chave
    (termos raros valem mais) e vieram bem colocados na busca; no máx. 2 por documento."""
    grupos = {'pt': textos.termos(pergunta + ' ' + kw_pt, tirar_negados=True),
              'en': textos.termos(kw_en, tirar_negados=True)}
    pesos = {}
    for ts in grupos.values():
        for t, p in indice.peso_termos(ts).items():
            pesos[textos.raiz(t)] = p
    principais = {textos.raiz(t) for t in textos.termos(pergunta, True)}
    vistos, unicos = set(), []
    for f in fontes:
        k = (f['url'].split('#')[0].split('?')[0], f.get('pagina'))
        if k in vistos:
            continue
        vistos.add(k)
        base = textos.sem_acento((f['texto'] + ' ' + f['titulo']).lower())
        tit = textos.sem_acento(f['titulo'].lower())
        ts = list(dict.fromkeys(textos.raiz(t) for t in grupos['en' if f.get('idioma') == 'en' else 'pt']))
        if not ts:
            continue
        total = sum(pesos.get(r, 1.0) for r in ts) or 1
        cobertura = sum(pesos.get(r, 1.0) for r in ts if r in base) / total
        freq = sum(min(3, base.count(r)) * pesos.get(r, 1.0) for r in ts) / (3 * total)
        bonus_tit = 0.1 * sum(1 for r in ts if r in tit)
        # perguntas em PT: o trecho PT precisa ter os termos principais da pergunta
        falta = 0.0
        if f.get('idioma') != 'en' and principais:
            falta = 0.4 * sum(1 for r in principais if r not in base) / len(principais)
        manual = 0.15 if f['fonte'] == 'doc' else 0.0   # manuais práticos têm preferência
        f['_nota'] = cobertura + 0.4 * freq + min(0.3, bonus_tit) + 0.25 / (1 + f.get('_pos', 5)) - falta + manual
        unicos.append(f)
    unicos.sort(key=lambda f: -f['_nota'])
    por_doc, saida = {}, []
    for f in unicos:
        d = f['titulo']
        if por_doc.get(d, 0) >= 2 or f['_nota'] < 0.45:
            continue
        por_doc[d] = por_doc.get(d, 0) + 1
        saida.append(f)
    return saida


def montar_contexto(fontes, limite_chars):
    partes, usadas, total = [], [], 0
    for f in fontes:
        bloco_txt = f['texto'].strip()
        if not bloco_txt:
            continue
        cab = f['titulo'] + (' (página %d)' % f['pagina'] if f.get('pagina') else '')
        resto = limite_chars - total
        if resto < 300:
            break
        if len(bloco_txt) > resto:
            bloco_txt = bloco_txt[:resto].rsplit(' ', 1)[0] + '…'
        n = len(usadas) + 1
        partes.append('[%d] %s\n%s' % (n, cab, bloco_txt))
        usadas.append(dict(f, n=n))
        total += len(bloco_txt) + len(cab)
    return '\n\n'.join(partes), usadas


def perguntar(pergunta):
    """Gerador de eventos para a página 'Pergunte': status, fontes, texto, aviso, fim/erro."""
    pergunta = (pergunta or '').strip()[:1000]
    if not pergunta:
        yield {'tipo': 'erro', 'texto': 'Escreva uma pergunta.'}
        return
    ok, motivo = disponivel()
    if not ok:
        yield {'tipo': 'erro', 'texto': 'A IA não está disponível: ' + motivo +
               ' A busca normal continua funcionando.'}
        return

    for ev in _ligar_com_status():
        yield ev
        if ev['tipo'] == 'erro':
            return
    yield {'tipo': 'status', 'texto': 'Escolhendo palavras-chave (português e inglês)…'}
    try:
        kw_pt, kw_en = palavras_chave(pergunta)
    except Exception as e:
        log.warning('palavras-chave: %s', e)
        kw_pt, kw_en = '', ''
    if kw_en:
        yield {'tipo': 'consulta_en', 'texto': kw_en}
    yield {'tipo': 'status', 'texto': 'Procurando nas fontes da biblioteca…'}
    # as buscas (manuais e enciclopédias, PT e EN) rodam ao mesmo tempo
    buscas = [(pergunta, 'pt')] + ([(kw_pt, 'pt')] if kw_pt else []) + ([(kw_en, 'en')] if kw_en else [])
    with concurrent.futures.ThreadPoolExecutor(len(buscas) * 2) as ex:
        futs = [ex.submit(_candidatos_docs, q, i) for q, i in buscas] + [ex.submit(_candidatos_zim, q, i) for q, i in buscas]
        fontes = []
        for f in futs:
            try:
                fontes += f.result(timeout=40)
            except Exception as e:
                log.warning('busca para a IA: %s', e)
    fontes = [f for f in fontes if (f.get('idioma') or 'pt') in ('pt', 'en')]
    m = _estado['modelo'] or {}
    # CPU sem GPU lê ~50 tokens/s de prompt: contexto menor = 1ª palavra mais cedo
    limite = 2800 if m.get('bilhoes', 8) >= 4 else 3600
    contexto, usadas = montar_contexto(_escolher(fontes, pergunta, kw_pt, kw_en), limite)
    saude = eh_saude(pergunta, usadas)
    yield {'tipo': 'fontes', 'fontes': [{'n': f['n'], 'titulo': f['titulo'], 'url': f['url'],
                                         'pagina': f.get('pagina'), 'idioma': f.get('idioma'),
                                         'tipo': f['fonte']} for f in usadas]}
    if saude:
        yield {'tipo': 'aviso', 'texto': AVISO_SAUDE}
    if not usadas:
        yield {'tipo': 'texto', 'texto': NAO_ENCONTREI + '\nTente outras palavras ou use a busca.'}
        yield {'tipo': 'fim'}
        return
    yield {'tipo': 'status', 'texto': 'Escrevendo a resposta…'}
    msgs = [{'role': 'system', 'content': SISTEMA_RAG},
            {'role': 'user', 'content': 'TRECHOS:\n\n%s\n\nPERGUNTA: %s' % (contexto, pergunta)}]
    pensou = False
    for tipo, t in conversar(msgs, max_tokens=500):
        if tipo == 'pensando':
            if not pensou:
                pensou = True
                yield {'tipo': 'status', 'texto': 'Pensando…'}
            continue
        yield {'tipo': 'texto', 'texto': t}
    yield {'tipo': 'fim'}


def traduzir(texto):
    texto = (texto or '').strip()[:6000]
    if not texto:
        yield {'tipo': 'erro', 'texto': 'Cole um texto para traduzir.'}
        return
    ok, motivo = disponivel()
    if not ok:
        yield {'tipo': 'erro', 'texto': 'A IA não está disponível: ' + motivo}
        return
    for ev in _ligar_com_status():
        yield ev
        if ev['tipo'] == 'erro':
            return
    # em partes: o primeiro parágrafo traduzido aparece em segundos, sem esperar o texto todo
    partes = _partes_traducao(texto)
    for i, parte in enumerate(partes):
        yield {'tipo': 'status', 'texto': 'Traduzindo…' if len(partes) == 1 else 'Traduzindo parte %d de %d…' % (i + 1, len(partes))}
        msgs = [{'role': 'system', 'content': 'Você é um tradutor. Traduza o texto do usuário para português do '
                                              'Brasil, fiel e claro, mantendo números, medidas e listas. Responda só '
                                              'com a tradução.'},
                {'role': 'user', 'content': parte}]
        if i:
            yield {'tipo': 'texto', 'texto': '\n\n'}
        for tipo, t in conversar(msgs, max_tokens=min(1200, len(parte) // 2 + 150), temperatura=0.2, pensar=False):
            if tipo == 'texto':
                yield {'tipo': 'texto', 'texto': t}
    yield {'tipo': 'fim'}


def _partes_traducao(texto, alvo=700):
    pars = [x.strip() for x in re.split(r'\n\s*\n', texto) if x.strip()]
    partes, atual = [], ''
    for par in pars:
        while len(par) > alvo * 1.5:   # parágrafo enorme: corta em frases
            corte = par.rfind('. ', 0, alvo) + 1 or alvo
            pedaco, par = par[:corte].strip(), par[corte:].strip()
            if atual:
                partes.append(atual)
                atual = ''
            partes.append(pedaco)
        if atual and len(atual) + len(par) > alvo:
            partes.append(atual)
            atual = par
        else:
            atual = (atual + '\n\n' + par) if atual else par
    if atual:
        partes.append(atual)
    return partes or [texto]
