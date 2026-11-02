"""Botão "Atualizar pela internet": roda _Baixador\\atualizar.py e, no fim, encaixa as novidades.

O trabalho pesado (catálogo do Kiwix, downloads) fica no script do baixador, que grava o
andamento em _Baixador\\atualizacao_estado.json. Aqui só iniciamos, acompanhamos, cancelamos e,
quando termina, trocamos as enciclopédias antigas pelas novas e reindexamos o acervo.
"""
import json
import logging
import os
import subprocess
import threading
import time

from . import config, indice, kiwix, sistema

log = logging.getLogger('biblioteca')

BAIXADOR = os.path.join(config.RAIZ, '_Baixador')
SCRIPT = os.path.join(BAIXADOR, 'atualizar.py')
ESTADO = os.path.join(BAIXADOR, 'atualizacao_estado.json')

_proc = None
_trava = threading.Lock()
_pos = {'mensagem': ''}  # o que o app fez depois do script (troca de ZIMs, reindexação)


def disponivel():
    return os.path.isfile(SCRIPT)


def _ler_estado():
    try:
        with open(ESTADO, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def estado():
    e = _ler_estado()
    rodando = (_proc is not None and _proc.poll() is None) or bool(
        _proc is None and e.get('rodando') and _script_vivo(e.get('pid')))
    if e.get('rodando') and not rodando and not e.get('fim'):
        # o app foi fechado no meio: nada se perdeu, o que já baixou continua guardado
        e['erro'] = ('A última atualização foi interrompida (o app foi fechado no meio). Clique em '
                     '"Atualizar agora" para continuar de onde parou.')
        e['resultado'] = None
    e['rodando'] = rodando
    e['disponivel'] = disponivel()
    e['depois'] = _pos['mensagem']
    return e


def iniciar(verificar, partes=('zims', 'acervo')):
    global _proc
    with _trava:
        if _proc is not None and _proc.poll() is None:
            return False, 'Já tem uma atualização em andamento.'
        if not disponivel():
            return False, 'Não achei _Baixador\\atualizar.py.'
        if indice.progresso.get('rodando'):
            return False, 'A biblioteca está sendo indexada agora; espere terminar.'
        args = [os.path.join(config.RAIZ, 'Programas', 'Python', 'python.exe'), '-I', '-X', 'utf8', SCRIPT]
        if verificar:
            args.append('--verificar')
        else:
            args += ['--partes', ','.join(partes)]
        _pos['mensagem'] = ''
        _proc = sistema.iniciar_processo(args, cwd=BAIXADOR, log_arquivo=os.path.join(config.DADOS, 'atualizacao_log.txt'))
        threading.Thread(target=_acompanhar, args=(_proc, verificar), daemon=True, name='atualizacao').start()
        log.info('atualização pela internet iniciada (%s)', 'verificar' if verificar else ','.join(partes))
        return True, 'Iniciado.'


def cancelar():
    p = _proc
    if p is None or p.poll() is not None:
        return False
    # o script chama curl e outros baixadores: encerra a árvore inteira (tudo é retomável)
    try:
        subprocess.call(['taskkill', '/T', '/F', '/PID', str(p.pid)], stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, creationflags=0x08000000)
    except OSError:
        sistema.encerrar(p)
    e = _ler_estado()
    if e:
        e.update({'rodando': False, 'fim': time.strftime('%Y-%m-%d %H:%M:%S'), 'etapa': 'Cancelada',
                  'erro': 'Cancelada. O que já foi baixado fica guardado; da próxima vez continua de onde parou.'})
        try:
            with open(ESTADO, 'w', encoding='utf-8') as f:
                json.dump(e, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
    log.info('atualização cancelada pelo usuário')
    return True


def limpar_pendentes():
    """Na abertura do app (kiwix ainda parado): apaga ZIMs antigos que já têm versão nova completa."""
    if config.estado.get('somente_leitura'):
        return
    e = _ler_estado()
    if not e.get('remover') or e.get('rodando') and _proc is None and _script_vivo(e.get('pid')):
        return
    restam = []
    for a in e['remover']:
        c = os.path.join(config.ENCICLOPEDIA, a)
        try:
            if os.path.exists(c):
                os.remove(c)
            if os.path.exists(c + '.ok'):
                os.remove(c + '.ok')
            log.info('versão antiga apagada: %s', a)
        except OSError:
            restam.append(a)
    e['remover'] = restam
    try:
        with open(ESTADO, 'w', encoding='utf-8') as f:
            json.dump(e, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def _script_vivo(pid):
    """O processo da atualização (de uma abertura anterior do app) ainda existe? Pergunta direto ao Windows."""
    if not pid:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            codigo = ctypes.c_ulong(0)
            return bool(k.GetExitCodeProcess(h, ctypes.byref(codigo))) and codigo.value == 259  # STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    except Exception:
        return False


def _acompanhar(p, verificar):
    p.wait()
    if verificar:
        return
    try:
        _encaixar()
    except Exception:
        log.exception('erro ao encaixar a atualização')
        _pos['mensagem'] = 'Erro ao terminar a atualização; veja App\\dados\\log.txt.'


def _encaixar():
    """Troca ZIMs antigos pelos novos (o kiwix-serve segura os arquivos abertos) e reindexa o acervo."""
    e = _ler_estado()
    pendentes = [a for a in e.get('remover', []) if os.path.exists(os.path.join(config.ENCICLOPEDIA, a))]
    removidos = []
    if pendentes:
        _pos['mensagem'] = 'Trocando as enciclopédias antigas pelas novas…'
        kiwix.parar()
        time.sleep(1.5)
        for a in pendentes:
            c = os.path.join(config.ENCICLOPEDIA, a)
            try:
                os.remove(c)
                if os.path.exists(c + '.ok'):
                    os.remove(c + '.ok')
                removidos.append(a)
            except OSError as erro:
                log.warning('não consegui apagar a versão antiga %s: %s', a, erro)
        e['remover'] = [a for a in e.get('remover', []) if a not in removidos]
        try:
            with open(ESTADO, 'w', encoding='utf-8') as f:
                json.dump(e, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
    _pos['mensagem'] = 'Abrindo as enciclopédias e indexando os livros novos…'
    kiwix.reiniciar_se_mudou()
    indice.atualizar()
    _pos['mensagem'] = ('Pronto: novidades já aparecem na busca' +
                        (' (%d versões antigas substituídas).' % len(removidos) if removidos else '.'))
    log.info('atualização encaixada: %d ZIMs antigos removidos', len(removidos))
