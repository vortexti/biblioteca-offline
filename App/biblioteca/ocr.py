"""Ler texto de imagens e de PDFs escaneados (OCR) com o reconhecimento que já vem no Windows 10/11.

Nada para instalar: um processo do PowerShell (sem janela, preso ao Job do app) recebe o script
ocr_windows.ps1 pela entrada padrão (não depende da política de scripts do Windows) e depois um
pedido por linha. Só um pedido por vez.
"""
import base64
import json
import logging
import os
import subprocess
import threading
import time

from . import config, sistema

log = logging.getLogger('biblioteca')

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ocr_windows.ps1')
MARCA = '@@RESULTADO@@'
_lock = threading.Lock()
_proc = {'p': None, 'idiomas': None, 'erro': ''}


def _b64(t):
    return base64.b64encode((t or '').encode('utf-8')).decode('ascii')


def _iniciar():
    p = _proc['p']
    if p is not None and p.poll() is None:
        return p
    if os.name != 'nt':
        raise RuntimeError('O leitor de imagens usa o Windows 10 ou 11.')
    p = subprocess.Popen(['powershell.exe', '-NoProfile', '-NoLogo', '-NonInteractive', '-Command', '-'],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         creationflags=0x08000000 | 0x00004000)   # sem janela, prioridade abaixo do normal
    sistema.prender_ao_job(p)
    with open(SCRIPT, encoding='utf-8') as f:
        p.stdin.write(f.read().replace('\r\n', '\n').encode('utf-8') + b'\n\n')
    p.stdin.flush()
    _proc['p'] = p
    r = _resposta(p, 60)
    if not r.get('pronto'):
        raise RuntimeError('o leitor do Windows não abriu: %s' % r.get('erro', ''))
    return p


def _resposta(p, timeout):
    """Lê a saída até a linha com a marca (o resto é ruído do PowerShell)."""
    fim = time.time() + timeout
    res = {}

    def ler():
        while True:
            linha = p.stdout.readline()
            if not linha:
                res['r'] = {'ok': False, 'erro': 'o leitor do Windows fechou'}
                return
            linha = linha.decode('utf-8', 'replace').strip()
            if linha.startswith(MARCA):
                try:
                    res['r'] = json.loads(linha[len(MARCA):])
                except ValueError:
                    res['r'] = {'ok': False, 'erro': 'resposta inválida do leitor'}
                return

    t = threading.Thread(target=ler, daemon=True)
    t.start()
    t.join(max(1, fim - time.time()))
    if t.is_alive():
        sistema.encerrar(p)
        _proc['p'] = None
        return {'ok': False, 'erro': 'o leitor demorou demais (%d s)' % timeout}
    return res['r']


def _pedir(cmd, *args, timeout=120):
    with _lock:
        try:
            p = _iniciar()
        except Exception as e:
            _proc['erro'] = str(e)
            return {'ok': False, 'erro': str(e)}
        linha = 'Responder %s\n' % ' '.join("'%s'" % str(a).replace("'", "''") for a in (cmd,) + args)
        try:
            p.stdin.write(linha.encode('utf-8'))
            p.stdin.flush()
        except OSError:
            _proc['p'] = None
            return {'ok': False, 'erro': 'o leitor do Windows fechou'}
        return _resposta(p, timeout)


def idiomas():
    if _proc['idiomas'] is None:
        r = _pedir('idiomas', timeout=60)
        _proc['idiomas'] = r.get('idiomas') or [] if r.get('ok') else []
        if not r.get('ok'):
            _proc['erro'] = r.get('erro', '')
    return _proc['idiomas']


def disponivel():
    if os.name != 'nt':
        return False, 'O leitor de imagens usa o Windows 10 ou 11.'
    if not idiomas():
        return False, ('O Windows deste computador não tem leitor de texto (OCR) instalado. ' + (_proc['erro'] or ''))
    return True, ''


def _idioma(pedido):
    ids = idiomas()
    if pedido:
        for i in ids:
            if i.lower().startswith(pedido.lower()):
                return i
    for pref in ('pt', 'en'):
        for i in ids:
            if i.lower().startswith(pref):
                return i
    return ids[0] if ids else ''


def _paragrafos(r):
    """Junta as linhas reconhecidas em parágrafos (pula de parágrafo quando o espaço vertical é grande)."""
    linhas = r.get('linhas') or []
    if linhas and not isinstance(linhas[0], list):
        linhas = [linhas]   # o PowerShell achata a lista quando há uma linha só
    saida, ultima_base, alturas = [], None, []
    for item in linhas:
        if len(item) < 5:
            continue
        texto, x0, y0, x1, y1 = item[0], item[1], item[2], item[3], item[4]
        alt = max(1, y1 - y0)
        alturas.append(alt)
        media = sorted(alturas)[len(alturas) // 2]
        if ultima_base is not None and y0 - ultima_base > media * 0.9:
            saida.append('')
        saida.append(texto)
        ultima_base = y1
    # junta linhas do mesmo parágrafo; hífen no fim da linha une a palavra
    pars, atual = [], ''
    for l in saida:
        if not l:
            if atual:
                pars.append(atual)
            atual = ''
        elif atual.endswith('-') and l[:1].islower():
            atual = atual[:-1] + l
        else:
            atual = (atual + ' ' + l) if atual else l
    if atual:
        pars.append(atual)
    return '\n\n'.join(pars)


def ler_imagem(caminho, idioma=''):
    ok, motivo = disponivel()
    if not ok:
        return {'ok': False, 'erro': motivo}
    t = time.time()
    r = _pedir('imagem', _b64(caminho), _idioma(idioma))
    if r.get('ok'):
        r['texto'] = _paragrafos(r)
        r.pop('linhas', None)
        r['segundos'] = round(time.time() - t, 1)
    return r


def ler_pagina_pdf(caminho, pagina, idioma='', png=None):
    ok, motivo = disponivel()
    if not ok:
        return {'ok': False, 'erro': motivo}
    t = time.time()
    r = _pedir('pdf', _b64(caminho), int(pagina), _idioma(idioma), _b64(png) if png else '', timeout=180)
    if r.get('ok'):
        r['texto'] = _paragrafos(r)
        r.pop('linhas', None)
        r['segundos'] = round(time.time() - t, 1)
    return r


def fechar_pdf():
    """Solta o PDF aberto no leitor (para não segurar o arquivo)."""
    if _proc['p'] is not None and _proc['p'].poll() is None:
        _pedir('fechar_pdf', timeout=30)


def fechar():
    p = _proc['p']
    if p is not None:
        sistema.encerrar(p)
    _proc['p'] = None
