"""Biblioteca Offline — ponto de entrada.

Chamado pelo ABRIR_BIBLIOTECA.bat:  Programas\\Python\\python.exe App\\main.py
Opções:  --sem-navegador   não abre o navegador (testes)
"""
import os
import sys
import time

APP = os.path.dirname(os.path.abspath(__file__))
# o Python embutido (._pth) não põe a pasta do script no sys.path
sys.path.insert(0, APP)
sys.path.insert(1, os.path.join(APP, 'vendor'))

from biblioteca import atualizacao, config, ia, indice, kiwix, ocr_lote, servidor, sistema  # noqa: E402


def escrever(msg=''):
    try:
        print(msg, flush=True)
    except (OSError, UnicodeEncodeError):
        pass


def main():
    inicio = time.time()
    try:
        os.system('title Biblioteca Offline - feche esta janela para sair')
    except Exception:
        pass
    config.testar_escrita()
    config.iniciar_log()
    config.carregar()
    log = config.log
    log.info('===== iniciando (raiz %s, Python %s) =====', config.RAIZ, sys.version.split()[0])

    # 1. servidor web do app
    porta = sistema.porta_livre(8800)
    config.estado['porta_app'] = porta
    servidor.iniciar(porta)
    url = 'http://127.0.0.1:%d/' % porta
    log.info('servidor do app em %s (%.1fs)', url, time.time() - inicio)

    # 2. enciclopédias (em segundo plano; a página já abre)
    atualizacao.limpar_pendentes()  # versões antigas que a última atualização deixou para trás
    kiwix.iniciar(8801 if porta != 8801 else 8802, evitar=(porta,))

    # 3. IA: só liga quando for usada; aqui só o vigia de ociosidade
    ia.vigiar_ociosidade()
    ia.aquecer()
    ocr_lote.iniciar()   # PDFs escaneados vão entrando na busca aos poucos

    # 4. modo rede da casa, se ficou ligado da última vez
    if config.obter('rede_casa'):
        servidor.ligar_rede(True)

    # 5. índice: arquivos novos entram sozinhos
    indice.atualizar()

    escrever('')
    escrever('  BIBLIOTECA OFFLINE')
    escrever('  ------------------')
    escrever('  Aberta no navegador:  %s' % url)
    if config.estado['aviso_disco']:
        escrever('  AVISO: %s' % config.estado['aviso_disco'])
    escrever('')
    escrever('  Para SAIR, feche esta janela. (Tudo é encerrado junto.)')
    escrever('')

    if '--sem-navegador' not in sys.argv:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception as e:
            escrever('  Abra o navegador e digite: %s  (%s)' % (url, e))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        log.info('encerrando')
        ia.desligar('(saindo)')
        kiwix.parar()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            config.log.exception('falha ao iniciar')
        except Exception:
            pass
        escrever('')
        escrever('  ERRO ao abrir a Biblioteca: %s' % e)
        escrever('  Detalhes em App\\dados\\log.txt')
        try:
            input('  Pressione ENTER para fechar.')
        except EOFError:
            pass
