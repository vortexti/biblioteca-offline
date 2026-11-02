"""Processo auxiliar de extração de texto.

Roda separado do app (python -I trabalhador.py): lê pedidos JSON pela entrada
padrão, um por linha, e responde JSON pela saída. Se um PDF travar ou estourar
a memória, só este processo morre e o app segue.
"""
import json
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
sys.path.insert(1, os.path.join(APP, 'vendor'))

from biblioteca import pdftexto, textos  # noqa: E402


def extrair(caminho, ext):
    if ext == '.pdf':
        r = pdftexto.extrair(caminho)
        return {'titulo': r['titulo'], 'paginas': r['paginas'], 'protegido': r['protegido'], 'idioma': ''}
    if ext == '.epub':
        r = textos.extrair_epub(caminho)
        return {'titulo': r['titulo'], 'paginas': r['paginas'], 'protegido': False, 'idioma': r['idioma']}
    r = textos.extrair_texto_simples(caminho, ext)
    return {'titulo': r['titulo'], 'paginas': r['paginas'], 'protegido': False, 'idioma': ''}


def main():
    entrada = sys.stdin.buffer
    saida = sys.stdout.buffer
    for linha in entrada:
        try:
            ped = json.loads(linha)
        except ValueError:
            continue
        try:
            r = extrair(ped['caminho'], ped['ext'])
            r['ok'] = True
        except MemoryError:
            r = {'ok': False, 'erro': 'arquivo grande demais para a memória'}
        except Exception as e:  # PDF corrompido etc.
            r = {'ok': False, 'erro': '%s: %s' % (type(e).__name__, str(e)[:200])}
        saida.write(json.dumps(r).encode('ascii') + b'\n')
        saida.flush()


if __name__ == '__main__':
    main()
