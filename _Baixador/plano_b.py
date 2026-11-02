"""BIBLIOTECA OFFLINE - "plano B": completa a biblioteca na ordem de valor para sobrevivencia.

1. Espera haver espaco livre (a pasta D:\\_PARA_APAGAR precisa ser apagada a mao: Shift+Delete).
2. Embrapa: livros e manuais primeiro, depois folders e folhetos (baixar_embrapa.py).
3. Manuais do Exercito Brasileiro (baixar_exercito.py).
4. Enciclopedias: MDWiki sem videos e, com o que sobrar, Gutenberg de historia, artes,
   filosofia e literatura (baixar_conhecimento.py --p3-reserva 20).
Sempre deixa 20 GB livres. Retomavel: pode fechar e abrir de novo.
"""
import os
import shutil
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)
PY = sys.executable
PRECISA_GB = 120


def livre_gb():
    return shutil.disk_usage(RAIZ).free / 2**30


def rodar(script, *args):
    print('\n>>> %s %s\n' % (script, ' '.join(args)), flush=True)
    subprocess.call([PY, '-I', '-X', 'utf8', os.path.join(AQUI, script)] + list(args), cwd=AQUI)


def main():
    if livre_gb() < PRECISA_GB:
        print('\n  Espaco livre: %.0f GB. Para continuar, apague a pasta D:\\_PARA_APAGAR' % livre_gb())
        print('  (clique nela, aperte Shift+Delete e confirme). Este programa espera e continua sozinho.\n',
              flush=True)
        while livre_gb() < PRECISA_GB:
            time.sleep(30)
        print('  Espaco liberado: %.0f GB livres. Comecando.' % livre_gb(), flush=True)
    rodar('baixar_embrapa.py')
    rodar('baixar_exercito.py')
    rodar('baixar_conhecimento.py', '--p3-reserva', '20')
    print('\n  Plano B concluido. Abra a biblioteca e clique em Situacao > "Atualizar biblioteca".\n', flush=True)


if __name__ == '__main__':
    main()
