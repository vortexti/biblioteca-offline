"""BIBLIOTECA OFFLINE - gera o KIT de instalação a partir da biblioteca atual.

O kit é um .zip pequeno com tudo o que foi feito à mão (o app, os baixadores, o Python portátil, os guias e
os arquivinhos de títulos) + o MANIFESTO: a lista de todos os arquivos da biblioteca com o endereço de onde
cada um foi baixado (tirado das marcas .ok). Descompactando o kit num HD vazio e rodando
INSTALAR_BIBLIOTECA.bat, a biblioteca é refeita igual a esta.

Uso:  python gerar_kit.py   (gera <raiz>\\Kit_Biblioteca_Offline_<data>.zip)
"""
import gzip
import os
import re
import sys
import time
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
BAIXADOR = os.path.dirname(AQUI)
RAIZ = os.path.dirname(BAIXADOR)
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'

FORA_DA_RAIZ = {'$RECYCLE.BIN', 'System Volume Information', '.claude', 'Android', '_PARA_APAGAR',
                'Install Security for Mac.zip', 'Install Security for Windows.exe'}   # não são da biblioteca
CODIGO = ['App', '_Baixador', 'Programas/Python']                          # vai inteiro no kit
ARQUIVOS_RAIZ = ['ABRIR_BIBLIOTECA.bat', 'COMO_ATUALIZAR_E_MELHORAR.txt', 'ESPECIFICACAO_APP.md']
NAO_LEVAR = ('App/dados', '__pycache__', '_Baixador/registro.txt', '_Baixador/atualizacao_estado.json',
             '_Baixador/reserva_downloads.txt', '_Baixador/instalador/instalacao_', '_Baixador/instalador/manifesto')
GERADOS = ('2_Mapas/brasil.pmtiles', '2_Mapas/lugares_br.tsv', '5_Acervo/Religiao/')     # refeitos por script
EXTRAS = [  # o que não veio de um download direto, mas o instalador precisa baixar
    ('6_Instaladores/llama-b11889-bin-win-cpu-x64.zip',
     'https://github.com/ggml-org/llama.cpp/releases/download/b11889/llama-b11889-bin-win-cpu-x64.zip'),
]
ESSENCIAIS_ZIM = ('wikipedia_pt', 'mdwiki', 'ifixit', 'appropedia', 'wikibooks_pt', 'wiktionary_pt', 'medlineplus',
                  'wikem', 'zimgit', 'ready.gov', 'survivorlibrary', 'cd3wd', 'energypedia', 'wikivoyage_pt',
                  'vikidia_pt', 'phet_pt', 'gutenberg_pt', 'wikipedia_en_medicine', 'nhs.uk', 'wwwnc.cdc',
                  'wikipedia_en_knots', 'trueprepper', 'lrnselfreliance', 'armypubs', 'irp.fas.org', 'usda-2015',
                  'quickguidesformedicine', 'wikiversity_pt', 'wikisource_pt', 'wikiquote_pt')


def prioridade(rel):
    r = rel.lower()
    if r.startswith('programas/') or (r.startswith('6_instaladores/') and ('kiwix-tools' in r or 'llama-b' in r)):
        return 1 if not r.startswith('programas/ia/') else 6
    if r.startswith('4_ia/'):
        return 1 if '4b' in r else (4 if '1.7b' in r else 6)
    if r.startswith('5_acervo/') and any(t in r for t in ('/saude/', '/sobrevivencia/', '/agua/', '/desastres/',
                                                           '/eletrica/', '/solda/')):
        return 2
    if r.startswith('1_enciclopedia/'):
        n = r.split('/')[-1]
        if n.startswith(ESSENCIAIS_ZIM):
            return 3
        return 6 if n.startswith('gutenberg_en') else 5
    if r.startswith(('2_mapas/', '6_instaladores/')):
        return 3
    if r.startswith('5_acervo/'):
        return 4
    return 6


def levar_no_kit(rel):
    return not any(rel.startswith(x) or ('/' + x) in rel for x in NAO_LEVAR)


def varrer():
    """(rel, tamanho, url ou None) de cada arquivo da biblioteca."""
    for base, pastas, arqs in os.walk(RAIZ):
        rel_base = os.path.relpath(base, RAIZ).replace('\\', '/')
        rel_base = '' if rel_base == '.' else rel_base + '/'
        if not rel_base:
            pastas[:] = [p for p in pastas if p not in FORA_DA_RAIZ]
        pastas[:] = [p for p in pastas if p != '__pycache__' and not p.startswith('Kit_')]
        nomes = set(arqs)
        for a in arqs:
            if a.endswith(('.ok', '.part', '.blocos', '.montando')) or (not rel_base and a in FORA_DA_RAIZ):
                continue
            if not rel_base and a.startswith('Kit_Biblioteca_Offline'):
                continue
            rel = rel_base + a
            try:
                tam = os.path.getsize(os.path.join(base, a))
            except OSError:
                continue
            url = None
            if a + '.ok' in nomes:
                try:
                    url = open(os.path.join(base, a + '.ok'), encoding='utf-8', errors='replace').readline().strip()
                except OSError:
                    url = ''
            yield rel, tam, url


def mais_novo_por_livro(zims):
    """Se houver duas versões do mesmo ZIM (atualização no meio), fica a mais nova."""
    por_nome = {}
    for rel, tam, url in zims:
        m = re.match(r'^(.+?)_(\d{4}-\d{2})[a-z]?\.zim$', rel.split('/')[-1])
        chave = m.group(1) if m else rel
        if chave not in por_nome or rel > por_nome[chave][0]:
            por_nome[chave] = (rel, tam, url)
    return list(por_nome.values())


def main():
    t0 = time.time()
    print('Mapeando a biblioteca em %s (pode levar uns 15 minutos)…' % RAIZ, flush=True)
    manifesto, zims, kit, sem_origem = [], [], [], []
    for rel, tam, url in varrer():
        topo = rel.split('/')[0]
        if rel in ARQUIVOS_RAIZ or any(rel.startswith(c + '/') for c in CODIGO):
            if levar_no_kit(rel):
                kit.append(rel)
            continue
        if '/' not in rel or rel.startswith(GERADOS):
            continue
        if url and url.startswith('http'):
            url = url.split(' ')[0]
            if rel.startswith('1_Enciclopedia/') and rel.endswith('.zim'):
                zims.append((rel, tam, url))
            elif rel.startswith('Programas/') and not rel.startswith('Programas/IA/'):
                continue   # vem dos .zip de 6_Instaladores
            else:
                manifesto.append((prioridade(rel), 'baixar', rel, tam, url))
        elif topo == 'Programas':
            continue       # extraído dos .zip pelo instalador
        elif tam <= 30 * 2**20:
            kit.append(rel)   # títulos (_indice.txt), LEIA-ME, instruções: vão no kit
        else:
            sem_origem.append((rel, tam))
    for rel, tam, url in mais_novo_por_livro(zims):
        manifesto.append((prioridade(rel), 'zim', rel, tam, url))
    for rel, url in EXTRAS:
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, method='HEAD', headers={'User-Agent': UA}), timeout=60)
            tam = int(r.headers.get('Content-Length') or 0)
        except Exception:
            tam = 0
        manifesto.append((prioridade(rel), 'baixar', rel, tam, url))
    manifesto.sort(key=lambda x: (x[0], x[2]))

    os.makedirs(AQUI, exist_ok=True)
    arq_man = os.path.join(AQUI, 'manifesto.tsv.gz')
    with gzip.open(arq_man, 'wt', encoding='utf-8') as f:
        f.write('# prioridade\ttipo\tcaminho\ttamanho\tendereço  (gerado em %s)\n' % time.strftime('%Y-%m-%d %H:%M'))
        for linha in manifesto:
            f.write('%d\t%s\t%s\t%d\t%s\n' % linha)

    total = sum(x[3] for x in manifesto)
    por = {}
    for p, tipo, rel, tam, url in manifesto:
        k = '/'.join(rel.split('/')[:2]) if rel.startswith('5_Acervo') else rel.split('/')[0]
        n, t = por.get(k, (0, 0))
        por[k] = (n + 1, t + tam)
    conteudo = ['CONTEÚDO DA BIBLIOTECA (o que o instalador baixa)', 'Gerado em %s' % time.strftime('%d/%m/%Y %H:%M'), '']
    conteudo += ['%-45s %7d arquivos %9.1f GB' % (k, n, t / 2**30) for k, (n, t) in sorted(por.items())]
    conteudo += ['', 'TOTAL: %d arquivos, %.1f GB' % (len(manifesto), total / 2**30),
                 '+ gerados na instalação: mapa do Brasil para o PC (~6 GB) e textos religiosos (~13 MB)', '']

    data = time.strftime('%Y-%m-%d')
    saida = os.path.join(RAIZ, 'Kit_Biblioteca_Offline_%s.zip' % data)
    with zipfile.ZipFile(saida + '.part', 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in sorted(kit):
            z.write(os.path.join(RAIZ, *rel.split('/')), rel)
        z.write(arq_man, '_Baixador/instalador/manifesto.tsv.gz')
        z.writestr('CONTEUDO.txt', '\r\n'.join(conteudo))
        for nome in ('LEIA-ME_PRIMEIRO.txt', 'INSTALAR_BIBLIOTECA.bat'):
            z.write(os.path.join(AQUI, 'modelos', nome), nome)
    os.replace(saida + '.part', saida)
    print('\n'.join(conteudo))
    print('Arquivos do kit (código, guias, títulos): %d' % len(kit))
    if sem_origem:
        print('ATENÇÃO, arquivos grandes sem endereço de origem (não entram):')
        for rel, tam in sem_origem:
            print('   %s (%.0f MB)' % (rel, tam / 2**20))
    print('\nKIT: %s (%.1f MB) em %.0f min' % (saida, os.path.getsize(saida) / 2**20, (time.time() - t0) / 60))


if __name__ == '__main__':
    main()
