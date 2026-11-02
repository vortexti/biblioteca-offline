"""Caminhos (sempre relativos à raiz do SSD), configurações e log."""
import json
import logging
import logging.handlers
import os
import shutil
import threading

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ = os.path.dirname(APP)
DADOS = os.path.join(APP, 'dados')
STATIC = os.path.join(APP, 'static')
VENDOR = os.path.join(APP, 'vendor')
RUNTIME = os.path.join(APP, 'runtime')

ACERVO = os.path.join(RAIZ, '5_Acervo')
ENCICLOPEDIA = os.path.join(RAIZ, '1_Enciclopedia')
IA_MODELOS = os.path.join(RAIZ, '4_IA')
MAPAS = os.path.join(RAIZ, '2_Mapas')
# cópia da IA no disco interno do computador (lê muito mais rápido que o SSD externo)
CACHE_PC = os.path.join(os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'BibliotecaOffline', 'ia')
INSTALADORES = os.path.join(RAIZ, '6_Instaladores')
PROGRAMAS = os.path.join(RAIZ, 'Programas')

ARQ_INDICE = os.path.join(DADOS, 'indice.db')
ARQ_CONFIG = os.path.join(DADOS, 'config.json')
ARQ_LOG = os.path.join(DADOS, 'log.txt')
TMP = os.path.join(DADOS, 'tmp')

PADRAO = {
    'modelo': 'auto',          # 'auto' (= o modelo rápido) ou nome do arquivo .gguf
    'minutos_ia': 60,          # desliga a IA após X minutos sem uso
    'copia_pc': True,          # guarda uma cópia da IA no disco do computador (carrega em segundos)
    'ia_ao_abrir': True,       # já liga a IA ao abrir o app (a 1ª pergunta não espera a carga)
    'ocr_fundo': True,         # lê os PDFs escaneados aos poucos (OCR do Windows) e os põe na busca
    'versao_cfg': 2,
    'pensar_mais': False,      # modo de raciocínio do Qwen3
    'rede_casa': False,        # escutar também na rede (celulares no hotspot)
}

# preenchido em main.preparar()
estado = {
    'somente_leitura': False,
    'aviso_disco': '',
    'porta_app': None,
    'porta_rede': None,
}

_lock = threading.Lock()
_cfg = None
log = logging.getLogger('biblioteca')


def rel(caminho):
    """Caminho relativo à raiz do SSD, com barras normais (portátil)."""
    return os.path.relpath(caminho, RAIZ).replace('\\', '/')


def absoluto(relativo):
    """Converte caminho relativo do índice em absoluto, sem deixar escapar da raiz."""
    p = os.path.normpath(os.path.join(RAIZ, relativo.replace('/', os.sep)))
    raiz = os.path.normpath(RAIZ)
    if os.path.commonpath([p.lower(), raiz.lower()]) != raiz.lower():
        raise ValueError('caminho fora do SSD')
    return p


def testar_escrita():
    """Confere se dá para escrever no SSD (pode estar somente leitura ou cheio)."""
    try:
        os.makedirs(DADOS, exist_ok=True)
        os.makedirs(TMP, exist_ok=True)
        teste = os.path.join(DADOS, '.teste_escrita')
        with open(teste, 'w') as f:
            f.write('ok' * 512)
        os.remove(teste)
    except OSError as e:
        estado['somente_leitura'] = True
        estado['aviso_disco'] = ('O SSD está somente leitura ou cheio. O app funciona, mas não vai '
                                 'atualizar o índice nem salvar configurações. (%s)' % (e.strerror or e))
        return False
    try:
        livre = shutil.disk_usage(RAIZ).free
        if livre < 200 * 1024 * 1024:
            estado['aviso_disco'] = ('O SSD está quase cheio (%d MB livres). O índice pode não ser atualizado.'
                                     % (livre // 2**20))
    except OSError:
        pass
    return True


def iniciar_log():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(message)s', '%d/%m %H:%M:%S')
    if not estado['somente_leitura']:
        try:
            h = logging.handlers.RotatingFileHandler(ARQ_LOG, maxBytes=2_000_000, backupCount=1, encoding='utf-8')
            h.setFormatter(fmt)
            log.addHandler(h)
        except OSError:
            pass


def carregar():
    global _cfg
    with _lock:
        cfg = dict(PADRAO)
        do_arquivo = {}
        try:
            with open(ARQ_CONFIG, encoding='utf-8') as f:
                do_arquivo = json.load(f)
        except (OSError, ValueError):
            pass
        cfg.update(do_arquivo)
        if do_arquivo and int(do_arquivo.get('versao_cfg') or 1) < 2:
            # v2: IA rápida; o antigo padrão de 15 min desligava a IA cedo demais
            if cfg.get('minutos_ia') == 15:
                cfg['minutos_ia'] = 60
            cfg['versao_cfg'] = 2
        _cfg = cfg
        return dict(cfg)


def obter(chave=None):
    if _cfg is None:
        carregar()
    return dict(_cfg) if chave is None else _cfg.get(chave, PADRAO.get(chave))


def salvar(novos):
    if _cfg is None:
        carregar()
    with _lock:
        for k, v in novos.items():
            if k in PADRAO:
                _cfg[k] = v
        if estado['somente_leitura']:
            return False
        try:
            tmp = ARQ_CONFIG + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(_cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, ARQ_CONFIG)
            return True
        except OSError as e:
            log.warning('não salvei config: %s', e)
            return False
