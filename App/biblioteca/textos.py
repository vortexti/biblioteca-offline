"""Texto de EPUB, HTML, TXT/MD; idioma; títulos; consultas de busca."""
import html
import os
import posixpath
import re
import unicodedata
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree


# ------------------------------------------------------------------ HTML -> texto

class _HtmlTexto(HTMLParser):
    BLOCO = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article',
             'blockquote', 'pre', 'dt', 'dd', 'table', 'ul', 'ol', 'hr', 'figcaption', 'header', 'footer'}
    PULAR = {'script', 'style', 'head', 'nav', 'noscript', 'svg', 'math', 'button', 'form', 'select', 'template'}

    def __init__(self, pular_classes=()):
        super().__init__(convert_charrefs=True)
        self.partes = []
        self.pulando = 0
        self.pilha = []
        self.pular_classes = pular_classes

    def handle_starttag(self, tag, attrs):
        pular = tag in self.PULAR
        if not pular and self.pular_classes and tag not in ('body', 'main', 'html'):
            a = dict(attrs)
            for tok in ((a.get('class') or '') + ' ' + (a.get('id') or '')).split():
                if any(tok == c or tok.startswith(c + '-') for c in self.pular_classes):
                    pular = True
                    break
        if tag in ('br', 'hr', 'img', 'meta', 'link', 'input', 'wbr', 'col', 'area', 'source'):
            if tag in ('br', 'hr') and not self.pulando:
                self.partes.append('\n')
            return
        self.pilha.append((tag, pular))
        if pular:
            self.pulando += 1
        if tag in self.BLOCO and not self.pulando:
            self.partes.append('\n')

    def handle_endtag(self, tag):
        # fecha até achar a tag (HTML mal formado)
        for i in range(len(self.pilha) - 1, -1, -1):
            if self.pilha[i][0] == tag:
                for t, p in self.pilha[i:]:
                    if p:
                        self.pulando -= 1
                del self.pilha[i:]
                break
        if tag in self.BLOCO and not self.pulando:
            self.partes.append('\n')

    def handle_data(self, data):
        if not self.pulando:
            self.partes.append(data)


def html_para_texto(conteudo, pular_classes=()):
    if isinstance(conteudo, bytes):
        conteudo = decodificar_bytes(conteudo)
    p = _HtmlTexto(pular_classes)
    try:
        p.feed(conteudo)
        p.close()
    except Exception:
        pass
    t = ''.join(p.partes)
    t = re.sub(r'[ \t ]+', ' ', t)
    t = re.sub(r' *\n[ \n]*', '\n', t)
    return t.strip()


def decodificar_bytes(b):
    if b[:3] == b'\xef\xbb\xbf':
        return b[3:].decode('utf-8', 'replace')
    if b[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return b.decode('utf-16', 'replace')
    m = re.search(rb'charset=["\']?([\w-]+)', b[:2000]) or re.search(rb'encoding=["\']([\w-]+)', b[:200])
    if m:
        try:
            return b.decode(m.group(1).decode('ascii'), 'replace')
        except LookupError:
            pass
    try:
        return b.decode('utf-8')
    except UnicodeDecodeError:
        return b.decode('cp1252', 'replace')


# ------------------------------------------------------------------ EPUB

def _xml(dados):
    try:
        return ElementTree.fromstring(dados)
    except ElementTree.ParseError:
        # alguns OPF têm entidades HTML
        txt = decodificar_bytes(dados)
        txt = re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#)', '&amp;', txt)
        return ElementTree.fromstring(txt.encode('utf-8'))


def _sem_ns(tag):
    return tag.rsplit('}', 1)[-1]


def extrair_epub(caminho):
    """{'titulo', 'idioma', 'paginas': [texto por capítulo], 'nomes': [arquivo interno]}"""
    with zipfile.ZipFile(caminho) as z:
        nomes = z.namelist()
        opf = None
        try:
            cont = _xml(z.read('META-INF/container.xml'))
            for el in cont.iter():
                if _sem_ns(el.tag) == 'rootfile' and el.get('full-path'):
                    opf = el.get('full-path')
                    break
        except (KeyError, ElementTree.ParseError):
            pass
        if not opf:
            opf = next((n for n in nomes if n.lower().endswith('.opf')), None)
        titulo, idioma, ordem = '', '', []
        if opf:
            raiz = _xml(z.read(opf))
            base = posixpath.dirname(opf)
            manifest = {}
            for el in raiz.iter():
                t = _sem_ns(el.tag)
                if t == 'title' and not titulo and el.text:
                    titulo = el.text.strip()
                elif t == 'language' and not idioma and el.text:
                    idioma = el.text.strip()
                elif t == 'item':
                    manifest[el.get('id')] = (el.get('href') or '', el.get('media-type') or '')
            for el in raiz.iter():
                if _sem_ns(el.tag) == 'itemref':
                    href, tipo = manifest.get(el.get('idref'), ('', ''))
                    if href and ('html' in tipo or href.lower().endswith(('.html', '.htm', '.xhtml'))):
                        from urllib.parse import unquote
                        ordem.append(posixpath.normpath(posixpath.join(base, unquote(href))))
        if not ordem:
            ordem = sorted(n for n in nomes if n.lower().endswith(('.html', '.htm', '.xhtml')))
        paginas, usados = [], []
        conjunto = set(nomes)
        for n in ordem:
            if n not in conjunto:
                continue
            try:
                t = html_para_texto(z.read(n))
            except Exception:
                t = ''
            if len(t.strip()) < 20 and paginas:
                continue
            paginas.append(t)
            usados.append(n)
        return {'titulo': titulo, 'idioma': idioma, 'paginas': paginas, 'nomes': usados}


def capitulos_epub(caminho, maximo=40000):
    """[(nome, texto)] por capítulo, cortando os arquivos internos nas âncoras do sumário (toc.ncx ou nav).
    Sem sumário útil, corta em pedaços de ~maximo caracteres. Capítulos muito longos também são divididos."""
    from urllib.parse import unquote
    with zipfile.ZipFile(caminho) as z:
        nomes = set(z.namelist())
        opf = next((n for n in nomes if n.lower().endswith('.opf')), None)
        try:
            for el in _xml(z.read('META-INF/container.xml')).iter():
                if _sem_ns(el.tag) == 'rootfile' and el.get('full-path'):
                    opf = el.get('full-path')
                    break
        except (KeyError, ElementTree.ParseError):
            pass
        espinha, sumario = [], None
        if opf:
            raiz = _xml(z.read(opf))
            base = posixpath.dirname(opf)
            manifest = {}
            for el in raiz.iter():
                if _sem_ns(el.tag) == 'item':
                    href = posixpath.normpath(posixpath.join(base, unquote(el.get('href') or '')))
                    manifest[el.get('id')] = href
                    tipo, props = el.get('media-type') or '', el.get('properties') or ''
                    if tipo == 'application/x-dtbncx+xml' or ('nav' in props.split() and not sumario):
                        sumario = href
            espinha = [manifest.get(el.get('idref')) for el in raiz.iter() if _sem_ns(el.tag) == 'itemref']
            espinha = [n for n in espinha if n in nomes]
        if not espinha:
            espinha = sorted(n for n in nomes if n.lower().endswith(('.html', '.htm', '.xhtml')))
        marcas = {}   # arquivo -> [(âncora, nome)]
        if sumario and sumario in nomes:
            pasta = posixpath.dirname(sumario)
            try:
                raiz = _xml(z.read(sumario))
                if sumario.lower().endswith('.ncx'):
                    itens = []
                    for np_ in raiz.iter():
                        if _sem_ns(np_.tag) != 'navPoint':
                            continue
                        rotulo = src = ''
                        for f in np_:
                            if _sem_ns(f.tag) == 'navLabel':
                                rotulo = ' '.join(''.join(f.itertext()).split())
                            elif _sem_ns(f.tag) == 'content':
                                src = f.get('src') or ''
                        itens.append((rotulo, src))
                else:
                    itens = [(' '.join(''.join(a.itertext()).split()), a.get('href') or '')
                             for a in raiz.iter() if _sem_ns(a.tag) == 'a']
                for rotulo, src in itens:
                    arq, _, ancora = src.partition('#')
                    arq = posixpath.normpath(posixpath.join(pasta, unquote(arq)))
                    marcas.setdefault(arq, []).append((ancora, rotulo))
            except (KeyError, ElementTree.ParseError):
                marcas = {}
        caps = []   # [nome, [trechos html]]
        for n in espinha:
            bruto = decodificar_bytes(z.read(n))
            cortes = []
            for ancora, rotulo in marcas.get(n, []):
                pos = 0
                if ancora:
                    m = re.search(r'\bid=["\']%s["\']' % re.escape(ancora), bruto)
                    if not m:
                        continue
                    pos = bruto.rfind('<', 0, m.start())
                cortes.append((max(pos, 0), rotulo))
            cortes.sort(key=lambda x: x[0])
            ini = 0
            for pos, rotulo in cortes:
                if pos > ini:
                    if caps:
                        caps[-1][1].append(bruto[ini:pos])
                    else:
                        caps.append(['', [bruto[ini:pos]]])
                    ini = pos
                caps.append([rotulo, []])
            if caps:
                caps[-1][1].append(bruto[ini:])
            else:
                caps.append(['', [bruto[ini:]]])
    saida = []
    pendente = ''
    for nome, partes in caps:
        # quebra de linha dentro do HTML é só espaço (o livro quebra as frases no meio)
        texto = '\n'.join(html_para_texto(re.sub(r'\s*\n\s*', ' ', p)) for p in partes).strip()
        if len(texto) < 40:   # só o título (ex.: "PARTE I"): junta ao próximo
            pendente = (pendente + ' · ' if pendente else '') + (nome or texto)
            continue
        if pendente:
            nome = pendente + (' · ' + nome if nome else '')
            pendente = ''
        nome = nome or next((l.strip() for l in texto.splitlines() if l.strip()), '')
        if len(texto) <= maximo * 1.5:
            saida.append((nome[:90], texto))
            continue
        pedacos, atual = [], ''
        for par in texto.split('\n'):
            if len(atual) + len(par) > maximo and atual:
                pedacos.append(atual)
                atual = ''
            atual += par + '\n'
        if atual.strip():
            pedacos.append(atual)
        for i, t in enumerate(pedacos):
            saida.append(((nome[:80] + (' (continuação %d)' % i if i else ''))
                          or t.strip().splitlines()[0][:80], t))
    return saida


# ------------------------------------------------------------------ TXT / MD / HTML

def extrair_texto_simples(caminho, ext):
    with open(caminho, 'rb') as f:
        b = f.read(30_000_000)
    if ext in ('.html', '.htm'):
        txt = html_para_texto(b)
        m = re.search(rb'<title[^>]*>(.*?)</title>', b[:20000], re.S | re.I)
        titulo = html.unescape(decodificar_bytes(m.group(1))).strip() if m else ''
    else:
        txt = decodificar_bytes(b)
        titulo = ''
        if ext == '.md':
            m = re.search(r'^#\s+(.+)$', txt, re.M)
            titulo = m.group(1).strip() if m else ''
    return {'titulo': titulo, 'paginas': dividir_secoes(txt)}


def dividir_secoes(txt, tamanho=3500):
    """Divide texto longo em 'páginas' de ~3500 caracteres, quebrando em parágrafos."""
    partes, atual, n = [], [], 0
    for par in re.split(r'\n\s*\n', txt):
        if n + len(par) > tamanho and atual:
            partes.append('\n\n'.join(atual))
            atual, n = [], 0
        atual.append(par)
        n += len(par) + 2
    if atual:
        partes.append('\n\n'.join(atual))
    return partes or ['']


# ------------------------------------------------------------------ idioma

_PT = set('de da do das dos que não nao uma para com os as em no na nos nas se por mais como ao ou são sao '
          'também tambem pode ser está esta foi muito quando água agua ele ela isso pelo pela entre'.split())
_EN = set('the and of to in is that for it with as on are be by this from or an which you your can '
          'not have was if at use water should they will'.split())


def idioma(texto):
    pal = re.findall(r'[a-záéíóúâêôãõçà]+', texto[:20000].lower())
    pt = sum(1 for p in pal if p in _PT)
    en = sum(1 for p in pal if p in _EN)
    if pt + en < 8:
        return ''
    return 'pt' if pt >= en else 'en'


def normalizar_idioma(cod):
    cod = (cod or '').lower()
    if cod.startswith(('pt', 'por')):
        return 'pt'
    if cod.startswith(('en', 'eng')):
        return 'en'
    if cod.startswith(('es', 'spa')):
        return 'es'
    return cod[:2]


# ------------------------------------------------------------------ títulos

_RUIM = re.compile(r"^(?:untitled|sem t[ií]tulo|microsoft (?:word|powerpoint).*|documento?|apresenta[cç][aã]o|"
                   r"slide\s*\d*|layout\s*\d*|capa|cover|miolo.*|livro ?miolo.*|e-?book|arquivo|template|pdf|\d+|"
                   r"distribution restriction.*|approved for public release.*|for official use only.*|"
                   r"[\w .-]+\.(?:pdf|docx?|indd|qxd|p65|pmd|cdr|ai|psd|eps|pub|pptx?))$", re.I)


def titulo_generico(t):
    """Título que é claramente metadado de diagramação/distribuição, não o nome da obra."""
    t = (t or '').strip()
    return bool(_RUIM.match(t) or re.search(r'\.(pmd|indd|docx?|qxd|p65)|miolo', t, re.I))


def titulo_bom(t):
    t = (t or '').strip().strip('\x00')
    if len(t) < 4 or len(t) > 250:
        return ''
    t = re.sub(r'^Microsoft (Word|PowerPoint) - ', '', t)
    if _RUIM.match(t) or re.search(r'\.(docx?|indd|pdf|qxd)$', t, re.I):
        return ''
    if sum(c.isalpha() for c in t) < len(t) * 0.5:
        return ''
    if ' ' not in t:  # 'Livromiolo', 'capa_final': metadado de diagramação, não título
        return ''
    return t


def parece_codigo(titulo):
    """'Pt wtnd 2009 14' ou '00079220' não dizem nada ao leitor."""
    reais = [t for t in re.findall(r'[^\W\d_]+', titulo)
             if len(t) >= 4 and sum(c in 'aeiouáéíóúâêôãõà' for c in t.lower()) >= 1]
    return len(reais) < 2


_LICENCA = re.compile(r'licen[cç]a|creative commons|permitida a reprodu|copyright|©|isbn|direitos reservados|'
                      r'vers[aã]o preliminar|tesauro|ficha catalogr', re.I)


def titulo_do_texto(paginas):
    """Primeira linha com cara de título na primeira página com texto."""
    for pag in paginas[:3]:
        for linha in pag.split('\n')[:12]:
            if _CABECALHO.search(linha) or _LICENCA.search(linha) or re.search(r'[^\w\s.,:;()\-–—/\'"ºª°&]', linha):
                continue
            if re.search(r'(\. ?){4,}|…{2,}', linha):
                continue  # linha de sumário ("Capítulo . . . . 74")
            l = re.sub(r'\s+\d+$', '', linha).strip(' .:-–—\t')
            letras = sum(c.isalpha() for c in l)
            if 4 <= len(l) <= 100 and letras >= max(4, len(l) * 0.6):
                if l.isupper():
                    l = l[0] + l[1:].lower()
                return l
    return ''


def titulo_scielo(paginas):
    """Capa padrão do SciELO Livros: 'AUTOR, X., org. Título do livro [online]. Cidade: Editora, ano.'"""
    for pag in paginas[:2]:
        if 'scielo' not in pag[:300].lower() or '[online]' not in pag:
            continue
        txt = re.sub(r'\s+', ' ', pag.split('[online]')[0])
        txt = re.sub(r'^(?:\s*/?\s*SciELO\s+(?:Books|Livros|Libros))+\s*/?\s*', '', txt, flags=re.I).strip()
        m = list(re.finditer(r'\b(?:orgs?|eds?|coords?|organizadores?|editores?)\.\s*', txt, re.I))
        if m:
            titulo = txt[m[-1].end():]
        else:
            # autores em maiúsculas: 'SOBRENOME, N.' (e 'SOBRENOME, N.')*
            titulo = re.sub(r"^(?:[A-ZÀ-Ý][A-ZÀ-Ý'\- ]+,\s*(?:[A-ZÀ-Ý][A-Za-zÀ-ÿ]{0,3}\s*\.\s*)+,?\s*(?:(?:and|e|&)\s+)?)+",
                            '', txt)
        titulo = re.sub(r'\s+:\s*', ': ', titulo).strip(' .,;')
        if 4 <= len(titulo) <= 250:
            return titulo[0].upper() + titulo[1:]
    return ''


_CAPITULAR = re.compile(r'(?<!\w)([^\W\d_]) ([^\W\d_]{2,})')


def juntar_capitulares(t):
    """'P olíticas p úblicas' -> 'Políticas públicas' (letra capitular separada pelo extrator)."""
    def junta(m):
        letra, resto = m.group(1), m.group(2)
        if letra.lower() in 'aeoéàyi' and not re.match(r'[çãõ]', resto):
            return m.group(0)  # 'A casa', 'e depois', 'O problema'
        return letra + resto
    return _CAPITULAR.sub(junta, t or '')


_CABECALHO = re.compile(r'^\W*c\s?h\s?apter\b|^\W*cap[ií]tulo\b|^\W*\d+\W*$|presidente da rep|vice-presidente|ministro d|secret[aá]rio|minist[eé]rio|secretaria|departamento|coordena[cç]|bras[ií]lia|^\W*\d{4}\W*$|s[eé]rie\b|'
                        r'universidade|faculdade|governo|rep[uú]blica|funda[cç][aã]o|edi[cç][aã]o|isbn|venda proibida|'
                        r'distribui[cç][aã]o|[aá]rea t[eé]cnica|instituto|programa nacional|organiza[cç][aã]o|'
                        r'^\W*(DF|RJ|SP)\W*$|reimpress|impresso|editora|^\W*vers[aã]o', re.I)


def titulo_pela_capa(paginas, titulo_arquivo):
    """Título real da capa: primeira linha que não é cabeçalho institucional e que compartilha
    palavras com o nome do arquivo (o nome costuma abreviar o título); junta a continuação."""
    if not paginas:
        return ''
    chaves = {raiz(p) for p in re.findall(r'\w{4,}', sem_acento(titulo_arquivo.lower())) if not p.isdigit()}
    if not chaves:
        return ''
    linhas = [l.strip() for l in paginas[0].split('\n')[:30] if l.strip()]
    if sum(len(l) for l in linhas) < 15 and len(paginas) > 1:
        linhas = [l.strip() for l in paginas[1].split('\n')[:30] if l.strip()]

    def aceitavel(l):
        letras = sum(c.isalpha() for c in l)
        if letras < 3 or letras < len(l) * 0.6 or len(l) > 140 or _CABECALHO.search(l) or l.startswith('('):
            return False
        # palavras quebradas por espaços ("s aú D e") = texto extraído torto
        pal = l.split()
        return sum(1 for p in pal if len(p) == 1 and p.lower() not in ('a', 'e', 'o', 'à')) <= 1

    for i, l in enumerate(linhas):
        if not aceitavel(l):
            continue
        tem = {raiz(p) for p in re.findall(r'\w{4,}', sem_acento(l.lower()))}
        if not (tem & chaves):
            continue
        titulo = l
        for prox in linhas[i + 1:i + 3]:
            if len(titulo) >= 45 or not aceitavel(prox) or len(prox) > 60:
                break
            titulo += ' ' + prox
        titulo = _reparar_palavras(re.sub(r'\s+', ' ', titulo), paginas)
        titulo = re.sub(r'(\s+(e|de|da|do|das|dos|para|em|a|o|,|\(|–|-))+\s*$', '', titulo, flags=re.I).strip(' .:-–,(')
        if titulo.isupper():
            titulo = titulo[0] + titulo[1:].lower()
        return titulo if len(titulo) >= 8 else ''
    return ''


def _reparar_palavras(titulo, paginas):
    """'Ref orma Psiquiátrica' -> 'Reforma Psiquiátrica', quando a palavra inteira aparece no texto."""
    vocab = set(re.findall(r'\w+', sem_acento(' '.join(paginas[:4]).lower())))
    pal = titulo.split(' ')
    i, out = 0, []
    while i < len(pal):
        juntou = False
        for k in (3, 2):   # tenta juntar 3 ou 2 pedaços
            if i + k <= len(pal):
                pedacos = pal[i:i + k]
                if any(len(re.sub(r'\W', '', p)) <= 3 for p in pedacos):
                    junto = ''.join(pedacos)
                    chave = sem_acento(re.sub(r'\W', '', junto).lower())
                    if len(chave) >= 5 and chave in vocab:
                        out.append(junto)
                        i += k
                        juntou = True
                        break
        if not juntou:
            out.append(pal[i])
            i += 1
    return ' '.join(out)


def reparar_interrogacoes(titulo, paginas):
    """'Atendimento Pr?-Hospitalar (APH) B?sico' -> procura no texto 'Atendimento Pr.-Hospitalar...'
    e devolve a grafia correta. Se não achar, devolve '' (o chamador usa outro método)."""
    if '?' not in titulo:
        return titulo
    padrao = re.escape(titulo.strip()).replace(r'\?', r'\w{1,2}')
    padrao = re.sub(r'(\\ )+', r'\\s+', padrao)
    texto = '\n'.join(paginas[:6])
    m = re.search(padrao, texto, re.I)
    if m:
        achado = re.sub(r'\s+', ' ', m.group(0))
        if achado.isupper():
            achado = achado[0] + achado[1:].lower()
            achado = re.sub(r'\(([a-z]{2,6})\)', lambda m: '(' + m.group(1).upper() + ')', achado)
        return achado
    # só o começo (títulos longos às vezes quebram de outro jeito na capa)
    curto = ' '.join(titulo.split()[:4])
    if len(curto) >= 12 and curto != titulo:
        r = reparar_interrogacoes(curto, paginas)
        if r:
            resto = titulo[len(curto):]
            return r + (resto if '?' not in resto else '')
    return ''


def titulo_de_arquivo(nome):
    base = os.path.splitext(nome)[0]
    base = re.sub(r'^\d{3,}_', '', base)          # id da Embrapa
    base = re.sub(r'^\d+-\d+_', '', base)         # id da Biblioteca do Exercito
    base = re.sub(r'^\d{1,2}_', '', base)         # numeração 01_
    base = re.sub(r'[_]+', ' ', base)
    base = re.sub(r'(?<=[a-zà-ú])(?=[A-ZÀ-Ú])', ' ', base)  # CamelCase
    base = re.sub(r'\s+', ' ', base).strip(' -')
    return base[:1].upper() + base[1:] if base else nome


# ------------------------------------------------------------------ consultas FTS5

PARADAS = set('a o as os de da do das dos e em no na nos nas um uma uns umas para pra por com sem que se '
              'como qual quais onde quando é ser ao aos à às ou mais menos muito pelo pela pelos pelas sobre '
              'meu minha seu sua isso isto esse essa este esta fazer faço faz posso pode devo '
              'the of and to in for on with how what is are a an do does can i my by from at it'.split())


def sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


NEGADORES = {'sem', 'nao', 'não', 'nunca', 'without', 'no', 'not', 'except'}


def termos(consulta, tirar_negados=False):
    """Palavras significativas da consulta (sem paradas), em minúsculas.
    tirar_negados: em 'purificar água sem fervura', 'fervura' não é obrigatória."""
    pal = re.findall(r'\w+', consulta.lower())
    if tirar_negados:
        pal = [p for i, p in enumerate(pal) if not (i and pal[i - 1] in NEGADORES)]
    sig = [p for p in pal if p not in PARADAS and sem_acento(p) not in PARADAS and len(p) > 1]
    return sig or [p for p in pal if len(p) > 1] or pal


def raiz(t):
    s = sem_acento(t.lower())
    return s[:max(4, len(s) - 2)] if len(s) > 4 else s


def _radical(p):
    """Radical simples para casar plural/derivações: tratamento -> tratament*, purificar -> purific*."""
    s = sem_acento(p)
    if len(s) <= 4 or s.isdigit():
        return '"%s"' % s
    for suf in ('acoes', 'mente', 'cao', 'coes', 'oes', 'ar', 'er', 'ir', 'es', 'os', 'as', 'ing', 'ed', 's',
                'o', 'a', 'e'):
        if s.endswith(suf) and len(s) - len(suf) >= 4:
            return '"%s"*' % s[:-len(suf)]
    return '"%s"*' % s


def consulta_fts(consulta, modo='and', tirar_negados=False):
    """modo: 'perto' (termos próximos), 'and' (todos) ou 'or' (qualquer um)."""
    ts = termos(consulta, tirar_negados)
    if not ts:
        return ''
    partes = [_radical(t) for t in ts[:12]]
    if modo == 'perto':
        return 'NEAR(%s, 10)' % ' '.join(partes) if len(partes) > 1 else ''
    return (' AND ' if modo == 'and' else ' OR ').join(partes)


_controle = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�]')


def limpar_controle(t):
    return _controle.sub('', t or '')


def trecho_relevante(texto, consulta, tamanho=1400):
    """Escolhe a janela do texto com mais termos da consulta (para o prompt da IA)."""
    if len(texto) <= tamanho:
        return texto
    ts = [raiz(t) for t in termos(consulta, True)]
    baixo = sem_acento(texto.lower())
    melhor, pos = -1, 0
    passo = max(200, tamanho // 4)
    for ini in range(0, max(1, len(texto) - tamanho + passo), passo):
        jan = baixo[ini:ini + tamanho]
        nota = sum(jan.count(t) for t in ts) + 3 * sum(1 for t in ts if t in jan)
        if nota > melhor:
            melhor, pos = nota, ini
    # alinha no começo de frase/parágrafo
    k = texto.rfind('\n', max(0, pos - 150), pos + 1)
    if k > 0:
        pos = k + 1
    return limpar_controle(texto[pos:pos + tamanho]).strip()
