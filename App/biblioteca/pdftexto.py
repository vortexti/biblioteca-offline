"""Extrator de texto de PDF em Python puro (só biblioteca padrão).

Não é um leitor de PDF completo: o objetivo é tirar texto pesquisável,
página por página, da maioria dos manuais. Se existir `pypdf` em
App\\vendor, ele é usado no lugar (ver `extrair_paginas`).

Limitações conhecidas (documentadas no LEIA-ME):
- PDFs criptografados com AES não são lidos (marcados como "protegido").
- PDFs escaneados (só imagem) não têm texto: ficam como "sem texto pesquisável".
"""
import re
import zlib
import unicodedata

# ---------------------------------------------------------------- léxico

ESPACO = b' \t\r\n\x0c\x00'
DELIM = b'()<>[]{}/%'
_ws_re = re.compile(rb'[ \t\r\n\x0c\x00]*')


class PDFErro(Exception):
    pass


class Ref:
    __slots__ = ('num', 'ger')

    def __init__(self, num, ger):
        self.num, self.ger = num, ger

    def __repr__(self):
        return '%d %d R' % (self.num, self.ger)


class Nome(str):
    """Nome PDF (/Font)."""


class Operador(str):
    """Operador de content stream (Tj, BT...)."""


class Stream:
    __slots__ = ('dic', 'dados', 'doc', 'num', 'ger')

    def __init__(self, dic, dados, doc):
        self.dic, self.dados, self.doc = dic, dados, doc
        self.num = self.ger = None

    def decodificar(self):
        dados = self.dados
        cifra = getattr(self.doc, 'cifra', None) if self.doc is not None else None
        if cifra is not None and self.num is not None and self.dic.get('Type') != 'XRef':
            dados = cifra.decifrar(self.num, self.ger or 0, dados)
        return decodificar_stream(self.dic, dados, self.doc)


_num_re = re.compile(rb'[+-]?(?:\d+\.?\d*|\.\d+)')
_nome_re = re.compile(rb'/([^ \t\r\n\x0c\x00()<>\[\]{}/%]*)')
_kw_re = re.compile(rb'[^ \t\r\n\x0c\x00()<>\[\]{}/%]+')


def _pular_espacos(d, i):
    n = len(d)
    while i < n:
        c = d[i]
        if c in ESPACO:
            i += 1
        elif c == 0x25:  # % comentário
            j = d.find(b'\n', i)
            k = d.find(b'\r', i)
            fim = min(x for x in (j, k, n) if x >= 0) if (j >= 0 or k >= 0) else n
            i = fim
        else:
            break
    return i


def _nome(b):
    if b'#' in b:
        b = re.sub(rb'#([0-9A-Fa-f]{2})', lambda m: bytes([int(m.group(1), 16)]), b)
    return Nome(b.decode('latin-1'))


def _string_literal(d, i):
    # d[i] == '('
    i += 1
    prof = 1
    out = bytearray()
    n = len(d)
    while i < n:
        c = d[i]
        if c == 0x5C:  # barra invertida
            i += 1
            if i >= n:
                break
            c = d[i]
            if c in b'nrtbf':
                out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[c])
                i += 1
            elif 0x30 <= c <= 0x37:
                j = i
                while j < n and j < i + 3 and 0x30 <= d[j] <= 0x37:
                    j += 1
                out.append(int(d[i:j], 8) & 0xFF)
                i = j
            elif c == 0x0D:
                i += 1
                if i < n and d[i] == 0x0A:
                    i += 1
            elif c == 0x0A:
                i += 1
            else:
                out.append(c)
                i += 1
        elif c == 0x28:
            prof += 1
            out.append(c)
            i += 1
        elif c == 0x29:
            prof -= 1
            i += 1
            if prof == 0:
                break
            out.append(c)
        else:
            out.append(c)
            i += 1
    return bytes(out), i


def _string_hex(d, i):
    j = d.find(b'>', i)
    if j < 0:
        j = len(d)
    h = re.sub(rb'[^0-9A-Fa-f]', b'', d[i + 1:j])
    if len(h) % 2:
        h += b'0'
    return bytes.fromhex(h.decode('ascii')), j + 1


class Lexer:
    """Lê objetos PDF a partir de bytes. Usado tanto para objetos quanto para content streams."""

    def __init__(self, dados, pos=0, doc=None):
        self.d = dados
        self.i = pos
        self.doc = doc

    def token(self):
        d = self.d
        i = _pular_espacos(d, self.i)
        n = len(d)
        if i >= n:
            self.i = i
            return None
        c = d[i]
        if c == 0x2F:  # /
            m = _nome_re.match(d, i)
            self.i = m.end()
            return _nome(m.group(1))
        if c == 0x28:
            s, self.i = _string_literal(d, i)
            return s
        if c == 0x3C:
            if i + 1 < n and d[i + 1] == 0x3C:
                self.i = i + 2
                return '<<'
            s, self.i = _string_hex(d, i)
            return s
        if c == 0x3E:
            if i + 1 < n and d[i + 1] == 0x3E:
                self.i = i + 2
                return '>>'
            self.i = i + 1
            return Operador('>')
        if c == 0x5B:
            self.i = i + 1
            return '['
        if c == 0x5D:
            self.i = i + 1
            return ']'
        if c == 0x7B or c == 0x7D:
            self.i = i + 1
            return Operador(chr(c))
        if c == 0x29:  # ')' solto
            self.i = i + 1
            return Operador(')')
        m = _num_re.match(d, i)
        if m and (m.end() >= n or d[m.end()] in ESPACO or d[m.end()] in DELIM):
            self.i = m.end()
            t = m.group(0)
            if b'.' in t:
                try:
                    return float(t)
                except ValueError:
                    return 0.0
            return int(t)
        m = _kw_re.match(d, i)
        if not m:
            self.i = i + 1
            return Operador(chr(c))
        self.i = m.end()
        w = m.group(0)
        if w == b'true':
            return True
        if w == b'false':
            return False
        if w == b'null':
            return None
        return Operador(w.decode('latin-1'))

    def objeto(self, tok=Ellipsis):
        """Lê um objeto completo (dicionário, array, ref...)."""
        if tok is Ellipsis:
            tok = self.token()
        if tok == '<<':
            dic = {}
            while True:
                k = self.token()
                if k == '>>' or k is None and self.i >= len(self.d):
                    break
                if not isinstance(k, Nome):
                    continue
                v = self.objeto()
                dic[k] = v
            # stream?
            j = _pular_espacos(self.d, self.i)
            if self.d.startswith(b'stream', j):
                j += 6
                if self.d[j:j + 2] == b'\r\n':
                    j += 2
                elif self.d[j:j + 1] in (b'\n', b'\r'):
                    j += 1
                comp = dic.get('Length')
                if isinstance(comp, Ref) and self.doc is not None:
                    try:
                        comp = self.doc.resolver(comp)
                    except Exception:
                        comp = None
                fim = None
                if isinstance(comp, int) and comp >= 0:
                    k = j + comp
                    if self.d[k:k + 30].lstrip().startswith(b'endstream'):
                        fim = k
                if fim is None:
                    k = self.d.find(b'endstream', j)
                    fim = k if k >= 0 else len(self.d)
                    # remove EOL antes de endstream
                    if fim > j and self.d[fim - 1:fim] == b'\n':
                        fim -= 1
                        if fim > j and self.d[fim - 1:fim] == b'\r':
                            fim -= 1
                    elif fim > j and self.d[fim - 1:fim] == b'\r':
                        fim -= 1
                dados = self.d[j:fim]
                k = self.d.find(b'endstream', fim)
                self.i = (k + 9) if k >= 0 else len(self.d)
                return Stream(dic, dados, self.doc)
            return dic
        if tok == '[':
            arr = []
            while True:
                t = self.token()
                if t == ']' or (t is None and self.i >= len(self.d)):
                    break
                arr.append(self.objeto(t))
            return self._refs(arr)
        if isinstance(tok, int) and not isinstance(tok, bool):
            # pode ser "n g R"
            salva = self.i
            t2 = self.token()
            if isinstance(t2, int) and not isinstance(t2, bool):
                t3 = self.token()
                if t3 == 'R':
                    return Ref(tok, t2)
            self.i = salva
            return tok
        return tok

    @staticmethod
    def _refs(arr):
        return arr


# ------------------------------------------------------------ filtros

def _ascii85(d):
    d = re.sub(rb'\s', b'', d)
    if d.startswith(b'<~'):
        d = d[2:]
    k = d.find(b'~>')
    if k >= 0:
        d = d[:k]
    out = bytearray()
    grupo = []
    for c in d:
        if c == 0x7A and not grupo:
            out += b'\0\0\0\0'
            continue
        if c < 33 or c > 117:
            continue
        grupo.append(c - 33)
        if len(grupo) == 5:
            v = 0
            for x in grupo:
                v = v * 85 + x
            out += (v & 0xFFFFFFFF).to_bytes(4, 'big')
            grupo = []
    if grupo:
        n = len(grupo)
        grupo += [84] * (5 - n)
        v = 0
        for x in grupo:
            v = v * 85 + x
        out += (v & 0xFFFFFFFF).to_bytes(4, 'big')[:n - 1]
    return bytes(out)


def _lzw(d):
    out = bytearray()
    tab = [bytes([i]) for i in range(256)] + [b'', b'']
    bits, buf, nb = 9, 0, 0
    ant = None
    for c in d:
        buf = (buf << 8) | c
        nb += 8
        while nb >= bits:
            nb -= bits
            cod = (buf >> nb) & ((1 << bits) - 1)
            if cod == 256:
                tab = tab[:258]
                bits = 9
                ant = None
                continue
            if cod == 257:
                return bytes(out)
            if ant is None:
                ent = tab[cod] if cod < len(tab) else b''
            elif cod < len(tab):
                ent = tab[cod]
                tab.append(ant + ent[:1])
            else:
                ent = ant + ant[:1]
                tab.append(ent)
            out += ent
            ant = ent
            if len(tab) >= (1 << bits) - 1 and bits < 12:
                bits += 1
    return bytes(out)


def _flate(d):
    try:
        return zlib.decompress(d)
    except zlib.error:
        pass
    # stream truncado ou com lixo: pega o que der
    for w in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
        try:
            o = zlib.decompressobj(w)
            return o.decompress(d)
        except zlib.error:
            continue
    return b''


def _preditor(d, parms):
    if not isinstance(parms, dict):
        return d
    p = parms.get('Predictor', 1)
    if not isinstance(p, int) or p < 10:
        return d
    cols = parms.get('Columns', 1) or 1
    cores = parms.get('Colors', 1) or 1
    bpc = parms.get('BitsPerComponent', 8) or 8
    bpp = max(1, cores * bpc // 8)
    lin = cols * cores * bpc // 8
    out = bytearray()
    ant = bytearray(lin)
    i = 0
    while i + 1 + lin <= len(d) + 0:
        t = d[i]
        row = bytearray(d[i + 1:i + 1 + lin])
        if t == 1:
            for k in range(bpp, lin):
                row[k] = (row[k] + row[k - bpp]) & 255
        elif t == 2:
            for k in range(lin):
                row[k] = (row[k] + ant[k]) & 255
        elif t == 3:
            for k in range(lin):
                a = row[k - bpp] if k >= bpp else 0
                row[k] = (row[k] + ((a + ant[k]) >> 1)) & 255
        elif t == 4:
            for k in range(lin):
                a = row[k - bpp] if k >= bpp else 0
                b = ant[k]
                c = ant[k - bpp] if k >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                row[k] = (row[k] + pr) & 255
        out += row
        ant = row
        i += 1 + lin
    return bytes(out)


def decodificar_stream(dic, dados, doc=None):
    filtros = dic.get('Filter')
    parms = dic.get('DecodeParms')
    if doc is not None:
        filtros = doc.resolver(filtros)
        parms = doc.resolver(parms)
    if filtros is None:
        return dados
    if not isinstance(filtros, list):
        filtros = [filtros]
    if not isinstance(parms, list):
        parms = [parms] * len(filtros)
    for f, p in zip(filtros, parms):
        if doc is not None:
            f = doc.resolver(f)
            p = doc.resolver(p)
        if f in ('FlateDecode', 'Fl'):
            dados = _preditor(_flate(dados), p)
        elif f in ('ASCIIHexDecode', 'AHx'):
            dados, _ = _string_hex(dados.replace(b'>', b'') + b'>', -1)
        elif f in ('ASCII85Decode', 'A85'):
            dados = _ascii85(dados)
        elif f in ('LZWDecode', 'LZW'):
            dados = _preditor(_lzw(dados), p)
        elif f in ('RunLengthDecode', 'RL'):
            out = bytearray()
            i = 0
            while i < len(dados):
                n = dados[i]
                if n == 128:
                    break
                if n < 128:
                    out += dados[i + 1:i + 2 + n]
                    i += n + 2
                else:
                    out += dados[i + 1:i + 2] * (257 - n)
                    i += 2
            dados = bytes(out)
        else:
            # imagem (DCT, JBIG2, CCITT...) ou desconhecido: não é texto
            raise PDFErro('filtro ' + str(f))
    return dados


# ------------------------------------------------------------ documento

_obj_re = re.compile(rb'(?<![0-9])(\d{1,8})[ \t\r\n\x0c\x00]+(\d{1,5})[ \t\r\n\x0c\x00]+obj\b')


class Documento:
    def __init__(self, dados):
        if not dados.lstrip()[:8].startswith(b'%PDF') and b'%PDF' not in dados[:1024]:
            raise PDFErro('não é PDF')
        self.d = dados
        self.offsets = {}     # num -> offset no arquivo
        self.gens = {}        # num -> geracao
        self.cifra = None
        self.em_objstm = {}   # num -> (num_objstm, indice)
        self.cache = {}
        self._objstm_cache = {}
        for m in _obj_re.finditer(dados):
            self.offsets[int(m.group(1))] = m.end()   # última definição vence (atualizações incrementais)
            self.gens[int(m.group(1))] = int(m.group(2))
        self.trailer = self._achar_trailer()
        self.criptografado = 'Encrypt' in self.trailer
        if self.criptografado:
            self._preparar_cifra()
        self._indexar_objstm()

    def _preparar_cifra(self):
        """PDF protegido com senha de abertura vazia (só permissões): dá para ler."""
        try:
            from . import pdfcripto
        except ImportError:
            import pdfcripto
        try:
            enc = self.resolver(self.trailer.get('Encrypt'))
            ids = self.resolver(self.trailer.get('ID'))
            id0 = self.resolver(ids[0]) if isinstance(ids, list) and ids else b''
            if isinstance(enc, dict):
                c = pdfcripto.Cifra(enc, id0 if isinstance(id0, bytes) else b'', self.resolver)
                if c.ok:
                    self.cifra = c
        except Exception:
            self.cifra = None

    def _achar_trailer(self):
        tr = {}
        for m in re.finditer(rb'trailer[ \t\r\n\x0c\x00]*<<', self.d):
            try:
                t = Lexer(self.d, m.end() - 2, self).objeto()
                if isinstance(t, dict):
                    tr.update(t)
            except Exception:
                pass
        # PDFs modernos: trailer dentro do XRef stream
        for num, off in list(self.offsets.items()):
            pass
        for m in re.finditer(rb'/Type[ \t\r\n\x0c\x00]*/XRef\b', self.d):
            ini = self.d.rfind(b'obj', 0, m.start())
            if ini < 0:
                continue
            try:
                o = Lexer(self.d, ini + 3, self).objeto()
                if isinstance(o, Stream):
                    for k in ('Root', 'Info', 'Encrypt', 'ID'):
                        if k in o.dic:
                            tr[k] = o.dic[k]
            except Exception:
                pass
        return tr

    def _indexar_objstm(self):
        for m in re.finditer(rb'/Type[ \t\r\n\x0c\x00]*/ObjStm\b', self.d):
            ini = self.d.rfind(b'obj', 0, m.start())
            mm = None
            for mm in _obj_re.finditer(self.d, max(0, ini - 30), ini + 3):
                pass
            if mm is None:
                continue
            num = int(mm.group(1))
            try:
                st = self.objeto_bruto(num)
                if not isinstance(st, Stream):
                    continue
                dados = st.decodificar()
                n = int(self.resolver(st.dic.get('N', 0)))
                lx = Lexer(dados, 0, self)
                pares = []
                for k in range(n):
                    a = lx.token()
                    b = lx.token()
                    if not isinstance(a, int) or not isinstance(b, int):
                        break
                    pares.append((a, b))
                first = int(self.resolver(st.dic.get('First', 0)))
                self._objstm_cache[num] = (dados, first, pares)
                for idx, (onum, _) in enumerate(pares):
                    if onum not in self.offsets:
                        self.em_objstm[onum] = (num, idx)
            except Exception:
                continue

    def objeto_bruto(self, num):
        off = self.offsets.get(num)
        if off is None:
            return None
        o = Lexer(self.d, off, self).objeto()
        if isinstance(o, Stream):
            o.num, o.ger = num, self.gens.get(num, 0)
        return o

    def objeto(self, num):
        if num in self.cache:
            return self.cache[num]
        self.cache[num] = None  # evita recursão infinita
        o = None
        try:
            if num in self.offsets:
                o = self.objeto_bruto(num)
            elif num in self.em_objstm:
                sn, idx = self.em_objstm[num]
                dados, first, pares = self._objstm_cache[sn]
                off = pares[idx][1]
                o = Lexer(dados, first + off, self).objeto()
        except Exception:
            o = None
        self.cache[num] = o
        return o

    def resolver(self, o, prof=0):
        while isinstance(o, Ref) and prof < 20:
            o = self.objeto(o.num)
            prof += 1
        return o

    # --------------------------------------------------- páginas

    def paginas(self):
        """Lista de (dicionário da página, resources herdados)."""
        raiz = self.resolver(self.trailer.get('Root'))
        if not isinstance(raiz, dict):
            raiz = None
            for num in sorted(self.offsets):
                o = self.objeto(num)
                if isinstance(o, dict) and o.get('Type') == 'Catalog':
                    raiz = o
                    break
        resultado = []
        vistos = set()

        def andar(no, res, prof):
            if prof > 40:
                return
            if isinstance(no, Ref):
                if no.num in vistos:
                    return
                vistos.add(no.num)
            no = self.resolver(no)
            if not isinstance(no, dict):
                return
            r = no.get('Resources', res)
            tipo = no.get('Type')
            if 'Kids' in no and tipo != 'Page':
                kids = self.resolver(no.get('Kids'))
                if isinstance(kids, list):
                    for k in kids:
                        andar(k, r, prof + 1)
            else:
                resultado.append((no, r))

        if raiz is not None:
            andar(raiz.get('Pages'), None, 0)
        if not resultado:
            # fallback: todos os objetos /Type /Page em ordem
            for num in sorted(set(self.offsets) | set(self.em_objstm)):
                o = self.objeto(num)
                if isinstance(o, dict) and o.get('Type') == 'Page':
                    resultado.append((o, o.get('Resources')))
        return resultado

    def info(self):
        inf = self.resolver(self.trailer.get('Info'))
        return inf if isinstance(inf, dict) else {}

    def conteudo_pagina(self, pag):
        c = self.resolver(pag.get('Contents'))
        partes = []
        if isinstance(c, Stream):
            c = [c]
        if isinstance(c, list):
            for s in c:
                s = self.resolver(s)
                if isinstance(s, Stream):
                    try:
                        partes.append(s.decodificar())
                    except Exception:
                        pass
        return b'\n'.join(partes)


# ------------------------------------------------------------ texto PDF (metadados)

PDFDOC = {0x18: '˘', 0x19: 'ˇ', 0x1a: 'ˆ', 0x1b: '˙', 0x1c: '˝', 0x1d: '˛',
          0x1e: '˚', 0x1f: '˜', 0x80: '•', 0x81: '†', 0x82: '‡', 0x83: '…',
          0x84: '—', 0x85: '–', 0x86: 'ƒ', 0x87: '⁄', 0x88: '‹', 0x89: '›',
          0x8a: '−', 0x8b: '‰', 0x8c: '„', 0x8d: '“', 0x8e: '”', 0x8f: '‘',
          0x90: '’', 0x91: '‚', 0x92: '™', 0x93: 'ﬁ', 0x94: 'ﬂ', 0x95: 'Ł',
          0x96: 'Œ', 0x97: 'Š', 0x98: 'Ÿ', 0x99: 'Ž', 0x9a: 'ı', 0x9b: 'ł',
          0x9c: 'œ', 0x9d: 'š', 0x9e: 'ž', 0xa0: '€'}


def texto_pdf(b):
    if isinstance(b, str):
        return b
    if not isinstance(b, (bytes, bytearray)):
        return ''
    if b[:2] == b'\xfe\xff':
        return b[2:].decode('utf-16-be', 'replace')
    if b[:2] == b'\xff\xfe':
        return b[2:].decode('utf-16-le', 'replace')
    if b[:3] == b'\xef\xbb\xbf':
        return b[3:].decode('utf-8', 'replace')
    return ''.join(PDFDOC.get(c, chr(c)) for c in b)


# ------------------------------------------------------------ fontes / codificação

def _glifos():
    """Nome de glifo Adobe -> caractere, gerado a partir do unicodedata."""
    g = {}
    acentos = {'ACUTE': 'acute', 'GRAVE': 'grave', 'CIRCUMFLEX': 'circumflex', 'TILDE': 'tilde',
               'DIAERESIS': 'dieresis', 'CEDILLA': 'cedilla', 'RING ABOVE': 'ring', 'CARON': 'caron',
               'MACRON': 'macron', 'BREVE': 'breve', 'OGONEK': 'ogonek', 'DOT ABOVE': 'dotaccent',
               'DOUBLE ACUTE': 'hungarumlaut', 'STROKE': 'slash'}
    for cp in range(0x20, 0x250):
        ch = chr(cp)
        try:
            nm = unicodedata.name(ch)
        except ValueError:
            continue
        m = re.match(r'LATIN (SMALL|CAPITAL) LETTER (\w)(?: WITH (.+))?$', nm)
        if m:
            letra = m.group(2).lower() if m.group(1) == 'SMALL' else m.group(2)
            if m.group(3):
                a = acentos.get(m.group(3))
                if a:
                    g[letra + a] = ch
            else:
                g[letra] = ch
    g.update({
        'space': ' ', 'exclam': '!', 'quotedbl': '"', 'numbersign': '#', 'dollar': '$', 'percent': '%',
        'ampersand': '&', 'quotesingle': "'", 'quoteright': '’', 'quoteleft': '‘', 'parenleft': '(',
        'parenright': ')', 'asterisk': '*', 'plus': '+', 'comma': ',', 'hyphen': '-', 'period': '.',
        'slash': '/', 'zero': '0', 'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5', 'six': '6',
        'seven': '7', 'eight': '8', 'nine': '9', 'colon': ':', 'semicolon': ';', 'less': '<', 'equal': '=',
        'greater': '>', 'question': '?', 'at': '@', 'bracketleft': '[', 'backslash': '\\',
        'bracketright': ']', 'asciicircum': '^', 'underscore': '_', 'grave': '`', 'braceleft': '{', 'bar': '|',
        'braceright': '}', 'asciitilde': '~', 'exclamdown': '¡', 'cent': '¢', 'sterling': '£', 'yen': '¥',
        'section': '§', 'copyright': '©', 'ordfeminine': 'ª', 'guillemotleft': '«', 'registered': '®',
        'degree': '°', 'plusminus': '±', 'mu': 'µ', 'paragraph': '¶', 'periodcentered': '·',
        'ordmasculine': 'º', 'guillemotright': '»', 'onequarter': '¼', 'onehalf': '½', 'threequarters': '¾',
        'questiondown': '¿', 'multiply': '×', 'divide': '÷', 'germandbls': 'ß', 'AE': 'Æ', 'ae': 'æ',
        'OE': 'Œ', 'oe': 'œ', 'Oslash': 'Ø', 'oslash': 'ø', 'endash': '–', 'emdash': '—',
        'quotedblleft': '“', 'quotedblright': '”', 'quotesinglbase': '‚', 'quotedblbase': '„',
        'bullet': '•', 'ellipsis': '…', 'dagger': '†', 'daggerdbl': '‡', 'perthousand': '‰',
        'guilsinglleft': '‹', 'guilsinglright': '›', 'trademark': '™', 'Euro': '€', 'euro': '€',
        'fi': 'fi', 'fl': 'fl', 'ff': 'ff', 'ffi': 'ffi', 'ffl': 'ffl', 'minus': '-', 'nbspace': ' ',
        'sfthyphen': '-', 'softhyphen': '-', 'dotlessi': 'ı', 'florin': 'ƒ', 'fraction': '/',
        'circumflex': '^', 'tilde': '~', 'dieresis': '¨', 'acute': '´', 'cedilla': '¸', 'macron': '¯',
        'brokenbar': '¦', 'logicalnot': '¬', 'currency': '¤', 'twosuperior': '²', 'threesuperior': '³',
        'onesuperior': '¹', 'Eth': 'Ð', 'eth': 'ð', 'Thorn': 'Þ', 'thorn': 'þ', 'arrowright': '→',
        'checkmark': '✓', 'uni00A0': ' ',
    })
    return g


GLIFOS = _glifos()


def glifo_para_texto(nome):
    if nome in GLIFOS:
        return GLIFOS[nome]
    base = nome.split('.')[0].split('_')[0]
    if base in GLIFOS:
        return GLIFOS[base]
    m = re.match(r'uni([0-9A-Fa-f]{4,})$', base)
    if m:
        h = m.group(1)
        try:
            return ''.join(chr(int(h[i:i + 4], 16)) for i in range(0, len(h) - len(h) % 4, 4))
        except ValueError:
            return ''
    m = re.match(r'u([0-9A-Fa-f]{4,6})$', base)
    if m:
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return ''
    if len(base) == 1:
        return base
    return ''


def _tabela_base(nome):
    if nome == 'MacRomanEncoding':
        return {i: bytes([i]).decode('mac_roman') for i in range(32, 256)}
    # WinAnsi (cp1252) como padrão razoável para texto latino
    t = {}
    for i in range(32, 256):
        try:
            t[i] = bytes([i]).decode('cp1252')
        except UnicodeDecodeError:
            t[i] = ''
    return t


def _parse_cmap(dados):
    """ToUnicode CMap -> (mapa {codigo: texto}, larguras de byte possíveis)."""
    mapa = {}
    larguras = set()

    def hexb(s):
        return s

    def para_txt(b):
        if len(b) % 2 == 0 and b:
            try:
                return b.decode('utf-16-be')
            except UnicodeDecodeError:
                pass
        return b.decode('latin-1')

    lx = Lexer(dados)
    pilha = []
    modo = None
    while True:
        t = lx.token()
        if t is None and lx.i >= len(dados):
            break
        if isinstance(t, Operador):
            if t == 'begincodespacerange':
                modo = 'cs'
                pilha = []
            elif t == 'endcodespacerange':
                for k in range(0, len(pilha) - 1, 2):
                    if isinstance(pilha[k], bytes):
                        larguras.add(len(pilha[k]))
                modo = None
            elif t == 'beginbfchar':
                modo = 'char'
                pilha = []
            elif t == 'endbfchar':
                for k in range(0, len(pilha) - 1, 2):
                    a, b = pilha[k], pilha[k + 1]
                    if isinstance(a, bytes) and isinstance(b, bytes):
                        mapa[(len(a), int.from_bytes(a, 'big'))] = para_txt(b)
                        larguras.add(len(a))
                modo = None
            elif t == 'beginbfrange':
                modo = 'range'
                pilha = []
            elif t == 'endbfrange':
                k = 0
                while k + 2 < len(pilha) + 0 and k + 2 <= len(pilha) - 1:
                    a, b, c = pilha[k], pilha[k + 1], pilha[k + 2]
                    k += 3
                    if not (isinstance(a, bytes) and isinstance(b, bytes)):
                        continue
                    ini, fim = int.from_bytes(a, 'big'), int.from_bytes(b, 'big')
                    w = len(a)
                    larguras.add(w)
                    if fim - ini > 65536:
                        continue
                    if isinstance(c, bytes):
                        base = int.from_bytes(c, 'big') if c else 0
                        if len(c) <= 2:
                            for x in range(ini, fim + 1):
                                try:
                                    mapa[(w, x)] = chr(base + x - ini)
                                except (ValueError, OverflowError):
                                    pass
                        else:
                            pref = c[:-2]
                            ult = int.from_bytes(c[-2:], 'big')
                            for x in range(ini, fim + 1):
                                mapa[(w, x)] = para_txt(pref + (ult + x - ini).to_bytes(2, 'big', signed=False)
                                                         if ult + x - ini < 65536 else pref)
                    elif isinstance(c, list):
                        for x, v in zip(range(ini, fim + 1), c):
                            if isinstance(v, bytes):
                                mapa[(w, x)] = para_txt(v)
                modo = None
            continue
        if modo:
            if t == '[':
                arr = []
                while True:
                    u = lx.token()
                    if u == ']' or u is None:
                        break
                    arr.append(u)
                pilha.append(arr)
            else:
                pilha.append(t)
    return mapa, sorted(larguras) or [1]


class Fonte:
    def __init__(self, doc, dic):
        self.mapa = None
        self.larguras = [1]
        self.tabela = None
        self.composta = False
        dic = doc.resolver(dic)
        if not isinstance(dic, dict):
            self.tabela = _tabela_base(None)
            return
        sub = dic.get('Subtype')
        self.composta = sub == 'Type0'
        tu = doc.resolver(dic.get('ToUnicode'))
        if isinstance(tu, Stream):
            try:
                self.mapa, self.larguras = _parse_cmap(tu.decodificar())
            except Exception:
                self.mapa = None
        if self.composta:
            if not self.mapa:
                self.larguras = [2]
            elif self.larguras == [1]:
                self.larguras = [2] if all(w == 2 for (w, _) in self.mapa) else self.larguras
            return
        enc = doc.resolver(dic.get('Encoding'))
        base = None
        dif = None
        if isinstance(enc, str):
            base = enc
        elif isinstance(enc, dict):
            base = doc.resolver(enc.get('BaseEncoding'))
            dif = doc.resolver(enc.get('Differences'))
        self.tabela = _tabela_base(base)
        if isinstance(dif, list):
            cod = 0
            for x in dif:
                x = doc.resolver(x)
                if isinstance(x, int):
                    cod = x
                elif isinstance(x, Nome):
                    t = glifo_para_texto(x)
                    if t or cod not in self.tabela:
                        self.tabela[cod] = t
                    cod += 1

    def decodificar(self, b):
        if self.mapa:
            out = []
            i = 0
            n = len(b)
            ws = self.larguras
            while i < n:
                achou = False
                for w in ws:
                    if i + w <= n:
                        c = int.from_bytes(b[i:i + w], 'big')
                        v = self.mapa.get((w, c))
                        if v is not None:
                            out.append(v)
                            i += w
                            achou = True
                            break
                if not achou:
                    w = ws[0]
                    if not self.composta and self.tabela is not None:
                        out.append(self.tabela.get(b[i], ''))
                    i += w
            return ''.join(out)
        if self.composta:
            # Identity sem ToUnicode: códigos são CIDs, não dá para saber o texto
            return ''
        return ''.join(self.tabela.get(c, '') for c in b)


# ------------------------------------------------------------ interpretação do conteúdo

def _limpar(t):
    t = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f�­]', '', t)
    t = re.sub(r'[ \t ]+', ' ', t)
    t = re.sub(r' *\n *', '\n', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    # junta palavras hifenizadas no fim de linha
    t = re.sub(r'(\w)-\n(\w)', r'\1\2', t)
    return t.strip()


class Extrator:
    LIMITE_CONTEUDO = 8_000_000

    def __init__(self, doc):
        self.doc = doc
        self.cache_fontes = {}

    def fonte(self, res, nome):
        res = self.doc.resolver(res)
        if not isinstance(res, dict):
            return None
        fontes = self.doc.resolver(res.get('Font'))
        if not isinstance(fontes, dict):
            return None
        ref = fontes.get(nome)
        chave = ref.num if isinstance(ref, Ref) else id(ref)
        if chave not in self.cache_fontes:
            try:
                self.cache_fontes[chave] = Fonte(self.doc, ref)
            except Exception:
                self.cache_fontes[chave] = None
        return self.cache_fontes[chave]

    def texto(self, conteudo, res, prof=0):
        if len(conteudo) > self.LIMITE_CONTEUDO:
            conteudo = conteudo[:self.LIMITE_CONTEUDO]
        out = []
        lx = Lexer(conteudo, 0, None)
        ops = []
        fonte = None
        ultimo_y = None
        d = conteudo
        n = len(d)
        while True:
            t = lx.token()
            if t is None and lx.i >= n:
                break
            if t == '[':
                arr = []
                while True:
                    u = lx.token()
                    if u == ']' or (u is None and lx.i >= n):
                        break
                    arr.append(u)
                ops.append(arr)
                continue
            if t == '<<':
                # dicionário inline (BDC): consome
                prof_d = 1
                while prof_d and lx.i < n:
                    u = lx.token()
                    if u == '<<':
                        prof_d += 1
                    elif u == '>>':
                        prof_d -= 1
                ops.append({})
                continue
            if not isinstance(t, Operador):
                ops.append(t)
                continue
            op = str(t)
            if op == 'BI':
                k = d.find(b'EI', lx.i)
                while k >= 0 and not (k + 2 >= n or d[k + 2] in ESPACO):
                    k = d.find(b'EI', k + 2)
                lx.i = (k + 2) if k >= 0 else n
            elif op == 'Tf':
                if len(ops) >= 2 and isinstance(ops[-2], str):
                    fonte = self.fonte(res, ops[-2])
            elif op == 'Tj' or op == "'" or op == '"':
                if op != 'Tj':
                    out.append('\n')
                if ops and isinstance(ops[-1], bytes):
                    out.append(self._dec(fonte, ops[-1]))
            elif op == 'TJ':
                if ops and isinstance(ops[-1], list):
                    for x in ops[-1]:
                        if isinstance(x, bytes):
                            out.append(self._dec(fonte, x))
                        elif isinstance(x, (int, float)) and x < -180:
                            out.append(' ')
            elif op in ('Td', 'TD'):
                if len(ops) >= 2 and isinstance(ops[-1], (int, float)):
                    if abs(ops[-1]) > 0.5:
                        out.append('\n')
                    elif isinstance(ops[-2], (int, float)) and ops[-2] > 0.5:
                        out.append(' ')
            elif op == 'T*':
                out.append('\n')
            elif op == 'Tm':
                if len(ops) >= 6 and isinstance(ops[-1], (int, float)):
                    y = ops[-1]
                    if ultimo_y is not None and abs(y - ultimo_y) > 0.5:
                        out.append('\n')
                    else:
                        out.append(' ')
                    ultimo_y = y
            elif op == 'ET':
                out.append(' ')
            elif op == 'Do' and prof < 5:
                if ops and isinstance(ops[-1], str):
                    out.append(self._xobjeto(res, ops[-1], prof))
            ops = []
        return ''.join(out)

    def _dec(self, fonte, b):
        if fonte is None:
            return b.decode('cp1252', 'replace') if b and max(b) < 128 else ''
        try:
            return fonte.decodificar(b)
        except Exception:
            return ''

    def _xobjeto(self, res, nome, prof):
        res = self.doc.resolver(res)
        if not isinstance(res, dict):
            return ''
        xo = self.doc.resolver(res.get('XObject'))
        if not isinstance(xo, dict):
            return ''
        st = self.doc.resolver(xo.get(nome))
        if not isinstance(st, Stream) or st.dic.get('Subtype') != 'Form':
            return ''
        try:
            dados = st.decodificar()
        except Exception:
            return ''
        r2 = st.dic.get('Resources', res)
        return '\n' + self.texto(dados, r2, prof + 1) + '\n'


# ------------------------------------------------------------ API

def _via_pypdf(caminho):
    try:
        import pypdf  # noqa: só se estiver vendorizado
    except ImportError:
        return None
    r = pypdf.PdfReader(caminho)
    titulo = ''
    try:
        titulo = (r.metadata or {}).get('/Title') or ''
    except Exception:
        pass
    pags = []
    for p in r.pages:
        try:
            pags.append(_limpar(p.extract_text() or ''))
        except Exception:
            pags.append('')
    return {'titulo': str(titulo), 'paginas': pags, 'protegido': False}


def extrair(caminho, max_paginas=5000):
    """Retorna {'titulo', 'paginas': [texto, ...], 'protegido': bool}."""
    r = _via_pypdf(caminho)
    if r is not None:
        return r
    with open(caminho, 'rb') as f:
        dados = f.read()
    doc = Documento(dados)
    tit = doc.resolver(doc.info().get('Title')) if doc.info() else ''
    info_ref = doc.trailer.get('Info')
    titulo = texto_pdf(tit)
    if doc.cifra is not None and isinstance(tit, bytes) and isinstance(info_ref, Ref):
        # o título só vem cifrado se o objeto Info estiver fora de "object streams": escolhe a
        # versão que parece texto de verdade
        try:
            decifrado = texto_pdf(doc.cifra.decifrar(info_ref.num, info_ref.ger, tit, stream=False))
        except Exception:
            decifrado = ''
        legivel = lambda t: sum(c.isalnum() and ord(c) < 0x250 or c in ' .,:;-()' for c in t) / max(1, len(t))
        titulo = max((titulo, decifrado), key=legivel)
        if legivel(titulo) < 0.85:
            titulo = ''
    if doc.criptografado and doc.cifra is None:
        n = len(doc.paginas())
        return {'titulo': titulo, 'paginas': [''] * n, 'protegido': True}
    ex = Extrator(doc)
    pags = []
    for pag, res in doc.paginas()[:max_paginas]:
        try:
            txt = ex.texto(doc.conteudo_pagina(pag), res)
        except Exception:
            txt = ''
        pags.append(_limpar(txt))
    return {'titulo': titulo, 'paginas': pags, 'protegido': False}


if __name__ == '__main__':
    import sys
    import time
    t = time.time()
    r = extrair(sys.argv[1])
    print('titulo:', r['titulo'], '| paginas:', len(r['paginas']), '| protegido:', r['protegido'],
          '| %.2fs' % (time.time() - t))
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if n < len(r['paginas']):
        print(r['paginas'][n][:1500])
