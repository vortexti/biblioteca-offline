"""BIBLIOTECA OFFLINE - textos religiosos (estante "Religiao" do app).

Cristianismo: Bíblia Livre (atualização da Almeida, licença livre) e Bíblia Portuguesa Mundial (domínio
público, inclui os livros deuterocanônicos da Bíblia católica) - ebible.org.
Judaísmo: Torá em português (tradução de Saymon Pires da Silva, publicada no Sefaria; leitura pessoal),
Tanakh JPS 1917 (inglês, domínio público) e o texto hebraico (Códice de Leningrado) - ebible.org/Sefaria.
Islamismo: Alcorão em português (tradução de Samir El-Hayek) com o texto árabe de cada versículo
(Tanzil.net: uso não comercial, texto sem alterações, citando a fonte).

Vai para 5_Acervo\\Religiao\\<religião>, com marca .ok e _indice.txt (títulos). Retomável.
Uso: python baixar_religiao.py
"""
import html
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
DEST = os.path.join(RAIZ, '5_Acervo', 'Religiao')
LOG = os.path.join(AQUI, 'registro.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'

BIBLIAS = [  # (pasta, arquivo, id do ebible, título)
    ('Cristianismo', 'Biblia_Livre_Almeida.epub', 'porbr2018',
     'Bíblia Sagrada — Bíblia Livre (Almeida atualizada)'),
    ('Cristianismo', 'Biblia_Portuguesa_Mundial_com_deuterocanonicos.epub', 'porbrbsl',
     'Bíblia Sagrada — Bíblia Portuguesa Mundial (com os livros deuterocanônicos/católicos)'),
    ('Judaismo', 'Tanakh_JPS_1917_ingles.epub', 'engjps',
     'Tanakh (Bíblia Hebraica) — tradução judaica JPS 1917, em inglês'),
    ('Judaismo', 'Tanakh_hebraico_Codice_de_Leningrado.epub', 'hboWLC',
     'Tanakh — texto original em hebraico (Códice de Leningrado)'),
]
TORA = [('Genesis', 'Bereshit — Gênesis'), ('Exodus', 'Shemot — Êxodo'), ('Leviticus', 'Vayikra — Levítico'),
        ('Numbers', 'Bamidbar — Números'), ('Deuteronomy', 'Devarim — Deuteronômio')]
VERSAO_TORA = 'Publicado em 5784, Saymon Pires da Silva [pt]'


def escreve(msg):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    print(linha, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')


def abrir(url, tentativas=4):
    for t in range(1, tentativas + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=120) as r:
                return r.read()
        except Exception as e:
            erro = e
            time.sleep(5 * t)
    raise RuntimeError('não abriu %s (%s)' % (url, erro))


def marcar(destino, fonte):
    with open(destino + '.ok', 'w', encoding='utf-8') as f:
        f.write(fonte + '\n')


def indice(pasta, arquivo, titulo):
    """_indice.txt da pasta ('arquivo<TAB>Título'), que o app usa como título."""
    caminho = os.path.join(pasta, '_indice.txt')
    linhas = {}
    if os.path.exists(caminho):
        for l in open(caminho, encoding='utf-8'):
            if '\t' in l:
                a, t = l.rstrip('\n').split('\t', 1)
                linhas[a] = t
    linhas[arquivo] = titulo
    with open(caminho, 'w', encoding='utf-8') as f:
        f.write(''.join('%s\t%s\n' % kv for kv in sorted(linhas.items())))


# ------------------------------------------------------------------ EPUB simples (gerado aqui)

def gravar_epub(destino, titulo, idioma, capitulos, autor='', css=''):
    """capitulos: lista de (título, corpo_xhtml)."""
    uid = 'urn:biblioteca-offline:' + re.sub(r'\W+', '-', os.path.basename(destino))
    tmp = destino + '.part'
    with zipfile.ZipFile(tmp, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype'), 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
                   'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
                   compress_type=zipfile.ZIP_DEFLATED)
        itens, spine, nav = [], [], []
        for i, (tit, corpo) in enumerate(capitulos, 1):
            nome = 'cap%03d.xhtml' % i
            z.writestr('OEBPS/' + nome, '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
                       'xmlns="http://www.w3.org/1999/xhtml" xml:lang="%s"><head><meta charset="utf-8"/><title>%s</title>'
                       '<style>%s</style></head><body><h1>%s</h1>%s</body></html>'
                       % (idioma, html.escape(tit), css, html.escape(tit), corpo), compress_type=zipfile.ZIP_DEFLATED)
            itens.append('<item id="c%d" href="%s" media-type="application/xhtml+xml"/>' % (i, nome))
            spine.append('<itemref idref="c%d"/>' % i)
            nav.append('<li><a href="%s">%s</a></li>' % (nome, html.escape(tit)))
        z.writestr('OEBPS/nav.xhtml', '<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html><html '
                   'xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><meta '
                   'charset="utf-8"/><title>Sumário</title></head><body><nav epub:type="toc"><h1>Sumário</h1><ol>%s'
                   '</ol></nav></body></html>' % ''.join(nav), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr('OEBPS/content.opf', '<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:identifier id="uid">%s</dc:identifier><dc:title>%s</dc:title><dc:language>%s</dc:language>'
                   '<dc:creator>%s</dc:creator><meta property="dcterms:modified">%s</meta></metadata><manifest>'
                   '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>%s</manifest>'
                   '<spine>%s</spine></package>'
                   % (uid, html.escape(titulo), idioma, html.escape(autor), time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                      ''.join(itens), ''.join(spine)), compress_type=zipfile.ZIP_DEFLATED)
    os.replace(tmp, destino)


# ------------------------------------------------------------------ partes

def _texto_html(trecho):
    t = re.sub(r'<span class="verse"[^>]*>\s*(\d+)(?:&#160;|\s)*</span>', r'\n\1. ', trecho)
    t = re.sub(r'<(?:br|/p|/div)[^>]*>', '\n', t)
    t = re.sub(r'<a [^>]*class="notemark".*?</a>|<a [^>]*href="#FN[^>]*>.*?</a>', '', t, flags=re.S)
    t = html.unescape(re.sub(r'<[^>]+>', '', t))
    return [l.strip() for l in t.splitlines() if l.strip()]


def reorganizar_ebible(caminho, titulo, idioma):
    """Os EPUBs do ebible.org trazem um livro inteiro por arquivo: refaz com UMA seção por capítulo
    (a busca acha o versículo certo e o leitor abre no capítulo)."""
    z = zipfile.ZipFile(caminho)
    opf_nome = [n for n in z.namelist() if n.endswith('.opf')][0]
    opf = z.read(opf_nome).decode('utf-8')
    base = os.path.dirname(opf_nome)
    hrefs = dict(re.findall(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf))
    hrefs.update({a: b for b, a in re.findall(r'<item[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf)})
    ordem = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
    caps, sobre = [], []
    for idref in ordem:
        nome = hrefs.get(idref)
        if not nome:
            continue
        s = z.read((base + '/' if base else '') + nome).decode('utf-8', 'replace')
        corpo = s.split('<body', 1)[-1]
        livro = re.search(r"<ul class='tnav'>\s*<li><a href='index\.x?html'>([^<]+)</a>", s)
        # o número do capítulo vem do texto do rótulo (o código do livro pode terminar em dígito: 'R236' = 2Cr 36)
        marcas = list(re.finditer(r"<div class='[a-z]*label' id='[A-Za-z0-9]+_0'>\s*(\d+)\s*</div>", corpo))
        if not livro or not marcas:
            linhas = _texto_html(corpo)
            if linhas and len(sobre) < 3:
                sobre.append('<p>' + '</p><p>'.join(html.escape(l) for l in linhas[:60]) + '</p>')
            continue
        livro = html.unescape(livro.group(1)).strip()
        for i, m in enumerate(marcas):
            fim = marcas[i + 1].start() if i + 1 < len(marcas) else len(corpo)
            linhas = _texto_html(corpo[m.end():fim])
            if linhas:
                caps.append(('%s %s' % (livro, m.group(1)), '<p>' + '</p><p>'.join(html.escape(l) for l in linhas) + '</p>'))
    z.close()
    if len(caps) < 100:
        raise RuntimeError('não consegui separar os capítulos de %s (%d)' % (os.path.basename(caminho), len(caps)))
    if sobre:
        caps.insert(0, ('Sobre esta edição', ''.join(sobre)))
    gravar_epub(caminho, titulo, idioma, caps, '', '.verso{margin:.2em 0}' if idioma != 'he' else
                'body{direction:rtl;text-align:right;font-size:1.25em;font-family:"SBL Hebrew","Ezra SIL","Times New Roman",serif}')
    return len(caps)


def biblias():
    for pasta, arquivo, ident, titulo in BIBLIAS:
        p = os.path.join(DEST, pasta)
        os.makedirs(p, exist_ok=True)
        destino = os.path.join(p, arquivo)
        url = 'https://ebible.org/epub/%s.epub' % ident
        ok = open(destino + '.ok', encoding='utf-8').read() if os.path.exists(destino + '.ok') else ''
        if not ok or not os.path.exists(destino) or "refazer" in ok:
            dados = abrir(url)
            if dados[:2] != b'PK':
                raise RuntimeError('%s não é um EPUB' % url)
            with open(destino, 'wb') as f:
                f.write(dados)
            escreve('Religião: %s (%.1f MB)' % (titulo, len(dados) / 2**20))
            ok = ''
        if 'capitulos' not in ok:
            idioma = {'engjps': 'en', 'hboWLC': 'he'}.get(ident, 'pt')
            n = reorganizar_ebible(destino, titulo, idioma)
            marcar(destino, url + ' (capitulos)')
            escreve('Religião: %s reorganizada em %d capítulos' % (titulo, n))
        indice(p, arquivo, titulo)


def alcorao():
    p = os.path.join(DEST, 'Islamismo')
    os.makedirs(p, exist_ok=True)
    arquivo = 'Alcorao_portugues_e_arabe_El-Hayek.epub'
    destino = os.path.join(p, arquivo)
    titulo = 'Alcorão Sagrado — tradução de Samir El-Hayek, com o texto árabe'
    if not os.path.exists(destino + '.ok'):
        pt = abrir('https://tanzil.net/trans/?transID=pt.elhayek&type=txt-2').decode('utf-8')
        ar = abrir('https://tanzil.net/pub/download/index.php?quranType=uthmani&outType=txt-2&agree=true').decode('utf-8')
        meta = abrir('https://tanzil.net/res/text/metadata/quran-data.js').decode('utf-8')
        nomes = re.findall(r"\[\d+, \d+, \d+, \d+, '([^']*)', \"([^\"]*)\", '([^']*)'", meta)  # árabe, transliterado, inglês

        def versos(txt):
            d, notas = {}, []
            for l in txt.splitlines():
                if l.startswith('#'):
                    notas.append(l.lstrip('# ').strip())
                elif l.count('|') >= 2:
                    s, a, t = l.split('|', 2)
                    d[(int(s), int(a))] = t.strip()
            return d, notas

        vpt, notas_pt = versos(pt)
        var, notas_ar = versos(ar)
        if len(vpt) < 6000 or len(var) < 6000:
            raise RuntimeError('Alcorão incompleto (pt %d, ar %d versículos)' % (len(vpt), len(var)))
        css = ('.ar{direction:rtl;text-align:right;font-size:1.35em;line-height:1.9;margin:.2em 0 1em;'
               'font-family:"Traditional Arabic","Arabic Typesetting","Amiri","Segoe UI",serif}.v{margin:.8em 0 .1em}')
        intro = ('<p>Tradução para o português de <b>Samir El-Hayek</b>, com o texto árabe original (escrita uthmani) '
                 'de cada versículo logo abaixo.</p><p>Fonte: <b>Tanzil.net</b> (tanzil.net). Avisos da fonte, '
                 'reproduzidos como vieram:</p><pre style="white-space:pre-wrap">%s</pre>'
                 % html.escape('\n'.join(notas_pt + [''] + notas_ar)))
        caps = [('Sobre esta edição', intro)]
        for s in range(1, 115):
            nome = ('%s (%s)' % (nomes[s - 1][1], nomes[s - 1][0])) if len(nomes) >= 114 else ''
            corpo = ''.join('<p class="v"><b>%d.</b> %s</p><p class="ar" lang="ar" dir="rtl">%s</p>'
                            % (a, html.escape(vpt.get((s, a), '')), html.escape(var.get((s, a), '')))
                            for a in range(1, 300) if (s, a) in vpt or (s, a) in var)
            caps.append(('Surata %d%s' % (s, ' — ' + nome if nome else ''), corpo))
        gravar_epub(destino, titulo, 'pt', caps, 'Samir El-Hayek (trad.)', css)
        marcar(destino, 'https://tanzil.net/trans/pt.elhayek')
        escreve('Religião: %s (%d versículos)' % (titulo, len(vpt)))
    indice(p, arquivo, titulo)


def tora():
    p = os.path.join(DEST, 'Judaismo')
    os.makedirs(p, exist_ok=True)
    arquivo = 'Tora_portugues_Saymon_Pires_da_Silva.epub'
    destino = os.path.join(p, arquivo)
    titulo = 'Torá (os 5 livros de Moisés) — tradução de Saymon Pires da Silva'
    if not os.path.exists(destino + '.ok'):
        caps = [('Sobre esta edição',
                 '<p>Torá em português, tradução de <b>Saymon Pires da Silva</b> (Chavruta Chats Traduções), '
                 'publicada no <b>Sefaria</b> (sefaria.org). Guardada aqui para leitura pessoal, sem alterações. '
                 'A tradução mantém vários termos em hebraico (por exemplo <i>Elohim</i>, <i>shamayim</i> = céus).</p>'
                 '<p>Os mesmos livros estão também, noutra tradução, no começo das Bíblias desta estante, e em '
                 'inglês (JPS 1917) e hebraico nos outros arquivos desta pasta.</p>')]
        total = 0
        for livro, nome in TORA:
            url = ('https://www.sefaria.org/api/v3/texts/%s?version=%s'
                   % (livro, urllib.parse.quote('portuguese|' + VERSAO_TORA)))
            d = json.loads(abrir(url))
            vs = [v for v in d.get('versions') or [] if v.get('versionTitle') == VERSAO_TORA]
            if not vs:
                raise RuntimeError('Sefaria não devolveu %s em português' % livro)
            for c, versos in enumerate(vs[0]['text'], 1):
                corpo = ''.join('<p><b>%d.</b> %s</p>' % (i, html.escape(html.unescape(re.sub(r'<[^>]+>', '', v))))
                                for i, v in enumerate(versos, 1) if v)
                caps.append(('%s, capítulo %d' % (nome, c), corpo))
                total += len(versos)
            time.sleep(1)
        gravar_epub(destino, titulo, 'pt', caps, 'Saymon Pires da Silva (trad.)')
        marcar(destino, 'https://www.sefaria.org (%s)' % VERSAO_TORA)
        escreve('Religião: %s (%d versículos)' % (titulo, total))
    indice(p, arquivo, titulo)


def leia_me():
    texto = '\r\n'.join([
        'TEXTOS RELIGIOSOS - fontes e licenças',
        '',
        'Cristianismo',
        '  Bíblia Livre (Almeida atualizada): Diego Santos, Mario Sérgio e Marco Teles, licença livre (ebible.org/porbr2018).',
        '  Bíblia Portuguesa Mundial: domínio público, inclui os deuterocanônicos (ebible.org/porbrbsl).',
        'Judaísmo',
        '  Torá em português: tradução de Saymon Pires da Silva, publicada no Sefaria (sefaria.org). Leitura pessoal.',
        '  Tanakh JPS 1917 (inglês): domínio público (ebible.org/engjps).',
        '  Tanakh em hebraico, Códice de Leningrado: domínio público (ebible.org/hboWLC).',
        'Islamismo',
        '  Alcorão, tradução de Samir El-Hayek, e texto árabe: Tanzil.net. Uso não comercial, sem alterações, citando a fonte.',
        '',
        'Baixados por _Baixador\\baixar_religiao.py.', ''])
    with open(os.path.join(DEST, 'LEIA-ME.txt'), 'w', encoding='utf-8-sig') as f:
        f.write(texto)
    indice(DEST, 'LEIA-ME.txt', 'Textos religiosos — fontes e licenças')


if __name__ == '__main__':
    escreve('===== Textos religiosos: início =====')
    os.makedirs(DEST, exist_ok=True)
    for parte in (biblias, alcorao, tora):
        try:
            parte()
        except Exception as e:
            escreve('Religião: falhou %s: %s' % (parte.__name__, e))
    leia_me()
    escreve('===== Textos religiosos: fim. Livre: %.0f GB =====' % (shutil.disk_usage(RAIZ).free / 2**30))
