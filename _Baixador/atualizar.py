"""BIBLIOTECA OFFLINE - atualizacao pela internet (chamado pelo botao "Atualizar pela internet" do app).

Partes:
  zims    enciclopedias (Kiwix): para cada .zim do SSD procura no catalogo oficial uma versao mais nova;
          se couber (sobrando RESERVA_GB), baixa a nova e depois apaga a antiga.
  acervo  livros e manuais: novidades da Embrapa e do Exercito (so o que entrou desde a ultima coleta),
          SciELO/Ministerio da Saude (tenta de novo o que faltou) e capitulos novos da Hesperian.

Uso:  python atualizar.py --verificar            (so mostra o que ha de novo; nao baixa)
      python atualizar.py [--partes zims,acervo]  (atualiza)
O estado vai para atualizacao_estado.json (o app le e mostra na tela Situacao).
Tudo e retomavel: se for interrompido, rodar de novo continua de onde parou.
"""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
ZIMS = os.path.join(RAIZ, '1_Enciclopedia')
ACERVO = os.path.join(RAIZ, '5_Acervo')
ESTADO = os.path.join(AQUI, 'atualizacao_estado.json')
LOG = os.path.join(AQUI, 'registro.txt')
PYTHON = sys.executable
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
RESERVA_GB = 8   # folga mínima que sempre fica livre no SSD (índice de busca, OCR, sistema)
SEM_JANELA = 0x08000000 if os.name == 'nt' else 0

sys.path.insert(0, AQUI)
import baixar_conhecimento as bc  # noqa: E402  (catalogo do Kiwix e conferencia de ZIM completo)

estado = {}


def gravar_estado(**mudancas):
    estado.update(mudancas)
    estado['atualizado'] = time.strftime('%Y-%m-%d %H:%M:%S')
    tmp = ESTADO + '.tmp'
    for _ in range(5):
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(estado, f, ensure_ascii=False, indent=1)
            os.replace(tmp, ESTADO)
            return
        except OSError:
            time.sleep(0.3)


def escreve(msg):
    linha = '[%s] %s' % (time.strftime('%d/%m %H:%M'), msg)
    print(linha, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(linha + '\n')
    except OSError:
        pass
    gravar_estado(mensagem=msg)


def tem_internet():
    for url in ('https://library.kiwix.org/', 'https://www.google.com/generate_204'):
        try:
            urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=15).close()
            return True
        except Exception:
            pass
    return False


def livre():
    return shutil.disk_usage(RAIZ).free


# ------------------------------------------------------------------ enciclopedias

_NOME_ZIM = re.compile(r'^(.+?)(?:_(maxi|nopic|mini))?_(\d{4}-\d{2})[a-z]?\.zim$')


def zims_locais():
    r = []
    for f in sorted(os.listdir(ZIMS)):
        m = _NOME_ZIM.match(f)
        if m and os.path.exists(os.path.join(ZIMS, f + '.ok')):
            r.append({'arquivo': f, 'nome': m.group(1), 'sabor': m.group(2) or '', 'data': m.group(3)})
    return r


def verificar_zims():
    locais = zims_locais()
    escreve('Procurando versões novas de %d enciclopédias no catálogo do Kiwix...' % len(locais))

    def consulta(z):
        try:
            return z, bc.procurar(z['nome'], z['sabor'])
        except Exception:
            return z, None

    novos = []
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        for z, achado in ex.map(consulta, locais):
            if not achado:
                continue
            url, tam = achado
            data = (re.search(r'_(\d{4}-\d{2})[a-z]?\.zim$', url) or [None, ''])[1]
            novo_arq = url.rsplit('/', 1)[-1]
            if data > z['data'] and novo_arq != z['arquivo']:
                novos.append({'arquivo': z['arquivo'], 'novo': novo_arq, 'url': url, 'data_atual': z['data'],
                              'data_nova': data, 'gb': round(tam / 2**30, 2), 'bytes': tam})
    novos.sort(key=lambda x: x['bytes'])
    # quais cabem (baixando os menores primeiro e apagando a versão antiga depois de cada um)
    sobra = livre() - RESERVA_GB * 2**30
    for n in novos:
        ja = os.path.getsize(os.path.join(ZIMS, n['novo'])) if os.path.exists(os.path.join(ZIMS, n['novo'])) else 0
        precisa = n['bytes'] - ja
        n['cabe'] = precisa <= sobra
        if n['cabe']:
            antigo = os.path.getsize(os.path.join(ZIMS, n['arquivo']))
            sobra += antigo - n['bytes']
    return novos


def baixar_zim(n):
    destino = os.path.join(ZIMS, n['novo'])
    if os.path.exists(destino + '.ok'):
        return True
    for t in range(1, 6):
        if livre() - max(0, n['bytes'] - (os.path.getsize(destino) if os.path.exists(destino) else 0)) < RESERVA_GB * 2**30:
            escreve('Sem espaço para %s; deixei para depois.' % n['novo'])
            return False
        p = subprocess.Popen(['curl.exe', '-s', '-S', '-L', '-f', '-C', '-', '--retry', '5', '--retry-delay', '15',
                              '--connect-timeout', '30', '--speed-limit', '20000', '--speed-time', '120',
                              '-A', UA, '-o', destino, n['url']], creationflags=SEM_JANELA)
        while p.poll() is None:
            feito = os.path.getsize(destino) if os.path.exists(destino) else 0
            gravar_estado(progresso=round(min(1.0, feito / max(1, n['bytes'])), 3))
            time.sleep(3)
        if bc.zim_completo(destino, n['bytes']):
            with open(destino + '.ok', 'w', encoding='utf-8') as f:
                f.write(n['url'] + '\n')
            return True
        escreve('Download interrompido (tentativa %d): %s' % (t, n['novo']))
        time.sleep(20)
    return False


def remover_antigo(arquivo):
    """Apaga a versão antiga; se o kiwix-serve do app estiver com ela aberta, o app apaga ao reiniciá-lo."""
    caminho = os.path.join(ZIMS, arquivo)
    try:
        os.remove(caminho)
        if os.path.exists(caminho + '.ok'):
            os.remove(caminho + '.ok')
        return True
    except OSError:
        pend = estado.setdefault('remover', [])
        if arquivo not in pend:
            pend.append(arquivo)
        gravar_estado()
        return False


def atualizar_zims():
    novos = verificar_zims()
    feitos, sem_espaco, falhas = [], [], []
    for i, n in enumerate(novos, 1):
        if not n['cabe']:
            sem_espaco.append(n)
            continue
        gravar_estado(etapa='Enciclopédias: %d de %d' % (i, len(novos)), progresso=0)
        escreve('Baixando %s (%.2f GB) para trocar %s' % (n['novo'], n['gb'], n['arquivo']))
        if baixar_zim(n):
            remover_antigo(n['arquivo'])
            feitos.append(n)
            escreve('Atualizada: %s' % n['novo'])
        else:
            falhas.append(n)
    gravar_estado(progresso=None)
    return {'atualizadas': [n['novo'] for n in feitos], 'sem_espaco': [n['novo'] for n in sem_espaco],
            'falhas': [n['novo'] for n in falhas]}


# ------------------------------------------------------------------ livros e manuais

def contar_ok():
    n = 0
    for base, _, arqs in os.walk(ACERVO):
        n += sum(1 for a in arqs if a.endswith('.ok'))
    return n


def reabrir_catalogo(arq):
    """Faz o baixador da Embrapa/Exército pedir só o que entrou depois da última coleta (OAI 'from')."""
    caminho = os.path.join(AQUI, arq)
    if not os.path.exists(caminho):
        return
    try:
        cat = json.load(open(caminho, encoding='utf-8'))
    except ValueError:
        return
    if not cat.get('completo'):
        return  # coleta anterior ainda em andamento: o baixador continua de onde parou
    desde = cat.get('coletado_em') or time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(caminho) - 86400))
    cat.update({'completo': False, 'token': None, 'desde': desde})
    with open(caminho + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(cat, f, ensure_ascii=False)
    os.replace(caminho + '.tmp', caminho)


def rodar(script, rotulo):
    escreve('%s: procurando novidades...' % rotulo)
    env = dict(os.environ, BIBLIOTECA_RESERVA_GB=str(RESERVA_GB))   # mesma folga para todos os baixadores
    p = subprocess.Popen([PYTHON, '-I', '-X', 'utf8', os.path.join(AQUI, script)], cwd=AQUI, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=SEM_JANELA, encoding='utf-8', errors='replace')
    for linha in p.stdout:
        linha = re.sub(r'^\[[^\]]*\]\s*', '', linha.strip())
        if linha and not linha.startswith('====='):
            gravar_estado(mensagem='%s: %s' % (rotulo, linha[:200]))
    return p.wait()


def atualizar_acervo():
    antes = contar_ok()
    reabrir_catalogo('embrapa_catalogo.json')
    reabrir_catalogo('exercito_catalogo.json')
    etapas = [('baixar_embrapa.py', 'Embrapa'), ('baixar_exercito.py', 'Exército Brasileiro'),
              ('baixar_pt.py', 'SciELO e Ministério da Saúde'), ('baixar_refino.py', 'Hesperian e mapas do celular')]
    erros = []
    for i, (script, rotulo) in enumerate(etapas, 1):
        gravar_estado(etapa='Livros e manuais: %d de %d (%s)' % (i, len(etapas), rotulo))
        if rodar(script, rotulo) != 0:
            erros.append(rotulo)
    return {'novos_arquivos': max(0, contar_ok() - antes), 'erros': erros}


# ------------------------------------------------------------------ principal

def main():
    verificar = '--verificar' in sys.argv
    partes = ['zims', 'acervo']
    if '--partes' in sys.argv:
        partes = [p for p in sys.argv[sys.argv.index('--partes') + 1].split(',') if p in ('zims', 'acervo')]
    anterior = {}
    try:
        anterior = json.load(open(ESTADO, encoding='utf-8'))
    except (OSError, ValueError):
        pass
    estado.clear()
    estado.update({'modo': 'verificar' if verificar else 'atualizar', 'partes': partes, 'rodando': True,
                   'inicio': time.strftime('%Y-%m-%d %H:%M:%S'), 'fim': None, 'etapa': 'Começando',
                   'resultado': None, 'erro': None, 'pid': os.getpid(),
                   'remover': anterior.get('remover', []), 'ultima_atualizacao': anterior.get('ultima_atualizacao')})
    gravar_estado()
    escreve('===== Atualização pela internet (%s: %s): início =====' % (estado['modo'], ', '.join(partes)))
    try:
        if not tem_internet():
            gravar_estado(erro='Sem internet. Conecte o computador e tente de novo.')
            return
        resultado = {}
        if verificar:
            gravar_estado(etapa='Verificando enciclopédias')
            novos = verificar_zims()
            resultado['zims'] = novos
            resultado['livre_gb'] = round(livre() / 2**30, 1)
            escreve('Verificação: %d enciclopédias com versão nova (%d cabem agora).'
                    % (len(novos), sum(1 for n in novos if n['cabe'])))
        else:
            if 'zims' in partes:
                gravar_estado(etapa='Enciclopédias')
                resultado['zims'] = atualizar_zims()
            if 'acervo' in partes:
                resultado['acervo'] = atualizar_acervo()
            resultado['livre_gb'] = round(livre() / 2**30, 1)
            estado['ultima_atualizacao'] = time.strftime('%Y-%m-%d %H:%M')
            escreve('Atualização concluída.')
        gravar_estado(resultado=resultado)
    except Exception as e:
        gravar_estado(erro='%s: %s' % (type(e).__name__, e))
        raise
    finally:
        gravar_estado(rodando=False, fim=time.strftime('%Y-%m-%d %H:%M:%S'), etapa='Fim', progresso=None)
        escreve('===== Atualização: fim =====')


if __name__ == '__main__':
    main()
