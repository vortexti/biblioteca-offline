"""Mapa do Brasil dentro do app: lê 2_Mapas\\brasil.pmtiles (PMTiles v3) e a lista de lugares.

O navegador (MapLibre, em static/mapa) pede os tiles vetoriais por /mapa/tiles/z/x/y.mvt;
aqui só achamos o pedaço certo do arquivo e devolvemos como está (já vem comprimido em gzip).
"""
import bisect
import gzip
import logging
import os
import struct
import threading
import unicodedata
from collections import OrderedDict

from . import config

log = logging.getLogger('biblioteca')

ARQUIVO = os.path.join(config.MAPAS, 'brasil.pmtiles')
LUGARES = os.path.join(config.MAPAS, 'lugares_br.tsv')
TIPOS_LUGAR = {
    'PPLC': 'capital do país', 'PPLA': 'capital de estado', 'PPLA2': 'cidade (sede de município)', 'PPL': 'localidade',
    'PPLX': 'bairro', 'PPLL': 'povoado', 'PPLF': 'fazenda/vila', 'PPLQ': 'lugar abandonado', 'PPLR': 'assentamento',
    'MT': 'morro/monte', 'MTS': 'serra', 'PK': 'pico', 'HLL': 'morro', 'LK': 'lago/lagoa', 'RSV': 'represa',
    'BAY': 'baía', 'ISL': 'ilha', 'PRK': 'parque', 'AIRP': 'aeroporto', 'HSP': 'hospital', 'FLLS': 'cachoeira',
    'DAM': 'barragem', 'CAPE': 'cabo/ponta', 'RF': 'recife',
}


def _varint(b, p):
    r = d = 0
    while True:
        c = b[p]
        p += 1
        r |= (c & 0x7f) << d
        if c < 0x80:
            return r, p
        d += 7


def _diretorio(dados):
    b = gzip.decompress(dados)
    n, p = _varint(b, 0)
    ids, runs, tams, offs = [0] * n, [0] * n, [0] * n, [0] * n
    ult = 0
    for i in range(n):
        d, p = _varint(b, p)
        ult += d
        ids[i] = ult
    for i in range(n):
        runs[i], p = _varint(b, p)
    for i in range(n):
        tams[i], p = _varint(b, p)
    for i in range(n):
        v, p = _varint(b, p)
        offs[i] = offs[i - 1] + tams[i - 1] if (v == 0 and i > 0) else v - 1
    return ids, runs, tams, offs


def zxy_para_id(z, x, y):
    acc = (4 ** z - 1) // 3
    n = 1 << z
    d = 0
    s = n >> 1
    while s:
        rx = 1 if x & s else 0
        ry = 1 if y & s else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = n - 1 - x
                y = n - 1 - y
            x, y = y, x
        s >>= 1
    return acc + d


class _Pmtiles:
    def __init__(self, caminho):
        self.caminho = caminho
        self.trava = threading.Lock()
        self.f = open(caminho, 'rb')
        cab = self.f.read(127)
        if cab[:7] != b'PMTiles' or cab[7] != 3:
            raise ValueError('não é PMTiles v3')
        (raiz_off, raiz_tam, self.meta_off, self.meta_tam, self.folhas_off, self.folhas_tam,
         self.dados_off, self.dados_tam) = struct.unpack('<8Q', cab[8:72])
        self.comp_tile = cab[98]
        self.minz, self.maxz = cab[100], cab[101]
        o, s, l, n = struct.unpack('<4i', cab[102:118])
        self.limites = [o / 1e7, s / 1e7, l / 1e7, n / 1e7]
        cz, clon, clat = struct.unpack('<Bii', cab[118:127])
        self.centro = [clon / 1e7, clat / 1e7, cz]
        self.raiz = _diretorio(self._ler(raiz_off, raiz_tam))
        self.folhas = OrderedDict()

    def _ler(self, off, tam):
        with self.trava:
            self.f.seek(off)
            return self.f.read(tam)

    def _folha(self, off, tam):
        chave = off
        d = self.folhas.get(chave)
        if d is None:
            d = _diretorio(self._ler(self.folhas_off + off, tam))
            self.folhas[chave] = d
            if len(self.folhas) > 256:
                self.folhas.popitem(last=False)
        return d

    def tile(self, z, x, y):
        if z < self.minz or z > self.maxz or not (0 <= x < (1 << z) and 0 <= y < (1 << z)):
            return None
        tid = zxy_para_id(z, x, y)
        d = self.raiz
        for _ in range(4):
            ids, runs, tams, offs = d
            i = bisect.bisect_right(ids, tid) - 1
            if i < 0:
                return None
            if runs[i] == 0:
                d = self._folha(offs[i], tams[i])
                continue
            if tid < ids[i] + runs[i]:
                return self._ler(self.dados_off + offs[i], tams[i])
            return None
        return None


_arq = None
_arq_trava = threading.Lock()
_lugares = None


def arquivo():
    """O mapa aberto, ou None se ainda não foi baixado por inteiro."""
    global _arq
    if _arq is not None:
        return _arq
    if not (os.path.isfile(ARQUIVO) and os.path.exists(ARQUIVO + '.ok')):
        return None
    with _arq_trava:
        if _arq is None:
            try:
                _arq = _Pmtiles(ARQUIVO)
            except (OSError, ValueError) as e:
                log.warning('mapa: não consegui abrir %s: %s', ARQUIVO, e)
                return None
    return _arq


def info():
    a = arquivo()
    if not a:
        return {'ok': False, 'motivo': 'O mapa do Brasil para o PC ainda não está no SSD (rode _Baixador\\BAIXAR_MAPA_PC.bat).'}
    return {'ok': True, 'limites': a.limites, 'centro': a.centro, 'maxzoom': a.maxz, 'minzoom': a.minz,
            'gb': round(os.path.getsize(ARQUIVO) / 2**30, 2), 'lugares': os.path.exists(LUGARES)}


def tile(z, x, y):
    a = arquivo()
    return a.tile(z, x, y) if a else None


def _sem_acento(t):
    return ''.join(c for c in unicodedata.normalize('NFD', t.lower()) if unicodedata.category(c) != 'Mn')


def _carregar_lugares():
    global _lugares
    if _lugares is None:
        lista = []
        if os.path.exists(LUGARES):
            with open(LUGARES, encoding='utf-8') as f:
                next(f, None)
                for linha in f:
                    c = linha.rstrip('\n').split('\t')
                    if len(c) >= 6:
                        lista.append((_sem_acento(c[0]), c[0], float(c[1]), float(c[2]), c[3], c[4], int(c[5] or 0)))
        _lugares = lista
    return _lugares


def buscar_lugares(q, limite=25):
    q = _sem_acento(' '.join(q.split()))[:80]
    if len(q) < 2:
        return []
    estado = ''
    if ',' in q:  # "Campinas, São Paulo"
        q, estado = [x.strip() for x in q.split(',', 1)]
    achados = []
    for norm, nome, lat, lon, tipo, uf, pop in _carregar_lugares():
        if q not in norm:
            continue
        if estado and estado not in _sem_acento(uf):
            continue
        cidade = tipo.startswith('PPLA') or tipo == 'PPLC' or pop >= 20000
        nota = (0 if norm == q else 1 if norm.startswith(q) else 2, 0 if cidade else 1 if tipo.startswith('PPL') else 2, -pop)
        rotulo = 'cidade' if tipo == 'PPL' and pop >= 20000 else TIPOS_LUGAR.get(tipo, tipo)
        achados.append((nota, {'nome': nome, 'lat': lat, 'lon': lon, 'tipo': rotulo,
                               'estado': uf, 'populacao': pop}))
    achados.sort(key=lambda x: x[0])
    return [a for _, a in achados[:limite]]
