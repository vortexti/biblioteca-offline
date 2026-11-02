"""BIBLIOTECA OFFLINE - acervo completo da Embrapa (Infoteca-e) via OAI-PMH.

Coleta o catalogo inteiro (titulo, tipo, PDFs) e baixa os PDFs para 5_Acervo\\Agricultura\\Embrapa,
com o mesmo padrao do baixar.ps1: arquivo "<id>_<nome>.pdf", marca .ok e linha no _indice.txt
("nome<TAB>Titulo") que o app usa como titulo. Retomavel.

Uso:  BAIXAR_EMBRAPA.bat   (ou python baixar_embrapa.py [--verificar])
"""
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
from html import unescape

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
DEST = os.path.join(RAIZ, '5_Acervo', 'Agricultura', 'Embrapa')
INDICE = os.path.join(DEST, '_indice.txt')
FEITOS = os.path.join(AQUI, 'embrapa_feitos.txt')
CATALOGO = os.path.join(AQUI, 'embrapa_catalogo.json')
LOG = os.path.join(AQUI, 'registro.txt')
OAI = 'https://www.infoteca.cnptia.embrapa.br/infoteca-oai/request'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
PARALELO = 8
RESERVA_GB = int(os.environ.get('BIBLIOTECA_RESERVA_GB') or 20)  # folga do SSD; a atualização do app passa 8
TAM_MAX = 500 * 2**20   # so pula PDFs absurdos (mapas em alta resolucao)
# so o que serve na pratica (fora: noticias de jornal, anais, capitulos repetidos dos livros...)
TIPOS = {'Folhetos', 'Livros', 'Folders', 'Manuais', 'Obras de referência (dicionários, enciclopédias, etc.)'}

_lock = threading.Lock()


def escreve(msg):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    with _lock:
        try:
            print(linha, flush=True)
        except Exception:
            pass
        try:
            with open(LOG, 'a', encoding='utf-8') as f:
                f.write(linha + '\n')
        except OSError:
            pass


def livre_util():
    """Espaco livre descontando o que o baixador de enciclopedias ainda vai gravar."""
    livre = shutil.disk_usage(RAIZ).free
    try:
        pend, ts = open(os.path.join(AQUI, 'reserva_downloads.txt')).read().split()
        if time.time() - int(ts) < 180:
            livre -= int(pend)
    except (OSError, ValueError):
        pass
    return livre


def pagina(url, tentativas=6):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            erro = e
            time.sleep(15 * (t + 1))
    raise RuntimeError('nao abriu %s: %s' % (url, erro))


def coletar():
    """Catalogo completo via OAI-PMH (formato xoai). Salva em embrapa_catalogo.json (retomavel)."""
    estado = {'token': None, 'itens': {}, 'completo': False}
    if os.path.exists(CATALOGO):
        try:
            estado = json.load(open(CATALOGO, encoding='utf-8'))
        except ValueError:
            pass
    if estado.get('completo'):
        return estado['itens']
    n = 0
    while True:
        if estado['token']:
            url = OAI + '?verb=ListRecords&resumptionToken=' + urllib.parse.quote(estado['token'])
        else:
            url = OAI + '?verb=ListRecords&metadataPrefix=xoai'
            if estado.get('desde'):  # atualizacao: so o que entrou depois da ultima coleta
                url += '&from=' + estado['desde']
        x = pagina(url)
        for rec in re.findall(r'<record>(.*?)</record>', x, re.S):
            m = re.search(r'<identifier>oai:[^:]+:doc/(\d+)</identifier>', rec)
            if not m or 'status="deleted"' in rec:
                continue
            doc = m.group(1)
            tit = re.search(r'<element name="title">.*?<field name="value">([^<]*)', rec, re.S)
            tipo = re.search(r'<element name="type">.*?<field name="value">([^<]*)', rec, re.S)
            ano = re.search(r'<element name="issued">.*?<field name="value">(\d{4})', rec, re.S)
            pdfs = []
            for b in re.findall(r'<element name="bitstream">(.*?)</element>', rec, re.S):
                u = re.search(r'<field name="url">([^<]*)', b)
                fmt = re.search(r'<field name="format">([^<]*)', b)
                tam = re.search(r'<field name="size">(\d+)', b)
                if u and fmt and fmt.group(1) == 'application/pdf' and '/bitstream/' in u.group(1):
                    pdfs.append([unescape(u.group(1)), int(tam.group(1)) if tam else 0])
            if pdfs:
                estado['itens'][doc] = {'t': unescape(tit.group(1)).strip() if tit else '',
                                        'tipo': unescape(tipo.group(1)) if tipo else '',
                                        'ano': ano.group(1) if ano else '', 'pdfs': pdfs}
        tok = re.search(r'<resumptionToken[^>]*>([^<]*)</resumptionToken>', x)
        estado['token'] = tok.group(1) if tok and tok.group(1) else None
        n += 1
        if n % 20 == 0 or not estado['token']:
            estado['completo'] = not estado['token']
            if estado['completo']:
                estado['coletado_em'] = time.strftime('%Y-%m-%d')
                estado.pop('desde', None)
            with open(CATALOGO + '.tmp', 'w', encoding='utf-8') as f:
                json.dump(estado, f, ensure_ascii=False)
            os.replace(CATALOGO + '.tmp', CATALOGO)
            escreve('Embrapa: catalogo com %d publicacoes...' % len(estado['itens']))
        if not estado['token']:
            return estado['itens']
        time.sleep(0.5)


def nome_arquivo(doc, url):
    folha = urllib.parse.unquote(url.rsplit('/', 1)[-1].split('?')[0])
    return re.sub(r'[\\/:*?"<>|]', '_', '%s_%s' % (doc, folha))


def baixar(url, destino):
    parcial = destino + '.part'
    for t in range(1, 5):
        try:
            req = urllib.request.Request(url.replace('http://', 'https://'), headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=180) as r, open(parcial, 'wb') as f:
                shutil.copyfileobj(r, f, 1 << 16)
            with open(parcial, 'rb') as f:
                if b'%PDF' not in f.read(1024):
                    os.remove(parcial)
                    return False
            os.replace(parcial, destino)
            with open(destino + '.ok', 'w', encoding='utf-8') as f:
                f.write(url + '\n')
            return True
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                break
            time.sleep(20 * t)
        except Exception:
            time.sleep(20 * t)
    try:
        os.remove(parcial)
    except OSError:
        pass
    return False


def main():
    os.makedirs(DEST, exist_ok=True)
    escreve('===== Embrapa (acervo completo): inicio =====')
    itens = coletar()
    tipos = {}
    tarefas = []
    # marcas .ok ja existentes, lidas de uma vez (checar arquivo por arquivo leva minutos no exFAT)
    prontos = {e.name[:-3] for e in os.scandir(DEST) if e.name.endswith('.ok')}
    for doc, it in itens.items():
        tipos.setdefault(it['tipo'], [0, 0])
        for url, tam in it['pdfs']:
            tipos[it['tipo']][0] += 1
            tipos[it['tipo']][1] += tam
            if tam > TAM_MAX or not (it['tipo'] in TIPOS or it['tipo'].startswith('Obras de refer')):
                continue
            nome = nome_arquivo(doc, url)
            if nome not in prontos:
                tarefas.append((doc, url, tam, nome, it['t']))
    total = sum(t[2] for t in tarefas)
    escreve('Embrapa: %d publicacoes com PDF; faltam %d PDFs (%.1f GB)' % (len(itens), len(tarefas), total / 2**30))
    if '--verificar' in sys.argv:
        for tp, (n, tam) in sorted(tipos.items(), key=lambda x: -x[1][1]):
            print('%-40s %6d PDFs %7.1f GB' % (tp[:40], n, tam / 2**30))
        return
    # ordem de valor: livros e manuais primeiro (ensinam do inicio ao fim), depois folders e folhetos;
    # dentro de cada tipo, menores primeiro
    ordem = {'Livros': 0, 'Manuais': 1, 'Folders': 3, 'Folhetos': 4}
    tipo_doc = {doc: it['tipo'] for doc, it in itens.items()}
    tarefas.sort(key=lambda t: (ordem.get(tipo_doc.get(t[0], ''), 2), t[2]))
    fila = list(tarefas)
    cont = {'ok': 0, 'falha': 0, 'espaco': 0}
    lock = threading.Lock()

    def trabalhador():
        while True:
            with lock:
                if not fila:
                    return
                doc, url, tam, nome, titulo = fila.pop(0)
            if livre_util() - tam < RESERVA_GB * 2**30:
                with lock:
                    cont['espaco'] += 1
                continue
            ok = baixar(url, os.path.join(DEST, nome))
            with lock:
                cont['ok' if ok else 'falha'] += 1
                if ok:
                    with open(INDICE, 'a', encoding='utf-8') as f:
                        f.write('%s\t%s\n' % (nome, titulo.rstrip('.')))
                    with open(FEITOS, 'a', encoding='utf-8') as f:
                        f.write(doc + '\n')
                feitos = sum(cont.values())
                if feitos % 500 == 0:
                    escreve('Embrapa: %d de %d (ok %d, falhas %d, sem espaco %d)'
                            % (feitos, len(tarefas), cont['ok'], cont['falha'], cont['espaco']))
            time.sleep(0.3)

    ts = [threading.Thread(target=trabalhador) for _ in range(PARALELO)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    escreve('===== Embrapa: fim. ok %d, falhas %d, sem espaco %d =====' % (cont['ok'], cont['falha'], cont['espaco']))


if __name__ == '__main__':
    main()
