"""BIBLIOTECA OFFLINE - refinamento: mapas do Brasil para o celular e livros de saude da Hesperian.

1. Organic Maps (celular): mapas offline do Brasil inteiro (23 regioes) + mapa-mundi base,
   na versao que o APK de 6_Instaladores usa. Vao para 2_Mapas\\OrganicMaps_<versao>.
   Como copiar para o celular: ver 2_Mapas\\COMO_USAR_NO_CELULAR.txt.
2. Hesperian em ingles (16 livros, ~430 capitulos em PDF): parto (A Book for Midwives),
   dentes (Where There Is No Dentist), saude da mulher, saude do trabalhador, criancas com
   deficiencia, saude mental comunitaria... Vao para 5_Acervo\\Saude\\Hesperian_EN\\<livro>.
Retomavel. Uso: python baixar_refino.py
"""
import glob
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
LOG = os.path.join(AQUI, 'registro.txt')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
RESERVA_GB = int(os.environ.get('BIBLIOTECA_RESERVA_GB') or 15)  # a atualização do app passa 8

LIVROS = {
    'wtnd': 'Where There Is No Doctor', 'nwtnd': 'New Where There Is No Doctor (advanced chapters)',
    'midw': 'A Book for Midwives', 'dent': 'Where There Is No Dentist', 'wwhnd': 'Where Women Have No Doctor',
    'wgthas': 'Workers Guide to Health and Safety', 'cgeh': 'Community Guide to Environmental Health',
    'dvc': 'Disabled Village Children', 'hhwl': 'Helping Health Workers Learn',
    'wwd': 'Health Handbook for Women with Disabilities', 'hcwb': 'Helping Children Who Are Blind',
    'hcwd': 'Helping Children Who Are Deaf', 'hclhiv': 'Helping Children Live with HIV',
    'haw': 'Health Actions for Women', 'pcmh': 'Promoting Community Mental Health', 'easb': 'Epilepsy and Seizures',
}


def escreve(msg):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    print(linha, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(linha + '\n')


def baixar(url, destino, assinatura=None):
    if os.path.exists(destino + '.ok'):
        return 'ja'
    if shutil.disk_usage(RAIZ).free < RESERVA_GB * 2**30:
        return 'espaco'
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    parcial = destino + '.part'
    for t in range(1, 5):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=300) as r, open(parcial, 'wb') as f:
                shutil.copyfileobj(r, f, 1 << 16)
            with open(parcial, 'rb') as f:
                ini = f.read(64)
            if assinatura and assinatura not in ini:
                os.remove(parcial)
                return 'invalido'
            os.replace(parcial, destino)
            with open(destino + '.ok', 'w', encoding='utf-8') as f:
                f.write(url + '\n')
            return 'ok'
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                return 'invalido'
            time.sleep(15 * t)
        except Exception:
            time.sleep(15 * t)
    return 'falha'


def mapas_celular():
    apk = sorted(glob.glob(os.path.join(RAIZ, '6_Instaladores', 'OrganicMaps-*.apk')))
    if not apk:
        escreve('Organic Maps: APK nao encontrado em 6_Instaladores; pulei os mapas do celular.')
        return
    arvore = json.loads(zipfile.ZipFile(apk[-1]).read('assets/countries.json'))
    versao = str(arvore['v'])

    def achar(no):
        if no.get('id') == 'Brazil':
            return no
        for f in no.get('g', []):
            r = achar(f)
            if r:
                return r

    def folhas(no):
        if 'g' in no:
            for f in no['g']:
                yield from folhas(f)
        else:
            yield no['id']

    regioes = ['World', 'WorldCoasts'] + list(folhas(achar(arvore)))
    try:
        servidor = json.loads(urllib.request.urlopen(urllib.request.Request(
            'https://meta.omaps.app/servers', headers={'User-Agent': UA}), timeout=60).read())[0]
    except Exception:
        servidor = 'https://cdn-us1.organicmaps.app/'
    pasta = os.path.join(RAIZ, '2_Mapas', 'OrganicMaps_' + versao)
    escreve('Organic Maps: versao %s, %d arquivos de mapa' % (versao, len(regioes)))
    cont = {}
    for r in regioes:
        url = servidor + 'maps/%s/%s.mwm' % (versao, urllib.parse.quote(r))
        res = baixar(url, os.path.join(pasta, r + '.mwm'))
        cont[res] = cont.get(res, 0) + 1
    escreve('Organic Maps: %s' % cont)


def hesperian_en():
    pagina = urllib.request.urlopen(urllib.request.Request(
        'https://languages.hesperian.org/pages/en/pdf.html', headers={'User-Agent': UA}), timeout=120).read().decode('utf-8', 'replace')
    links = sorted(set(re.findall(r'href="(https?://[^"]+\.pdf)"', pagina)))
    escreve('Hesperian EN: %d capitulos em PDF' % len(links))
    cont = {}
    for u in links:
        m = re.search(r'/en_([a-z]+)_\d{4}/', u)
        livro = LIVROS.get(m.group(1), m.group(1)) if m else 'Outros'
        pasta = os.path.join(RAIZ, '5_Acervo', 'Saude', 'Hesperian_EN', re.sub(r'[^\w ()-]', '', livro).replace(' ', '_'))
        nome = urllib.parse.unquote(u.rsplit('/', 1)[-1]).replace(' ', '_')
        res = baixar(u, os.path.join(pasta, nome), b'%PDF')
        cont[res] = cont.get(res, 0) + 1
        time.sleep(0.2)
    escreve('Hesperian EN: %s' % cont)
    gravar_indices(pagina)


def gravar_indices(pagina):
    """_indice.txt de cada livro com o nome real do capitulo (texto do link), lido pelo indice do app."""
    import html
    por_pasta = {}
    for u, texto in re.findall(r'<a href="(https?://[^"]+\.pdf)"[^>]*>([^<]+)</a>', pagina):
        m = re.search(r'/en_([a-z]+)_\d{4}/', u)
        livro = LIVROS.get(m.group(1), m.group(1)) if m else 'Outros'
        pasta = os.path.join(RAIZ, '5_Acervo', 'Saude', 'Hesperian_EN', re.sub(r'[^\w ()-]', '', livro).replace(' ', '_'))
        nome = urllib.parse.unquote(u.rsplit('/', 1)[-1]).replace(' ', '_')
        texto = ' '.join(html.unescape(texto).split())
        num = re.match(r'(\d+)\s+(.*)', texto)
        titulo = '%s — %s (cap. %s)' % (num.group(2), livro, num.group(1)) if num else '%s — %s' % (texto, livro)
        por_pasta.setdefault(pasta, []).append('%s\t%s' % (nome, titulo))
    for pasta, linhas in por_pasta.items():
        if os.path.isdir(pasta):
            with open(os.path.join(pasta, '_indice.txt'), 'w', encoding='utf-8') as f:
                f.write('\n'.join(sorted(linhas)) + '\n')
    escreve('Hesperian EN: _indice.txt gravado em %d livros' % len(por_pasta))


if __name__ == '__main__':
    escreve('===== Refinamento (mapas do celular + Hesperian EN): inicio =====')
    mapas_celular()
    hesperian_en()
    escreve('===== Refinamento: fim. Livre: %.0f GB =====' % (shutil.disk_usage(RAIZ).free / 2**30))
