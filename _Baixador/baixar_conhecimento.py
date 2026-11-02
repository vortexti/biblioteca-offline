"""BIBLIOTECA OFFLINE - baixador da "biblioteca do conhecimento" (ZIMs do Kiwix).

Baixa centenas de GB de conhecimento organizado por prioridade:
  P1 essenciais (livros-texto, medicina, wikis, Stack Exchange tecnicos, Gutenberg de ciencias...)
  P2 grandes (Khan Academy, Survivor Library, CrashCourse, APOD/NASA)
  P3 o que couber (resto do Gutenberg, Stack Overflow)

- Sempre a versao mais nova do catalogo oficial do Kiwix (library.kiwix.org).
- 3 downloads ao mesmo tempo, retomaveis (curl -C -): pode fechar e rodar de novo.
- Nunca deixa o SSD com menos de RESERVA_GB livres (pula o que nao cabe).
- Confere cada ZIM no fim (tamanho do catalogo + cabecalho ZIM) e grava a marca .ok,
  igual ao baixar.ps1. O app (ABRIR_BIBLIOTECA.bat) passa a usar sozinho.

Uso:  BAIXAR_CONHECIMENTO.bat        (ou: python baixar_conhecimento.py [--verificar])
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
DESTINO = os.path.join(RAIZ, '1_Enciclopedia')
LOG = os.path.join(AQUI, 'registro.txt')
RESERVA_GB = 40
RESERVA_P3_GB = 140
PARALELO = 3
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'

# (nome no catalogo, versao preferida ou None, rotulo)
P1 = [
    # --- portugues ---
    ('ifixit_pt_all', None, 'iFixit em portugues (consertos)'),
    ('gutenberg_pt_all', None, 'Projeto Gutenberg em portugues (livros)'),
    ('wikiquote_pt_all', 'maxi', 'Wikiquote PT'),
    ('phet_pt_all', None, 'Simulacoes PhET (fisica, quimica, biologia) PT'),
    ('vikidia_pt_all', 'maxi', 'Vikidia PT (enciclopedia para jovens)'),
    ('pt.stackoverflow.com_mul_all', None, 'Stack Overflow em portugues'),
    ('portuguese.stackexchange.com_mul_all', None, 'Lingua portuguesa (Stack Exchange)'),
    # --- livros-texto universitarios (LibreTexts) ---
    ('libretexts.org_en_bio', None, 'LibreTexts Biologia'),
    ('libretexts.org_en_chem', None, 'LibreTexts Quimica'),
    ('libretexts.org_en_phys', None, 'LibreTexts Fisica'),
    ('libretexts.org_en_med', None, 'LibreTexts Medicina'),
    ('libretexts.org_en_geo', None, 'LibreTexts Geociencias (meteorologia, geologia)'),
    ('libretexts.org_en_eng', None, 'LibreTexts Engenharia'),
    ('libretexts.org_en_math', None, 'LibreTexts Matematica'),
    ('libretexts.org_en_stats', None, 'LibreTexts Estatistica'),
    ('libretexts.org_en_workforce', None, 'LibreTexts Oficios (eletrica, mecanica, construcao)'),
    ('libretexts.org_en_k12', None, 'LibreTexts Ensino fundamental e medio'),
    ('libretexts.org_en_socialsci', None, 'LibreTexts Ciencias sociais'),
    ('libretexts.org_en_biz', None, 'LibreTexts Administracao'),
    ('libretexts.org_en_human', None, 'LibreTexts Humanidades'),
    # --- saude ---
    ('medlineplus.gov_en_all', None, 'MedlinePlus (saude, remedios, exames)'),
    ('mdwiki_en_all', 'maxi', 'MDWiki (medicina) - versao sem videos'),
    ('wwwnc.cdc.gov_en_all', None, 'CDC - saude do viajante e doencas'),
    ('librepathology_en_all', 'maxi', 'Libre Pathology'),
    ('wikivet.net_en_all', 'maxi', 'WikiVet (veterinaria)'),
    ('quickguidesformedicine_en_all', None, 'Guias rapidos de medicina'),
    ('genomicseducation_en_all', None, 'Genetica e genomica (NHS)'),
    ('skin-of-color-society_en_all', None, 'Dermatologia em peles negras e pardas'),
    # --- wikis em ingles ---
    ('wikibooks_en_all', 'maxi', 'Wikibooks EN (livros didaticos)'),
    ('wikiversity_en_all', 'maxi', 'Wikiversity EN'),
    ('wikipedia_en-simple_all', 'maxi', 'Wikipedia em ingles simples'),
    ('wikivoyage_en_all', 'maxi', 'Wikivoyage EN'),
    ('wikiquote_en_all', 'maxi', 'Wikiquote EN'),
    ('wikisource_en_all', 'maxi', 'Wikisource EN (textos originais)'),
    ('vikidia_en_all', 'maxi', 'Vikidia EN'),
    ('phet_en_all', None, 'Simulacoes PhET EN'),
    # --- referencia e educacao ---
    ('theworldfactbook_en_all', None, 'CIA World Factbook (paises)'),
    ('internet-encyclopedia-philosophy_en_all', None, 'Enciclopedia de Filosofia'),
    ('encyclopedie-environnement.org_en_all', None, 'Enciclopedia do Meio Ambiente'),
    ('citizendium.org_en_all', 'maxi', 'Citizendium (enciclopedia revisada)'),
    ('oer4schools_en_all', None, 'OER4Schools (ensino)'),
    ('milneopentextbooks.org_en_all', None, 'Milne Open Textbooks'),
    ('opendatastructures.org_en_all', None, 'Estruturas de dados'),
    ('jeffe.cs.illinois.edu_en_all', None, 'Algoritmos (Jeff Erickson)'),
    ('artofproblemsolving_en_all', 'maxi', 'Art of Problem Solving (matematica)'),
    ('proofwiki.org_en_all', 'maxi', 'ProofWiki (demonstracoes matematicas)'),
    ('openmusictheory.com_en_all', None, 'Teoria musical'),
    ('science-in-the-bath_en_all', None, 'Science in the Bath'),
    ('biologycourses_en_all', None, 'Cursos de biologia'),
    ('gardenology.org_en_all', None, 'Gardenology (plantas)'),
    # --- pratico / sobrevivencia ---
    ('armypubs_en_all', None, 'Publicacoes do Exercito dos EUA (manuais tecnicos)'),
    ('trueprepper.com_en_all', None, 'TruePrepper (preparacao)'),
    ('lrnselfreliance_en_all', None, 'Learning Self-Reliance (videos de autossuficiencia)'),
    ('maps_en_south-america', None, 'Mapa da America do Sul (offline)'),
    # --- Projeto Gutenberg por assunto ---
    ('gutenberg_en_lcc-r', None, 'Gutenberg R - Medicina'),
    ('gutenberg_en_lcc-s', None, 'Gutenberg S - Agricultura'),
    ('gutenberg_en_lcc-t', None, 'Gutenberg T - Tecnologia'),
    ('gutenberg_en_lcc-q', None, 'Gutenberg Q - Ciencias'),
    ('gutenberg_en_lcc-u', None, 'Gutenberg U - Ciencia militar'),
    ('gutenberg_en_lcc-v', None, 'Gutenberg V - Ciencia naval'),
    ('gutenberg_en_lcc-g', None, 'Gutenberg G - Geografia e antropologia'),
    ('gutenberg_en_lcc-l', None, 'Gutenberg L - Educacao'),
]
_SE = ['physics', 'chemistry', 'biology', 'math', 'engineering', 'earthscience', 'astronomy', 'space',
       'aviation', 'bicycles', 'outdoors', 'sustainability', 'medicalsciences', 'psychology', 'fitness',
       'pets', 'crafts', 'arduino', 'raspberrypi', 'robotics', 'dsp', '3dprinting', 'networkengineering',
       'stats', 'cs', 'scicomp', 'history', 'hsm', 'philosophy', 'economics', 'law', 'money', 'parenting',
       'homebrew', 'lifehacks', 'skeptics', 'linguistics', 'english', 'ell', 'gis', 'photo', 'sound',
       'music', 'retrocomputing', 'security', 'crypto', 'ai', 'datascience', 'matheducators', 'academia',
       'travel', 'reverseengineering', 'iot', 'drones',
       'vegetarianism', 'coffee', 'alcohol', 'interpersonal', 'workplace', 'writing', 'electronics']
for s in _SE:
    P1.append((s + '.stackexchange.com_en_all', None, 'Stack Exchange: ' + s))
P1.append(('mathoverflow.net_en_all', None, 'MathOverflow (matematica avancada)'))

P2 = [
    ('survivorlibrary.com_en_all', None, 'Survivor Library (livros antigos de oficios)'),
]

# P3: cultura geral (historia, artes, filosofia, literatura) - so com o espaco que sobrar
# (fora por decisao: Khan Academy, CrashCourse, APOD, Stack Overflow)
P3 = [('gutenberg_en_lcc-' + x, None, 'Gutenberg ' + x.upper()) for x in
      ('d', 'n', 'b', 'h', 'j', 'k', 'c', 'm', 'e', 'f', 'z', 'a',
       'pa', 'pb', 'pc', 'pd', 'pe', 'pf', 'pg', 'ph', 'pj', 'pk', 'pl', 'pm', 'pn', 'pq', 'pt',
       'pr', 'ps', 'pz', 'p')]

LISTA = [(1,) + x for x in P1] + [(2,) + x for x in P2] + [(3,) + x for x in P3]

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


def pagina(url, tentativas=4):
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            ultimo = e
            time.sleep(5 * (t + 1))
    escreve('Nao abriu %s (%s)' % (url, ultimo))
    return ''


def procurar(nome, flavour):
    """Entrada mais nova do catalogo para o nome (e versao) pedidos: (url, tamanho) ou None."""
    x = pagina('https://library.kiwix.org/catalog/v2/entries?count=50&name=' + urllib.parse.quote(nome))
    melhores = []
    for e in re.findall(r'<entry>(.*?)</entry>', x, re.S):
        n = re.search(r'<name>(.*?)</name>', e)
        if not n or n.group(1) != nome:
            continue
        fl = (re.search(r'<flavour>(.*?)</flavour>', e) or [None, ''])[1]
        m = re.search(r'<link[^>]*application/x-zim[^>]*>', e)
        if not m:
            continue
        href = re.search(r'href="([^"]+)"', m.group(0))
        tam = re.search(r'length="(\d+)"', m.group(0))
        if not href:
            continue
        url = href.group(1).replace('.meta4', '')
        ordem = 0 if (flavour and fl == flavour) else (1 if fl in ('maxi', '') else (2 if fl == 'nopic' else 3))
        data = re.search(r'_(\d{4}-\d{2})', url)
        melhores.append((ordem, '' if not data else data.group(1), url, int(tam.group(1)) if tam else 0))
    if not melhores:
        return None
    melhores.sort(key=lambda t: (t[0], [-ord(c) for c in t[1]]))
    # entre a melhor versao, a data mais recente
    alvo = [m for m in melhores if m[0] == melhores[0][0]]
    alvo.sort(key=lambda t: t[1], reverse=True)
    return alvo[0][2], alvo[0][3]


def zim_completo(caminho, esperado=0):
    try:
        tam = os.path.getsize(caminho)
        with open(caminho, 'rb') as f:
            cab = f.read(80)
        if len(cab) < 80 or cab[:4] != b'ZIM\x04':
            return False
        if tam != struct.unpack_from('<Q', cab, 72)[0] + 16:
            return False
        # o catalogo informa o tamanho arredondado em KB: tolera a diferenca
        return not esperado or abs(tam - esperado) < max(2 * 2**20, esperado // 1000)
    except OSError:
        return False


def ja_tem(nome):
    """Ja existe uma versao completa (com .ok) desse livro, de qualquer data/versao?"""
    pref = nome + '_'
    for f in os.listdir(DESTINO):
        if f.startswith(pref) and f.endswith('.zim') and os.path.exists(os.path.join(DESTINO, f + '.ok')):
            resto = f[len(pref):]
            # wikipedia_en_all_maxi_2026-08.zim -> resto 'maxi_2026-08.zim'
            if re.match(r'^((maxi|nopic|mini)_)?\d{4}-\d{2}[a-z]?\.zim$', resto):
                return f
    return None


RESERVAS = os.path.join(AQUI, 'reserva_downloads.txt')


class Planejador:
    """Controla o espaco: so comeca um arquivo se, descontando o que ainda falta gravar dos
    downloads em andamento, sobrar a reserva. Publica esse total em reserva_downloads.txt
    para os outros baixadores (Embrapa, portugues) tambem descontarem."""

    def __init__(self):
        self.ativos = {}
        self.lock = threading.Lock()
        threading.Thread(target=self._publicar, daemon=True).start()

    def pendente(self):
        total = 0
        for dest, esperado in list(self.ativos.items()):
            try:
                feito = os.path.getsize(dest)
            except OSError:
                feito = 0
            total += max(0, esperado - feito)
        return total

    def _publicar(self):
        while True:
            try:
                with open(RESERVAS, 'w') as f:
                    f.write('%d %d' % (self.pendente(), int(time.time())))
            except OSError:
                pass
            time.sleep(20)

    def cabe(self, dest, esperado, prio=1):
        # P3 (resto do Gutenberg, Stack Overflow) so entra se sobrar espaco para o acervo em
        # portugues (Embrapa, Ministerio da Saude, SciELO) e o indice de busca do app
        reserva = RESERVA_GB if prio < 3 else RESERVA_P3_GB
        with self.lock:
            try:
                feito = os.path.getsize(dest)
            except OSError:
                feito = 0
            livre = shutil.disk_usage(RAIZ).free
            if livre - self.pendente() - max(0, esperado - feito) < reserva * 2**30:
                return False
            self.ativos[dest] = esperado
            return True

    def libera(self, dest):
        with self.lock:
            self.ativos.pop(dest, None)


def baixar(url, destino, esperado, rotulo, plano, prio=1):
    feito = os.path.getsize(destino) if os.path.exists(destino) else 0
    if feito and zim_completo(destino, esperado):
        # baixado numa rodada anterior, mas sem a marca .ok
        with open(destino + '.ok', 'w', encoding='utf-8') as f:
            f.write(url + '\n')
        escreve('OK: %s' % rotulo)
        return 'ok'
    if not plano.cabe(destino, esperado, prio):
        escreve('SEM ESPACO (pulei): %s (%.1f GB)' % (rotulo, esperado / 2**30))
        return 'espaco'
    try:
        escreve('Baixando: %s (%.2f GB)' % (rotulo, esperado / 2**30))
        for t in range(1, 8):
            cod = subprocess.call(['curl.exe', '-s', '-S', '-L', '-f', '-C', '-', '--retry', '5', '--retry-delay', '15',
                                   '--connect-timeout', '30', '--speed-limit', '20000', '--speed-time', '120',
                                   '-A', UA, '-o', destino, url],
                                  creationflags=0x08000000 if os.name == 'nt' else 0)
            if zim_completo(destino, esperado):
                with open(destino + '.ok', 'w', encoding='utf-8') as f:
                    f.write(url + '\n')
                escreve('OK: %s' % rotulo)
                return 'ok'
            tam = os.path.getsize(destino) if os.path.exists(destino) else 0
            if esperado and tam > esperado + max(2 * 2**20, esperado // 1000):
                escreve('Arquivo maior que o esperado, recomecando: %s' % rotulo)
                os.remove(destino)
            escreve('Interrompido (curl %s, %.1f/%.1f GB), tentativa %d: %s'
                    % (cod, tam / 2**30, esperado / 2**30, t, rotulo))
            time.sleep(30)
        escreve('NAO CONSEGUI BAIXAR: %s (%s)' % (rotulo, url))
        return 'falha'
    finally:
        plano.libera(destino)


def main():
    global RESERVA_P3_GB
    verificar = '--verificar' in sys.argv
    # depois que a Embrapa e o acervo em portugues terminarem: --p3-reserva 40 aproveita o espaco que sobrou
    if '--p3-reserva' in sys.argv:
        RESERVA_P3_GB = int(sys.argv[sys.argv.index('--p3-reserva') + 1])
    os.makedirs(DESTINO, exist_ok=True)
    escreve('===== Biblioteca do conhecimento: inicio (%d itens) =====' % len(LISTA))
    tarefas = []
    total = 0
    for prio, nome, flavour, rotulo in LISTA:
        existente = ja_tem(nome)
        if existente:
            escreve('Ja baixado: %s (%s)' % (rotulo, existente))
            continue
        achado = procurar(nome, flavour)
        if not achado:
            escreve('Nao achei no catalogo: %s (%s)' % (rotulo, nome))
            continue
        url, tam = achado
        total += tam
        tarefas.append((prio, nome, rotulo, url, tam))
    escreve('Para baixar: %d itens, %.0f GB; livre no SSD: %.0f GB (reserva %d GB)'
            % (len(tarefas), total / 2**30, shutil.disk_usage(RAIZ).free / 2**30, RESERVA_GB))
    if verificar:
        for prio, nome, rotulo, url, tam in tarefas:
            print('P%d %7.2f GB  %s  %s' % (prio, tam / 2**30, rotulo, os.path.basename(url)))
        return
    # ordem: prioridade e, dentro dela, menores primeiro (mais conteudo pronto mais cedo)
    tarefas.sort(key=lambda t: (t[0], t[4]))
    plano = Planejador()
    fila = list(tarefas)
    resultado = {'ok': 0, 'espaco': 0, 'falha': 0}
    lock = threading.Lock()

    def trabalhador():
        while True:
            with lock:
                if not fila:
                    return
                prio, nome, rotulo, url, tam = fila.pop(0)
            dest = os.path.join(DESTINO, os.path.basename(urllib.parse.urlparse(url).path))
            r = baixar(url, dest, tam, rotulo, plano, prio)
            with lock:
                resultado[r] += 1

    ts = [threading.Thread(target=trabalhador) for _ in range(PARALELO)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    escreve('===== Fim: %d baixados, %d sem espaco, %d com falha. Livre: %.0f GB ====='
            % (resultado['ok'], resultado['espaco'], resultado['falha'], shutil.disk_usage(RAIZ).free / 2**30))
    escreve('Abra o app e clique em Situacao > "Atualizar biblioteca" (ou reabra o app).')


if __name__ == '__main__':
    main()
