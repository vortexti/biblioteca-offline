"""Torna os PDFs escaneados pesquisáveis, aos poucos e em segundo plano, com o OCR do Windows.

Ordem: Sobrevivência, Saúde, Água, Exército... e a Embrapa por último; dentro de cada tema, os
menores primeiro. Cada página lida fica guardada no índice (tabela 'ocr'): fechar o app não perde nada.
Para quando a IA está respondendo ou a biblioteca está sendo indexada (para não deixar o PC lento).
"""
import logging
import threading
import time

from . import config, indice, ocr

log = logging.getLogger('biblioteca')

estado = {'rodando': False, 'pausa': '', 'doc': '', 'pagina': 0, 'paginas': 0, 'docs_feitos': 0,
          'paginas_feitas': 0, 'restantes_docs': None, 'restantes_paginas': None, 'erro': '', 'inicio': 0.0}
_thread = None
_trava = threading.Lock()

PRIORIDADE = """CASE
    WHEN tema LIKE 'Sobreviv%' THEN 0 WHEN tema LIKE 'Sa_de%' THEN 1 WHEN tema LIKE '_gua%' THEN 2
    WHEN tema LIKE 'Desastre%' THEN 2 WHEN tema LIKE 'Ex_rcito%' THEN 3 WHEN tema LIKE 'Eletric%' THEN 3
    WHEN tema LIKE 'Mec_nica%' THEN 3 WHEN tema LIKE 'Solda%' THEN 3 WHEN tema LIKE 'Agricultura%' THEN 9
    ELSE 5 END"""
FILTRO = ("situacao = 'sem_texto' AND tipo = 'pdf' AND caminho NOT IN "
          "(SELECT caminho FROM ocr WHERE pagina = 0)")   # pagina 0 = já tentado sem sucesso


def ligar(sim):
    config.salvar({'ocr_fundo': bool(sim)})
    if sim:
        iniciar()


def iniciar():
    global _thread
    with _trava:
        if _thread is not None and _thread.is_alive():
            return
        if not config.obter('ocr_fundo') or config.estado.get('somente_leitura'):
            return
        _thread = threading.Thread(target=_laco, daemon=True, name='ocr-fundo')
        _thread.start()


def resumo():
    e = dict(estado)
    e['ligado'] = bool(config.obter('ocr_fundo'))
    if e['restantes_docs'] is None:
        try:
            con = indice.conexao()
            n, pgs = con.execute('SELECT COUNT(*), COALESCE(SUM(paginas), 0) FROM docs WHERE ' + FILTRO).fetchone()
            e['restantes_docs'], e['restantes_paginas'] = n, pgs
        except Exception:
            pass
    return e


def _esperar_vez():
    """Segura o OCR enquanto o computador está ocupado com algo que a pessoa está esperando."""
    from . import ia
    while config.obter('ocr_fundo'):
        if indice.progresso.get('rodando'):
            estado['pausa'] = 'esperando a indexação terminar'
        elif ia._lock_geracao.locked():
            estado['pausa'] = 'esperando a IA terminar de responder'
        else:
            estado['pausa'] = ''
            return True
        time.sleep(3)
    return False


def _laco():
    estado.update(rodando=True, erro='', inicio=time.time(), docs_feitos=0, paginas_feitas=0)
    time.sleep(20)   # deixa o app, a IA e as enciclopédias abrirem primeiro
    con = None
    titulos = None
    try:
        con = indice._conectar_escrita()
        ok, motivo = ocr.disponivel()
        if not ok:
            estado['erro'] = motivo
            return
        while _esperar_vez():
            n, pgs = con.execute('SELECT COUNT(*), COALESCE(SUM(paginas), 0) FROM docs WHERE ' + FILTRO).fetchone()
            estado.update(restantes_docs=n, restantes_paginas=pgs)
            d = con.execute('SELECT id, caminho, tamanho, paginas, titulo FROM docs WHERE ' + FILTRO +
                            ' ORDER BY ' + PRIORIDADE + ', paginas LIMIT 1').fetchone()
            if not d:
                log.info('OCR em segundo plano: todos os escaneados foram lidos')
                break
            doc_id, rel, tam, total, titulo = d
            estado.update(doc=titulo, pagina=0, paginas=total)
            caminho = config.absoluto(rel)
            erros_seguidos, falhou = 0, ''
            pg = 1
            while pg <= max(1, total):
                if not _esperar_vez():
                    return
                if indice.ocr_feito(con, rel, tam, pg) is None:
                    r = ocr.ler_pagina_pdf(caminho, pg)
                    if r.get('ok'):
                        erros_seguidos = 0
                        total = r.get('paginas') or total
                        indice.salvar_ocr(con, rel, tam, pg, r.get('texto', ''))
                        estado['paginas_feitas'] += 1
                    else:
                        erros_seguidos += 1
                        if erros_seguidos >= 3 or 'não existe' in (r.get('erro') or ''):
                            falhou = r.get('erro') or 'erro'
                            break
                estado['pagina'] = pg
                pg += 1
            if titulos is None:
                titulos = indice._titulos_indice()
            novo = indice.aplicar_ocr(con, doc_id, titulos) if not falhou else None
            ainda = con.execute('SELECT situacao FROM docs WHERE id = ?', (novo,)).fetchone() if novo else None
            if falhou or not ainda or ainda[0] != 'ok':
                # sem texto mesmo depois do OCR (mapa, foto, desenho) ou PDF que o Windows não abre: não tenta de novo
                indice.salvar_ocr(con, rel, tam, 0, 'falhou: ' + falhou if falhou else 'sem texto')
            else:
                estado['docs_feitos'] += 1
            ocr.fechar_pdf()
    except Exception as e:
        log.exception('OCR em segundo plano parou')
        estado['erro'] = str(e)
    finally:
        estado.update(rodando=False, pausa='', doc='')
        if con is not None:
            con.close()
