"""BIBLIOTECA OFFLINE - acervo em portugues (PDFs) pelo Internet Archive.

- Livros academicos de acesso aberto do SciELO Livros (Fiocruz, Unesp, UFBA, EdUFSCar...):
  o site oficial bloqueia programas, entao usamos as copias do Internet Archive (Wayback Machine).
- Publicacoes do Ministerio da Saude (bvsms.saude.gov.br): manuais, cadernos de atencao basica,
  guias, protocolos (o site oficial tambem bloqueia programas).

Vao para 5_Acervo\\Livros_SciELO e 5_Acervo\\Saude\\Ministerio_da_Saude, com a marca .ok.
O app indexa sozinho ao abrir (ou em Situacao > Atualizar biblioteca).
Retomavel: pode fechar e rodar de novo.  Uso: BAIXAR_PT.bat  (ou python baixar_pt.py [--verificar])
"""
import os
import re
import shutil
import sys
import threading
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
LOG = os.path.join(AQUI, 'registro.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
PARALELO = 6
RESERVA_GB = int(os.environ.get('BIBLIOTECA_RESERVA_GB') or 40)  # a atualização do app passa 8
CDX = 'http://web.archive.org/cdx/search/cdx?url=%s&filter=mimetype:application/pdf&filter=statuscode:200&collapse=urlkey&fl=original,timestamp,length'

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


def abrir(url, timeout=120):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    return urllib.request.urlopen(req, timeout=timeout)


def lista_cdx(padrao):
    for t in range(6):
        try:
            with abrir(CDX % padrao, timeout=900) as r:
                return [l.split() for l in r.read().decode('utf-8', 'replace').splitlines() if l.strip()]
        except Exception as e:
            escreve('Indice do Internet Archive indisponivel (%s); nova tentativa em %d s' % (e, 60 * (t + 1)))
            time.sleep(60 * (t + 1))
    return []


def nome_seguro(s):
    return re.sub(r'[\\/:*?"<>|\s]+', '_', s)[:150]


def tarefas_scielo():
    livros = {}
    for o, ts, *_ in lista_cdx('books.scielo.org/id/*'):
        m = re.search(r'/id/(\w+)/pdf/([^/]+)\.pdf$', o)
        if not m or re.search(r'-\d{2}$', m.group(2)):   # pula capitulos (livro completo basta)
            continue
        chave = m.group(1)
        if chave not in livros or ts > livros[chave][1]:   # uma edicao por livro, a mais recente
            livros[chave] = (o, ts, m.group(2))
    pasta = os.path.join(RAIZ, '5_Acervo', 'Livros_SciELO')
    return [(o, ts, os.path.join(pasta, nome_seguro('%s_%s.pdf' % (k, n))), 'SciELO ' + n)
            for k, (o, ts, n) in sorted(livros.items())]


def tarefas_bvs():
    vistos = {}
    for o, ts, *_ in lista_cdx('bvsms.saude.gov.br/bvs/publicacoes/*'):
        m = re.search(r'/bvs/publicacoes/(.+\.pdf)$', o, re.I)
        if not m:
            continue
        chave = m.group(1).lower()
        if chave not in vistos or ts > vistos[chave][1]:
            vistos[chave] = (o, ts, m.group(1))
    pasta = os.path.join(RAIZ, '5_Acervo', 'Saude', 'Ministerio_da_Saude')
    return [(o, ts, os.path.join(pasta, nome_seguro(n.replace('/', '_'))), 'Min. Saude ' + n)
            for _, (o, ts, n) in sorted(vistos.items())]


def baixar(original, ts, destino, rotulo):
    if os.path.exists(destino + '.ok'):
        return 'ja'
    if livre_util() < RESERVA_GB * 2**30:
        return 'espaco'
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    url = 'https://web.archive.org/web/%sid_/%s' % (ts, original)
    parcial = destino + '.part'
    for t in range(1, 6):
        try:
            with abrir(url) as r, open(parcial, 'wb') as f:
                while True:
                    bloco = r.read(1 << 16)
                    if not bloco:
                        break
                    f.write(bloco)
            with open(parcial, 'rb') as f:
                ini = f.read(1024)
            if b'%PDF' not in ini:
                os.remove(parcial)
                return 'invalido'
            os.replace(parcial, destino)
            with open(destino + '.ok', 'w', encoding='utf-8') as f:
                f.write(url + '\n')
            return 'ok'
        except urllib.error.HTTPError as e:
            if e.code in (404, 403, 410):
                return 'invalido'
            espera = 120 if e.code == 429 else 20 * t   # 429 = muitos pedidos: espera mais
            time.sleep(espera)
        except Exception:
            time.sleep(20 * t)
    try:
        os.remove(parcial)
    except OSError:
        pass
    return 'falha'


def main():
    escreve('===== Acervo em portugues (SciELO Livros + Ministerio da Saude): inicio =====')
    tarefas = tarefas_scielo() + tarefas_bvs()
    pendentes = [t for t in tarefas if not os.path.exists(t[2] + '.ok')]
    escreve('PDFs na lista: %d; faltando: %d' % (len(tarefas), len(pendentes)))
    if '--verificar' in sys.argv:
        return
    fila = list(pendentes)
    cont = {'ok': 0, 'ja': 0, 'invalido': 0, 'falha': 0, 'espaco': 0}
    lock = threading.Lock()

    def trabalhador():
        while True:
            with lock:
                if not fila:
                    return
                item = fila.pop(0)
            r = baixar(*item)
            with lock:
                cont[r] += 1
                feitos = sum(cont.values())
                if r == 'falha':
                    escreve('Falhou (tente de novo depois): %s' % item[3])
                if feitos % 200 == 0:
                    escreve('Acervo PT: %d de %d (ok %d, invalidos %d, falhas %d)'
                            % (feitos, len(pendentes), cont['ok'], cont['invalido'], cont['falha']))
            time.sleep(1)   # gentileza com o Internet Archive

    ts = [threading.Thread(target=trabalhador) for _ in range(PARALELO)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    escreve('===== Acervo PT: fim. ok %d, invalidos %d, falhas %d ====='
            % (cont['ok'], cont['invalido'], cont['falha']))


if __name__ == '__main__':
    main()
