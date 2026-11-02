"""BIBLIOTECA OFFLINE - manuais do Exercito Brasileiro (Biblioteca Digital do Exercito, BDEx).

Coleta o catalogo da BDEx (bdex.eb.mil.br) via OAI-PMH e baixa os MANUAIS e CADERNOS DE
INSTRUCAO de interesse pratico (primeiros socorros, sobrevivencia, topografia e orientacao,
comunicacoes, engenharia e construcao, logistica, saude...), em portugues.
Ficam de fora monografias, boletins, apresentacoes e manuais de armamento/explosivos.

Vao para 5_Acervo\\Exercito_Brasileiro, com marca .ok e _indice.txt ("nome<TAB>Titulo").
Retomavel.  Uso: BAIXAR_EXERCITO.bat   (ou python baixar_exercito.py [--verificar])
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
DEST = os.path.join(RAIZ, '5_Acervo', 'Exercito_Brasileiro')
INDICE = os.path.join(DEST, '_indice.txt')
CATALOGO = os.path.join(AQUI, 'exercito_catalogo.json')
LOG = os.path.join(AQUI, 'registro.txt')
OAI = 'https://bdex.eb.mil.br/oai/request'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
PARALELO = 4
RESERVA_GB = int(os.environ.get('BIBLIOTECA_RESERVA_GB') or 20)  # a atualização do app passa 8
TAM_MAX = 300 * 2**20

# o que e manual/caderno de instrucao (pelo tipo ou pelo titulo)
EH_MANUAL = re.compile(r'manual|caderno de instru|instru[cç][aã]o (geral|provis)|vade-m[eé]cum|'
                       r'\b(C|CI|IP|EB\d{2}-(MC|MT|CI|ME|IR))\s?[\d-]', re.I)
# fora: armamento, municao, explosivos, combate, doutrina interna e burocracia
EXCLUIR = re.compile(r'explosiv|destrui[cç]|demoli|\bminas?\b|minagem|muni[cç]|armamento|\bfuzil|metralhad|'
                     r'pistola|morteiro|obuseiro|canh[aã]o|m[ií]ssil|foguete|granada|tiro\b|de tiro|atirador|'
                     r'artilharia|blindad|carro de combate|guerra eletr|intelig[eê]ncia|contra-?intelig|'
                     r'opera[cç][oõ]es (psicol|de informa)|controle de dist[uú]rbio|boletim|portaria|'
                     r'regulamento interno|cerimonial|contin[eê]ncia|uniforme', re.I)

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
    livre = shutil.disk_usage(RAIZ).free
    try:
        pend, ts = open(os.path.join(AQUI, 'reserva_downloads.txt')).read().split()
        if time.time() - int(ts) < 180:
            livre -= int(pend)
    except (OSError, ValueError):
        pass
    return livre


def pagina(url, tentativas=6):
    erro = None
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
        url = (OAI + '?verb=ListRecords&resumptionToken=' + urllib.parse.quote(estado['token']) if estado['token']
               else OAI + '?verb=ListRecords&metadataPrefix=xoai')
        if estado.get('desde') and not estado['token']:  # atualizacao: so o que entrou depois da ultima coleta
            url += '&from=' + estado['desde']
        x = pagina(url)
        for rec in re.findall(r'<record>(.*?)</record>', x, re.S):
            m = re.search(r'<identifier>oai:[^<]*?(\d+/\d+)</identifier>', rec)
            if not m or 'status="deleted"' in rec:
                continue
            tit = re.search(r'<element name="title">.*?<field name="value">([^<]*)', rec, re.S)
            tipo = re.search(r'<element name="type">.*?<field name="value">([^<]*)', rec, re.S)
            pdfs = []
            for b in re.findall(r'<element name="bitstream">(.*?)</element>', rec, re.S):
                u = re.search(r'<field name="url">([^<]*)', b)
                fmt = re.search(r'<field name="format">([^<]*)', b)
                tam = re.search(r'<field name="size">(\d+)', b)
                if u and fmt and fmt.group(1) == 'application/pdf':
                    pdfs.append([unescape(u.group(1)), int(tam.group(1)) if tam else 0])
            if pdfs:
                estado['itens'][m.group(1)] = {'t': unescape(tit.group(1)).strip() if tit else '',
                                              'tipo': unescape(tipo.group(1)) if tipo else '', 'pdfs': pdfs}
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
            escreve('Exercito: catalogo com %d documentos...' % len(estado['itens']))
        if not estado['token']:
            return estado['itens']
        time.sleep(0.5)


# organização de tropa (brigada, batalhão...) e doutrina de comando: não ensinam a fazer nada prático
ORGANIZACAO = re.compile(r'brigada|batalh|regimento|companhia|divis[aã]o de ex|estado-maior|grupamento|'
                         r'comando de|subunidade|for[cç]a terrestre componente|planejamento estrat|mobiliza[cç]|'
                         r'emprego d[aoe]s? |assuntos civis|espa[cç]o a[eé]reo', re.I)


def _sem_acento(s):
    import unicodedata
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn').lower()


# qualquer tipo de documento (artigo, monografia...) sobre o que interessa num cenário de sobrevivência
TEMAS_UTEIS = re.compile(r'sobreviv|primeiros socorros|socorrismo|pre-hospitalar|\baph\b|ambiente de selva|na selva|'
                         r'caatinga|pantanal|animais pe[cç]onhent|ofidi|serpente|hipotermia|insola[cç]|desidrata|'
                         r'purifica[cç][aã]o d[ae] agua|potabiliza|tratamento de agua|orienta[cç][aã]o terrestre|'
                         r'navega[cç][aã]o terrestre|bussola|topografia|cartografia|abrigo|resgate|salvamento|busca e salvamento|'
                         r'evacua[cç][aã]o aeromedica|desastre|calamidade|defesa civil|plantas medicinais|alimenta[cç][aã]o em campanha|'
                         r'racao operacional|higiene|saneamento|meteorolog|nos e amarra', re.I)
NAO_UTEIS = re.compile(r'gest[aã]o hospitalar|hotelaria|prontu[aá]rio|acredita|glosa|cadeia de suprimento|'
                       r'licita|contrato|or[cç]ament|custos', re.I)


def tema_util(titulo):
    for letra in 'aeiouc':
        t = _sem_acento(titulo).replace('?', letra)
        if TEMAS_UTEIS.search(t) and not NAO_UTEIS.search(t):
            return True
    return False


def excluido(titulo):
    """Armamento, municao, explosivos, combate e organizacao de tropa: fora."""
    # o servidor da BDEx entrega os acentos como '?': testa as letras mais provaveis no lugar
    for letra in 'aeiouc':
        t = _sem_acento(titulo).replace('?', letra)
        if EXCLUIR.search(t) or re.search(r'servico da peca|\d+\s?mm|carros? de combate|radar|helicoptero|'
                                          r'antiaere|tatic|combate urbano|blindad|forcas? de paz|monusco|'
                                          r'formacao do futuro oficial|sexo feminino|astros', t):
            return True
        if ORGANIZACAO.search(t) and 'saude' not in t:
            return True
    return False


def selecionado(it):
    if excluido(it['t']):
        return False
    if tema_util(it['t']):
        return True
    tipo = _sem_acento(it['tipo'])
    oficial = tipo.startswith(('manual', 'caderno de instru')) or (tipo.startswith('instru') and 'provis' in tipo)
    # sem tipo definido: aceita pelo titulo so se tiver cara de manual oficial (codigo C 21-74, EB70-MC...)
    pelo_titulo = tipo in ('', 'outro', 'livro') and EH_MANUAL.search(it['t'])
    return bool(oficial or pelo_titulo)


def nome_arquivo(chave, url):
    folha = urllib.parse.unquote(url.rsplit('/', 1)[-1].split('?')[0])
    return re.sub(r'[\\/:*?"<>|]', '_', '%s_%s' % (chave.replace('/', '-'), folha))[:180]


def baixar(url, destino):
    parcial = destino + '.part'
    for t in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
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
            if e.code in (401, 403, 404, 410):
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
    escreve('===== Exercito Brasileiro (manuais): inicio =====')
    itens = coletar()
    prontos = {e.name[:-3] for e in os.scandir(DEST) if e.name.endswith('.ok')}
    tarefas = []
    for chave, it in itens.items():
        if not selecionado(it):
            continue
        for url, tam in it['pdfs']:
            if tam > TAM_MAX:
                continue
            nome = nome_arquivo(chave, url)
            if nome not in prontos:
                tarefas.append((chave, url, tam, nome, it['t']))
    escreve('Exercito: %d documentos no catalogo; %d PDFs de manuais a baixar (%.1f GB)'
            % (len(itens), len(tarefas), sum(t[2] for t in tarefas) / 2**30))
    if '--verificar' in sys.argv:
        tipos = {}
        for it in itens.values():
            tipos[it['tipo']] = tipos.get(it['tipo'], 0) + 1
        print('Tipos:', sorted(tipos.items(), key=lambda x: -x[1])[:25])
        for t in sorted(tarefas, key=lambda t: t[4])[:: max(1, len(tarefas) // 40)]:
            print('   %6.1f MB  %s' % (t[2] / 2**20, t[4][:100]))
        return
    tarefas.sort(key=lambda t: t[2])
    fila = list(tarefas)
    cont = {'ok': 0, 'falha': 0, 'espaco': 0}
    lock = threading.Lock()

    def trabalhador():
        while True:
            with lock:
                if not fila:
                    return
                chave, url, tam, nome, titulo = fila.pop(0)
            if livre_util() - tam < RESERVA_GB * 2**30:
                with lock:
                    cont['espaco'] += 1
                continue
            ok = baixar(url, os.path.join(DEST, nome))
            with lock:
                cont['ok' if ok else 'falha'] += 1
                if ok:  # titulos com '?' (acentos perdidos no servidor) sao reparados pelo indexador
                    with open(INDICE, 'a', encoding='utf-8') as f:
                        f.write('%s\t%s\n' % (nome, titulo.rstrip('.')))
                if sum(cont.values()) % 100 == 0:
                    escreve('Exercito: %d de %d (ok %d, falhas %d, sem espaco %d)'
                            % (sum(cont.values()), len(tarefas), cont['ok'], cont['falha'], cont['espaco']))
            time.sleep(0.3)

    ts = [threading.Thread(target=trabalhador) for _ in range(PARALELO)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    escreve('===== Exercito: fim. ok %d, falhas %d, sem espaco %d =====' % (cont['ok'], cont['falha'], cont['espaco']))


if __name__ == '__main__':
    main()
