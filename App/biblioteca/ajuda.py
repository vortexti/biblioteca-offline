"""Assistente de ajuda: responde dúvidas sobre como usar o app, com base no guia (static/ajuda/guia.json)."""
import html
import json
import math
import os
import re

from . import config, ia, textos

ARQ = os.path.join(config.STATIC, 'ajuda', 'guia.json')
_cache = {'mtime': None, 'secoes': []}

SISTEMA = (
    'Você é o assistente de ajuda da "Biblioteca Offline", um app que funciona sem internet. Explique como usar o '
    'app em português do Brasil, com palavras simples, curto e em passos numerados quando for um procedimento. Use '
    'SOMENTE o GUIA abaixo; diga o nome exato das telas e botões como estão no guia. Não invente botões, atalhos, nomes de Wi-Fi nem recursos que não estejam no guia. '
    'Se o guia não responder, diga que não sabe e indique a tela Situação ou a Ajuda. Se a pessoa estiver pedindo um '
    'conteúdo (por exemplo como tratar uma febre ou como plantar), não responda o conteúdo: explique que isso se '
    'procura na busca do Início ou na tela Pergunte, e sugira as palavras para buscar.'
)


def secoes():
    try:
        mt = os.path.getmtime(ARQ)
    except OSError:
        return []
    if _cache['mtime'] != mt:
        with open(ARQ, encoding='utf-8') as f:
            dados = json.load(f)
        for s in dados:
            t = re.sub(r'<li>', '\n- ', s['html'])
            t = re.sub(r'</p>|</ol>|</ul>', '\n', t)
            s['texto'] = html.unescape(re.sub(r'<[^>]+>', '', t)).strip()
        _cache.update(mtime=mt, secoes=dados)
    return _cache['secoes']


VAZIAS = set("""a o e as os um uma uns umas de do da dos das no na nos nas em por para pra com sem que se como
onde quando qual quais quem eu voce você meu minha isso isto esse essa este esta ele ela eles elas ao aos à às ou
mais menos muito muita já ja ta tá está esta estao estão é eh ser ter tem fazer faço faz pode posso consigo
usar uso app aplicativo biblioteca coisa coisas sobre aqui ali la lá""".split())


def _palavras(texto):
    return [w for w in re.findall(r'[a-z0-9]+', textos.sem_acento((texto or '').lower())) if w not in VAZIAS]


def _casa(t, palavras):
    # 'celular' casa com 'celulares'; termo curto ('ia', 'gps') só com a palavra inteira
    return t in palavras if len(t) <= 3 else any(w.startswith(t[:max(4, len(t) - 2)]) for w in palavras)


def escolher(pergunta, n=3):
    """As seções do guia que mais têm a ver com a pergunta (palavras-chave valem mais que o texto)."""
    ts = set(_palavras(pergunta))
    if not ts:
        return []
    todas = [(s, set(_palavras(s['palavras'] + ' ' + s['titulo'])), set(_palavras(s['texto']))) for s in secoes()]
    # palavra que aparece em quase todas as seções vale pouco; a rara ('celular', 'kiwix') vale muito
    peso = {t: math.log(1 + len(todas) / max(1, sum(1 for _, c, b in todas if _casa(t, c) or _casa(t, b)))) for t in ts}
    notas = []
    for s, chave, corpo in todas:
        nota = sum(3 * peso[t] for t in ts if _casa(t, chave)) + sum(peso[t] for t in ts if _casa(t, corpo))
        if nota:
            notas.append((nota, s))
    notas.sort(key=lambda x: -x[0])
    return [s for _, s in notas[:n]]


def perguntar(pergunta):
    pergunta = (pergunta or '').strip()[:500]
    if not pergunta:
        yield {'tipo': 'erro', 'texto': 'Escreva sua dúvida sobre o app.'}
        return
    achadas = escolher(pergunta) or [s for s in secoes() if s['id'] == 'comece']
    yield {'tipo': 'secoes', 'secoes': [{'id': s['id'], 'titulo': s['titulo'], 'resumo': s['resumo']} for s in achadas]}
    ok, motivo = ia.disponivel()
    if not ok:
        yield {'tipo': 'texto', 'texto': 'A IA não está disponível agora, mas as partes do guia acima explicam.'}
        yield {'tipo': 'fim'}
        return
    for ev in ia._ligar_com_status():
        yield ev
        if ev['tipo'] == 'erro':
            return
    guia, total = [], 0
    for s in achadas:
        bloco = '## %s\n%s' % (s['titulo'], s['texto'])
        if total + len(bloco) > 3600:
            bloco = bloco[:max(0, 3600 - total)]
        guia.append(bloco)
        total += len(bloco)
        if total >= 3600:
            break
    yield {'tipo': 'status', 'texto': 'Escrevendo a explicação…'}
    msgs = [{'role': 'system', 'content': SISTEMA},
            {'role': 'user', 'content': 'GUIA:\n\n%s\n\nDÚVIDA: %s' % ('\n\n'.join(guia), pergunta)}]
    for tipo, t in ia.conversar(msgs, max_tokens=350, temperatura=0.2, pensar=False):
        if tipo == 'texto':
            yield {'tipo': 'texto', 'texto': t}
    yield {'tipo': 'fim'}
