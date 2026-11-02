"""BIBLIOTECA OFFLINE - mapa do Brasil para ver DENTRO do app (PC e celular pela rede da casa).

Recorta o Brasil (e a faixa de fronteira) do mapa-mundi aberto da Protomaps (dados do
OpenStreetMap, formato PMTiles) usando so pedidos parciais HTTP: baixa apenas os pedacos do Brasil.
Grava 2_Mapas\\brasil.pmtiles (+ .ok). O app le esse arquivo e mostra o mapa na tela "Mapa".
Tambem baixa a lista de cidades do Brasil (GeoNames) para a busca de lugares: 2_Mapas\\lugares_br.tsv.

Uso:  python baixar_mapa_pc.py               (zoom maximo 15 = ruas e predios)
      python baixar_mapa_pc.py --zoom 14     (menor)
      python baixar_mapa_pc.py --estimar     (so calcula o tamanho, nao grava nada)
"""
import array
import concurrent.futures
import gzip
import io
import json
import math
import os
import shutil
import struct
import sys
import time
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
LOG = os.path.join(AQUI, 'registro.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
BUILDS = 'https://build-metadata.protomaps.dev/builds.json'
BASE = 'https://build.protomaps.com/'
BBOX = (-74.1, -33.9, -34.7, 5.4)  # oeste, sul, leste, norte (Brasil inteiro)
DESTINO = os.path.join(RAIZ, '2_Mapas', 'brasil.pmtiles')
RESERVA_GB = int(os.environ.get('BIBLIOTECA_RESERVA_GB') or 10)  # o instalador e a atualização passam a folga deles


def escreve(msg):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    print(linha, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')


# ------------------------------------------------------------------ PMTiles v3

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


def ler_varint(b, pos):
    r = 0
    desl = 0
    while True:
        c = b[pos]
        pos += 1
        r |= (c & 0x7f) << desl
        if c < 0x80:
            return r, pos
        desl += 7


def varint(n, saida):
    while n >= 0x80:
        saida.append((n & 0x7f) | 0x80)
        n >>= 7
    saida.append(n)


def ler_diretorio(dados):
    b = gzip.decompress(dados)
    n, p = ler_varint(b, 0)
    ids, runs, tams, offs = [0] * n, [0] * n, [0] * n, [0] * n
    ultimo = 0
    for i in range(n):
        d, p = ler_varint(b, p)
        ultimo += d
        ids[i] = ultimo
    for i in range(n):
        runs[i], p = ler_varint(b, p)
    for i in range(n):
        tams[i], p = ler_varint(b, p)
    for i in range(n):
        v, p = ler_varint(b, p)
        offs[i] = offs[i - 1] + tams[i - 1] if (v == 0 and i > 0) else v - 1
    return ids, runs, tams, offs


def escrever_diretorio(ids, runs, tams, offs):
    s = bytearray()
    varint(len(ids), s)
    ultimo = 0
    for t in ids:
        varint(t - ultimo, s)
        ultimo = t
    for r in runs:
        varint(r, s)
    for t in tams:
        varint(t, s)
    for i, o in enumerate(offs):
        if i > 0 and o == offs[i - 1] + tams[i - 1]:
            varint(0, s)
        else:
            varint(o + 1, s)
    return gzip.compress(bytes(s), 9, mtime=0)


def pedir(url, ini, fim, tentativas=6):
    """Bytes [ini, fim) do arquivo remoto."""
    for t in range(1, tentativas + 1):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Range': 'bytes=%d-%d' % (ini, fim - 1)})
            with urllib.request.urlopen(req, timeout=180) as r:
                dados = r.read()
            if len(dados) == fim - ini:
                return dados
        except Exception as e:
            erro = e
        time.sleep(5 * t)
    raise RuntimeError('falha ao baixar bytes %d-%d' % (ini, fim))


# ------------------------------------------------------------------ faixas de tiles do Brasil

def lon_x(lon, n):
    return min(n - 1, max(0, int((lon + 180) / 360 * n)))


def lat_y(lat, n):
    r = math.radians(lat)
    return min(n - 1, max(0, int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)))


def faixas(zmax):
    """Lista ordenada de faixas [ini, fim) de ids de tile que cobrem o BBOX, zoom 0..zmax.
    Filhos de um tile na curva de Hilbert ocupam ids contíguos: tile inteiro dentro -> uma faixa por nível."""
    o, s, l, nn = BBOX
    res = []

    def visita(z, x, y):
        n = 1 << z
        x0, x1 = lon_x(o, n), lon_x(l, n)
        y0, y1 = lat_y(nn, n), lat_y(s, n)
        if x < x0 or x > x1 or y < y0 or y > y1:
            return
        d = zxy_para_id(z, x, y) - (4 ** z - 1) // 3
        dentro = x0 < x < x1 and y0 < y < y1
        if dentro:
            for k in range(0, zmax - z + 1):
                base = (4 ** (z + k) - 1) // 3
                res.append((base + d * 4 ** k, base + (d + 1) * 4 ** k))
            return
        res.append((zxy_para_id(z, x, y), zxy_para_id(z, x, y) + 1))
        if z < zmax:
            for dx in (0, 1):
                for dy in (0, 1):
                    visita(z + 1, 2 * x + dx, 2 * y + dy)

    visita(0, 0, 0)
    res.sort()
    juntas = []
    for a, b in res:
        if juntas and a <= juntas[-1][1]:
            juntas[-1] = (juntas[-1][0], max(juntas[-1][1], b))
        else:
            juntas.append((a, b))
    return juntas


# ------------------------------------------------------------------ recorte

class Recorte:
    def __init__(self, url, zmax):
        self.url = url
        self.zmax = zmax
        self.cab = pedir(url, 0, 16384)
        if self.cab[:7] != b'PMTiles' or self.cab[7] != 3:
            raise RuntimeError('arquivo de mapa remoto não é PMTiles v3')
        (self.raiz_off, self.raiz_tam, self.meta_off, self.meta_tam, self.folhas_off, self.folhas_tam,
         self.dados_off, self.dados_tam) = struct.unpack('<8Q', self.cab[8:72])
        if self.cab[97] != 2 or self.cab[98] != 2:
            raise RuntimeError('compressão não suportada')
        self.ids = array.array('Q')
        self.runs = array.array('L')
        self.tams = array.array('L')
        self.offs = array.array('Q')

    def coletar(self):
        fx = faixas(self.zmax)
        escreve('Mapa do PC: %d faixas de tiles até o zoom %d' % (len(fx), self.zmax))
        raiz = ler_diretorio(self.cab[self.raiz_off:self.raiz_off + self.raiz_tam])
        self._percorrer(raiz, fx, None)

    def _percorrer(self, d, fx, limite):
        """Percorre um diretório; 'limite' = primeiro id fora dele (para a última folha)."""
        import bisect
        ids, runs, tams, offs = d
        inicios = [a for a, _ in fx]
        folhas = []
        for i in range(len(ids)):
            ini = ids[i]
            if runs[i]:
                fim = ini + runs[i]
            else:
                fim = ids[i + 1] if i + 1 < len(ids) else (limite or 1 << 62)
            j = bisect.bisect_right(inicios, ini) - 1
            j = max(j, 0)
            while j < len(fx) and fx[j][0] < fim:
                a, b = fx[j]
                if b > ini:
                    if runs[i]:
                        c0, c1 = max(a, ini), min(b, fim)
                        self.ids.append(c0)
                        self.runs.append(c1 - c0)
                        self.tams.append(tams[i])
                        self.offs.append(offs[i])
                    else:
                        folhas.append((offs[i], tams[i], fim))
                        break
                j += 1
        if folhas:
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                lidas = list(ex.map(lambda f: (ler_diretorio(pedir(self.url, self.folhas_off + f[0],
                                                                    self.folhas_off + f[0] + f[1])), f[2]), folhas))
            for sub, fim in lidas:
                self._percorrer(sub, fx, fim)

    def tamanho(self):
        unicos = {}
        for o, t in zip(self.offs, self.tams):
            unicos[o] = t
        return sum(unicos.values()), len(unicos)

    def gravar(self, destino):
        ordem = sorted(range(len(self.ids)), key=self.ids.__getitem__)
        ids = array.array('Q', (self.ids[i] for i in ordem))
        runs = array.array('L', (self.runs[i] for i in ordem))
        tams = array.array('L', (self.tams[i] for i in ordem))
        offs_src = array.array('Q', (self.offs[i] for i in ordem))
        del self.ids, self.runs, self.tams, self.offs
        # conteúdos únicos em ordem de origem (o arquivo de origem está agrupado pela curva de Hilbert)
        unicos = sorted(set(zip(offs_src, tams)))
        novo_off = {}
        pos = 0
        for o, t in unicos:
            novo_off[o] = pos
            pos += t
        total = pos
        # blocos de download: pedaços contíguos (com pequenas lacunas) de até 16 MB
        blocos = []
        for o, t in unicos:
            if blocos and o - blocos[-1][1] < 65536 and o + t - blocos[-1][0] < 16 << 20:
                blocos[-1][1] = max(blocos[-1][1], o + t)
                blocos[-1][2].append((o, t))
            else:
                blocos.append([o, o + t, [(o, t)]])
        escreve('Mapa do PC: %d tiles, %d conteúdos, %.2f GB em %d blocos' % (sum(runs), len(unicos), total / 2**30, len(blocos)))
        offs = array.array('Q', (novo_off[o] for o in offs_src))
        raiz, folhas = self._diretorios(ids, runs, tams, offs)
        meta = json.loads(gzip.decompress(pedir(self.url, self.meta_off, self.meta_off + self.meta_tam)))
        meta['description'] = 'Brasil - recorte do mapa Protomaps (dados OpenStreetMap)'
        meta_b = gzip.compress(json.dumps(meta).encode('utf-8'), 9, mtime=0)
        o, s, l, n = BBOX
        cab = bytearray(127)
        raiz_off = 127
        meta_off = raiz_off + len(raiz)
        folhas_off = meta_off + len(meta_b)
        dados_ini = folhas_off + len(folhas)
        cab[:8] = b'PMTiles' + bytes([3])
        struct.pack_into('<8Q', cab, 8, raiz_off, len(raiz), meta_off, len(meta_b), folhas_off, len(folhas),
                         dados_ini, total)
        struct.pack_into('<3Q', cab, 72, sum(runs), len(ids), len(unicos))
        cab[96:102] = bytes([1, 2, 2, 1, 0, self.zmax])
        struct.pack_into('<4i', cab, 102, int(o * 1e7), int(s * 1e7), int(l * 1e7), int(n * 1e7))
        struct.pack_into('<Bii', cab, 118, 4, int(-51.9 * 1e7), int(-14.2 * 1e7))
        topo = bytes(cab) + raiz + meta_b + folhas
        # retomada: só vale se o arquivo parcial for do mesmo mapa de origem (mesmo topo)
        parcial = destino + '.part'
        retomar = destino + '.blocos'
        feitos = 0
        if os.path.exists(parcial) and os.path.exists(retomar):
            try:
                url_ant, n_ant = open(retomar, encoding='utf-8').read().split()
                with open(parcial, 'rb') as f:
                    if url_ant == self.url and f.read(len(topo)) == topo:
                        feitos = int(n_ant)
            except (OSError, ValueError):
                feitos = 0
        if feitos:
            escreve('Mapa do PC: retomando do bloco %d' % feitos)
        with open(parcial, 'r+b' if feitos else 'wb') as f:
            if not feitos:
                f.write(topo)
            def baixa(bl):
                return bl, pedir(self.url, self.dados_off + bl[0], self.dados_off + bl[1])
            t0 = time.time()
            with concurrent.futures.ThreadPoolExecutor(8) as ex:
                for k in range(feitos, len(blocos), 64):
                    lote = blocos[k:k + 64]
                    for bl, dados in ex.map(baixa, lote):
                        for o2, t in bl[2]:
                            f.seek(dados_ini + novo_off[o2])
                            f.write(dados[o2 - bl[0]:o2 - bl[0] + t])
                    f.flush()
                    with open(retomar, 'w', encoding='utf-8') as r:
                        r.write('%s %d' % (self.url, k + len(lote)))
                    if shutil.disk_usage(RAIZ).free < 3 * 2**30:
                        raise RuntimeError('pouco espaço no SSD')
                    feito = k + len(lote)
                    if feito % 1280 == 0 or feito == len(blocos):
                        escreve('Mapa do PC: %d/%d blocos (%.0f min)' % (feito, len(blocos), (time.time() - t0) / 60))
            f.truncate(dados_ini + total)
        os.replace(parcial, destino)
        os.remove(retomar)
        with open(destino + '.ok', 'w', encoding='utf-8') as f:
            f.write(self.url + chr(10))
        escreve('Mapa do PC: gravado %s (%.2f GB)' % (destino, os.path.getsize(destino) / 2**30))

    @staticmethod
    def _diretorios(ids, runs, tams, offs):
        n = len(ids)
        if n <= 4096:
            r = escrever_diretorio(ids, runs, tams, offs)
            if len(r) <= 16384 - 127:
                return r, b''
        tam_folha = 4096
        while True:
            folhas = bytearray()
            r_ids, r_runs, r_tams, r_offs = [], [], [], []
            for i in range(0, n, tam_folha):
                d = escrever_diretorio(ids[i:i + tam_folha], runs[i:i + tam_folha], tams[i:i + tam_folha],
                                       offs[i:i + tam_folha])
                r_ids.append(ids[i])
                r_runs.append(0)
                r_tams.append(len(d))
                r_offs.append(len(folhas))
                folhas += d
            raiz = escrever_diretorio(r_ids, r_runs, r_tams, r_offs)
            if len(raiz) <= 16384 - 127:
                return raiz, bytes(folhas)
            tam_folha *= 2


# ------------------------------------------------------------------ lugares (busca)

def lugares():
    destino = os.path.join(RAIZ, '2_Mapas', 'lugares_br.tsv')
    if os.path.exists(destino + '.ok'):
        return
    req = urllib.request.Request('https://download.geonames.org/export/dump/BR.zip', headers={'User-Agent': UA})
    dados = urllib.request.urlopen(req, timeout=600).read()
    z = zipfile.ZipFile(io.BytesIO(dados))
    estados = {}
    linhas = []
    for linha in io.TextIOWrapper(z.open('BR.txt'), encoding='utf-8'):
        c = linha.rstrip('\n').split('\t')
        if len(c) < 15:
            continue
        classe, cod = c[6], c[7]
        if classe == 'A' and cod == 'ADM1':
            estados[c[10]] = c[1]
        if classe == 'P' or (classe in ('T', 'H', 'L', 'S') and cod in (
                'MT', 'MTS', 'PK', 'HLL', 'LK', 'RSV', 'BAY', 'ISL', 'PRK', 'AIRP', 'HSP', 'FLLS', 'DAM', 'CAPE', 'RF')):
            linhas.append((c[1], c[4], c[5], cod, c[10], int(c[14] or 0)))
    with open(destino, 'w', encoding='utf-8') as f:
        f.write('nome\tlat\tlon\ttipo\testado\tpopulacao\n')
        for nome, lat, lon, cod, adm1, pop in linhas:
            f.write('%s\t%s\t%s\t%s\t%s\t%d\n' % (nome, lat, lon, cod, estados.get(adm1, ''), pop))
    with open(destino + '.ok', 'w', encoding='utf-8') as f:
        f.write('https://download.geonames.org/export/dump/BR.zip (CC BY 4.0)\n')
    escreve('Lugares: %d nomes do Brasil (GeoNames)' % len(linhas))


if __name__ == '__main__':
    zmax = 15
    if '--zoom' in sys.argv:
        zmax = int(sys.argv[sys.argv.index('--zoom') + 1])
    builds = json.loads(urllib.request.urlopen(urllib.request.Request(BUILDS, headers={'User-Agent': UA}), timeout=60).read())
    url = BASE + builds[-1]['key']
    escreve('===== Mapa do PC (%s, zoom até %d): início =====' % (builds[-1]['key'], zmax))
    if os.path.exists(DESTINO + '.ok') and '--estimar' not in sys.argv:
        escreve('Mapa do PC: já existe (apague 2_Mapas\\brasil.pmtiles.ok para refazer)')
    else:
        rc = Recorte(url, zmax)
        rc.coletar()
        tam, n = rc.tamanho()
        escreve('Mapa do PC: estimativa %.2f GB (%d conteúdos, %d entradas)' % (tam / 2**30, n, len(rc.ids)))
        if '--estimar' not in sys.argv:
            if shutil.disk_usage(RAIZ).free - tam < RESERVA_GB * 2**30:
                escreve('Mapa do PC: não cabe com folga; tente --zoom 14')
                sys.exit(1)
            rc.gravar(DESTINO)
    if '--estimar' not in sys.argv:
        lugares()
    escreve('===== Mapa do PC: fim. Livre: %.0f GB =====' % (shutil.disk_usage(RAIZ).free / 2**30))
