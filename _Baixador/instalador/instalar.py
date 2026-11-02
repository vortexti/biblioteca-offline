"""BIBLIOTECA OFFLINE - instalador a partir do kit.

Recria a biblioteca inteira na pasta onde o kit foi descompactado (de preferência a raiz de um HD/SSD
vazio de 1 TB): baixa cada arquivo do mesmo endereço de onde a biblioteca original foi baixada
(lista em manifesto.tsv.gz), instala os programas e refaz o que é gerado aqui (mapa do PC, textos
religiosos). Retomável: se parar (internet, energia, fechou a janela), rode de novo e ele continua.

Uso:  INSTALAR_BIBLIOTECA.bat              (instala / continua)
      INSTALAR_BIBLIOTECA.bat --verificar  (só mostra o que falta, não baixa nada)
"""
import concurrent.futures
import gzip
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
BAIXADOR = os.path.dirname(AQUI)
RAIZ = os.path.dirname(BAIXADOR)
MANIFESTO = os.path.join(AQUI, 'manifesto.tsv.gz')
REGISTRO = os.path.join(AQUI, 'instalacao_registro.txt')
TROCAS = os.path.join(AQUI, 'instalacao_trocas.json')
PYTHON = sys.executable
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
FOLGA_GB = 5
PARALELO = 6
SEM_JANELA = 0x08000000 if os.name == 'nt' else 0
GRUPOS = {1: 'Programas e IA', 2: 'Saúde, sobrevivência e religião', 3: 'Enciclopédias essenciais, mapas e instaladores',
          4: 'Agricultura, Exército, livros acadêmicos', 5: 'Demais enciclopédias', 6: 'Gutenberg, IAs extras e o resto'}
PROGRAMAS = [  # zip em 6_Instaladores -> pasta em Programas
    ('kiwix-tools_win', 'KiwixServe'), ('kiwix-desktop_windows', 'Kiwix'), ('llama-b', 'LlamaCpp')]

sys.path.insert(0, BAIXADOR)
_trava = threading.Lock()
estado = {'feitos': 0, 'bytes': 0, 'falhas': [], 'sem_espaco': [], 'trocados': {}, 'diferentes': 0,
          'agora': '', 'inicio': time.time(), 'total_bytes': 0, 'total_arquivos': 0}


def escreve(msg, tela=True):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    if tela:
        print(linha, flush=True)
    with _trava:
        with open(REGISTRO, 'a', encoding='utf-8') as f:
            f.write(linha + '\n')


def gb(n):
    return '%.1f' % (n / 2**30)


def livre():
    return shutil.disk_usage(RAIZ).free


def ler_manifesto():
    itens = []
    with gzip.open(MANIFESTO, 'rt', encoding='utf-8') as f:
        for linha in f:
            if linha.startswith('#') or not linha.strip():
                continue
            prio, tipo, rel, tam, url = linha.rstrip('\n').split('\t')
            itens.append({'prio': int(prio), 'tipo': tipo, 'rel': rel, 'tam': int(tam or 0), 'url': url})
    return itens


def caminho(rel):
    return os.path.join(RAIZ, *rel.split('/'))


def pronto(item):
    p = caminho(item['rel'])
    if os.path.exists(p + '.ok') and os.path.isfile(p) and os.path.getsize(p) > 0:
        return True
    novo = estado['trocados'].get(item['rel'])
    return bool(novo and os.path.exists(caminho(novo) + '.ok'))


MAGICOS = {'.pdf': b'%PDF', '.epub': b'PK', '.zip': b'PK', '.apk': b'PK', '.zim': b'ZIM\x04', '.gguf': b'GGUF'}


def conteudo_certo(p, rel):
    ext = os.path.splitext(rel)[1].lower()
    try:
        with open(p, 'rb') as f:
            ini = f.read(8)
    except OSError:
        return False
    if ext in MAGICOS:
        return ini.startswith(MAGICOS[ext])
    return len(ini) > 0 and not ini.lstrip().lower().startswith((b'<!doc', b'<html'))


def baixar_url(url, destino, tam):
    """Baixa para destino.part (retomável para arquivos grandes) e só no fim troca o nome."""
    parcial = destino + '.part'
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    if tam > 50 * 2**20 or tam == 0 and url.endswith(('.zim', '.gguf', '.map', '.exe')):
        for t in range(1, 6):
            subprocess.call(['curl.exe', '-s', '-S', '-L', '-f', '-C', '-', '--retry', '5', '--retry-delay', '15',
                             '--connect-timeout', '30', '--speed-limit', '20000', '--speed-time', '120',
                             '-A', UA, '-o', parcial, url], creationflags=SEM_JANELA,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            feito = os.path.getsize(parcial) if os.path.exists(parcial) else 0
            if feito and (not tam or feito >= tam):
                break
            time.sleep(10 * t)
    else:
        for t in range(1, 5):
            try:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                with urllib.request.urlopen(req, timeout=180) as r, open(parcial, 'wb') as f:
                    shutil.copyfileobj(r, f, 1 << 16)
                break
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    raise
                time.sleep(8 * t)
            except Exception:
                time.sleep(8 * t)
    if not os.path.exists(parcial) or os.path.getsize(parcial) == 0:
        raise RuntimeError('não baixou')
    os.replace(parcial, destino)


def zim_novo(item):
    """Enciclopédia cujo arquivo saiu do ar: pega a versão mais nova do mesmo livro no catálogo do Kiwix."""
    import re
    import baixar_conhecimento as bc
    nome = os.path.basename(item['rel'])
    m = re.match(r'^(.+?)(?:_(maxi|nopic|mini))?_(\d{4}-\d{2})[a-z]?\.zim$', nome)
    if not m:
        return None
    achado = bc.procurar(m.group(1), m.group(2) or '')
    if not achado:
        return None
    url, tam = achado
    return url, '1_Enciclopedia/' + url.rsplit('/', 1)[-1], tam


def instalar_item(item):
    if pronto(item):
        return 'ja'
    p = caminho(item['rel'])
    falta = item['tam'] - (os.path.getsize(p + '.part') if os.path.exists(p + '.part') else 0)
    if livre() - max(0, falta) < FOLGA_GB * 2**30:
        return 'espaco'
    url, rel, tam = item['url'], item['rel'], item['tam']
    if tam > 200 * 2**20:
        estado['agora'] = os.path.basename(rel)
    try:
        baixar_url(url, caminho(rel), tam)
    except Exception as e:
        if item['tipo'] != 'zim':
            escreve('FALHOU: %s (%s)' % (rel, e), tela=False)
            return 'falha'
        novo = zim_novo(item)
        if not novo:
            escreve('FALHOU (e não há versão nova no catálogo): %s' % rel)
            return 'falha'
        url, rel, tam = novo
        escreve('Enciclopédia fora do ar; baixando a versão nova: %s' % os.path.basename(rel))
        try:
            baixar_url(url, caminho(rel), tam)
        except Exception as e2:
            escreve('FALHOU: %s (%s)' % (rel, e2))
            return 'falha'
        with _trava:
            estado['trocados'][item['rel']] = rel
            with open(TROCAS, 'w', encoding='utf-8') as f:
                json.dump(estado['trocados'], f, ensure_ascii=False, indent=1)
    p = caminho(rel)
    if not conteudo_certo(p, rel):
        os.remove(p)
        escreve('FALHOU (o site devolveu outra coisa): %s' % rel, tela=False)
        return 'falha'
    if tam and os.path.getsize(p) != tam:
        estado['diferentes'] += 1   # o arquivo mudou no site desde a biblioteca original; vale igual
    with open(p + '.ok', 'w', encoding='utf-8') as f:
        f.write(url + '\n')
    if os.path.getsize(p) > 200 * 2**20:
        escreve('OK: %s (%s GB)' % (os.path.basename(rel), gb(os.path.getsize(p))))
    return 'ok'


def mostrar_andamento(parar):
    while not parar.wait(30):
        dt = max(1, time.time() - estado['inicio'])
        vel = estado['bytes'] / dt
        falta = max(0, estado['total_bytes'] - estado['bytes'])
        eta = falta / vel if vel > 1e5 else 0
        print('   … %d de %d arquivos · %s de %s GB · %.1f MB/s%s%s' % (
            estado['feitos'], estado['total_arquivos'], gb(estado['bytes']), gb(estado['total_bytes']), vel / 2**20,
            ' · faltam ~%d h %02d min' % (eta // 3600, eta % 3600 // 60) if eta else '',
            ' · agora: ' + estado['agora'] if estado['agora'] else ''), flush=True)


def instalar_programas():
    for prefixo, pasta in PROGRAMAS:
        destino = os.path.join(RAIZ, 'Programas', pasta)
        marca = os.path.join(destino, '.instalado')
        zips = sorted(f for f in os.listdir(os.path.join(RAIZ, '6_Instaladores')) if f.startswith(prefixo) and f.endswith('.zip')
                      and os.path.exists(os.path.join(RAIZ, '6_Instaladores', f + '.ok'))) \
            if os.path.isdir(os.path.join(RAIZ, '6_Instaladores')) else []
        if not zips or os.path.exists(marca):
            continue
        z = zipfile.ZipFile(os.path.join(RAIZ, '6_Instaladores', zips[-1]))
        nomes = [n for n in z.namelist() if not n.endswith('/')]
        topo = {n.split('/')[0] for n in nomes}
        corta = (len(topo) == 1 and all('/' in n for n in nomes))   # zip com uma pasta só: tira a pasta
        os.makedirs(destino, exist_ok=True)
        for n in nomes:
            alvo = os.path.join(destino, *(n.split('/')[1:] if corta else n.split('/')))
            os.makedirs(os.path.dirname(alvo), exist_ok=True)
            with z.open(n) as a, open(alvo, 'wb') as b:
                shutil.copyfileobj(a, b)
        for dll in ('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'):   # runtime do Visual C++ levado no kit
            o = os.path.join(RAIZ, 'App', 'runtime', dll)
            if os.path.exists(o) and not os.path.exists(os.path.join(destino, dll)) and pasta != 'Kiwix':
                shutil.copy2(o, destino)
        open(marca, 'w').write(zips[-1])
        escreve('Programa instalado: %s (de %s)' % (pasta, zips[-1]))


def rodar_script(nome, rotulo, *args):
    escreve('%s…' % rotulo)
    env = dict(os.environ, BIBLIOTECA_RESERVA_GB=str(FOLGA_GB))
    r = subprocess.call([PYTHON, '-I', '-X', 'utf8', os.path.join(BAIXADOR, nome)] + list(args), cwd=BAIXADOR, env=env)
    if r != 0:
        escreve('%s terminou com problema (código %s); rode a instalação de novo depois.' % (rotulo, r))


def main():
    print('=' * 72)
    print('  BIBLIOTECA OFFLINE — instalação')
    print('=' * 72)
    if os.name == 'nt' and sys.getwindowsversion().major < 10:
        print('Atenção: a biblioteca precisa do Windows 10 ou 11.')
    itens = ler_manifesto()
    if os.path.exists(TROCAS):
        estado['trocados'] = json.load(open(TROCAS, encoding='utf-8'))
    faltam = [i for i in itens if i['tipo'] != 'gerado' and not pronto(i)]
    # só para testar o kit: --max-mb 50 (pula arquivos maiores) --limite 30 (no máximo 30 por parte)
    if '--max-mb' in sys.argv:
        teto = float(sys.argv[sys.argv.index('--max-mb') + 1]) * 2**20
        faltam = [i for i in faltam if i['tam'] <= teto]
    if '--limite' in sys.argv:
        n = int(sys.argv[sys.argv.index('--limite') + 1])
        faltam = [i for p in sorted({x['prio'] for x in faltam}) for i in [x for x in faltam if x['prio'] == p][:n]]
    total = sum(i['tam'] for i in faltam)
    print('Pasta da biblioteca: %s' % RAIZ)
    print('Espaço livre aqui: %s GB · ainda falta baixar: %d arquivos, %s GB' % (gb(livre()), len(faltam), gb(total)))
    if '--verificar' in sys.argv:
        por = {}
        for i in faltam:
            k = '/'.join(i['rel'].split('/')[:2 if i['rel'].startswith(('5_Acervo', '2_Mapas')) else 1])
            por[k] = (por.get(k, (0, 0))[0] + 1, por.get(k, (0, 0))[1] + i['tam'])
        for k, (n, t) in sorted(por.items()):
            print('   falta %-45s %6d arquivos  %8s GB' % (k, n, gb(t)))
        return
    if livre() < total + FOLGA_GB * 2**30:
        print('\nO espaço livre NÃO dá para tudo. Vou instalar por ordem de importância (o essencial primeiro)')
        print('e pular o que não couber. Para a biblioteca completa use um HD/SSD de 1 TB vazio.\n')
    try:
        urllib.request.urlopen(urllib.request.Request('https://download.kiwix.org/', headers={'User-Agent': UA}), timeout=20)
    except Exception:
        print('Sem internet. Conecte o computador e rode de novo.')
        return
    print('Isto leva de horas a alguns dias (depende da internet). Pode deixar rodando; se parar, rode de novo.\n')
    escreve('===== instalação: início (%d arquivos, %s GB a baixar) =====' % (len(faltam), gb(total)))
    estado.update(total_bytes=total, total_arquivos=len(faltam), inicio=time.time())
    parar = threading.Event()
    threading.Thread(target=mostrar_andamento, args=(parar,), daemon=True).start()
    try:
        for prio in sorted({i['prio'] for i in faltam}):
            grupo = [i for i in faltam if i['prio'] == prio]
            escreve('--- Parte %d de %d: %s (%d arquivos, %s GB) ---' % (prio, max(GRUPOS), GRUPOS.get(prio, ''),
                                                                         len(grupo), gb(sum(i['tam'] for i in grupo))))
            grupo.sort(key=lambda i: i['tam'])   # pequenos primeiro: a biblioteca fica útil mais cedo
            with concurrent.futures.ThreadPoolExecutor(PARALELO) as ex:
                futuros = {ex.submit(instalar_item, i): i for i in grupo}
                for fut in concurrent.futures.as_completed(futuros):   # conta conforme cada um termina
                    item = futuros[fut]
                    try:
                        res = fut.result()
                    except Exception as e:
                        escreve('FALHOU: %s (%s)' % (item['rel'], e), tela=False)
                        res = 'falha'
                    estado['feitos'] += 1
                    estado['bytes'] += item['tam']
                    if res == 'falha':
                        estado['falhas'].append(item['rel'])
                    elif res == 'espaco':
                        estado['sem_espaco'].append(item['rel'])
            estado['agora'] = ''
            if prio == 1:
                instalar_programas()
    finally:
        parar.set()
    instalar_programas()
    # o que é gerado aqui, não baixado pronto
    if '--sem-mapa' not in sys.argv:
        rodar_script('baixar_mapa_pc.py', 'Mapa do Brasil para o PC (recorte do mapa-múndi; ~6 GB)')
    rodar_script('baixar_religiao.py', 'Textos religiosos (Bíblias, Torá, Alcorão)')
    print()
    escreve('===== instalação: fim =====')
    print('Baixados nesta rodada: %d arquivos.' % (estado['feitos'] - len(estado['falhas']) - len(estado['sem_espaco'])))
    if estado['diferentes']:
        print('%d arquivos tinham mudado no site desde a biblioteca original (versões novas; tudo bem).' % estado['diferentes'])
    if estado['trocados']:
        print('%d enciclopédias foram trocadas pela versão mais nova.' % len(estado['trocados']))
    if estado['sem_espaco']:
        print('NÃO couberam %d arquivos (falta espaço). Lista no registro: %s' % (len(estado['sem_espaco']), REGISTRO))
        escreve('sem espaço: ' + ' | '.join(estado['sem_espaco'][:500]), tela=False)
    if estado['falhas']:
        print('FALHARAM %d arquivos (site fora do ar ou endereço mudou). Rode a instalação de novo mais tarde;' % len(estado['falhas']))
        print('se continuar, os baixadores em _Baixador (ex.: BAIXAR_EMBRAPA.bat) buscam pelo catálogo atual.')
        escreve('falhas: ' + ' | '.join(estado['falhas'][:2000]), tela=False)
    print('\nPRONTO. Para usar: dois cliques em ABRIR_BIBLIOTECA.bat.')
    print('Na primeira vez o app monta o índice de busca (algumas horas; dá para ir usando enquanto isso).')


if __name__ == '__main__':
    main()
