# Biblioteca Offline — Especificação do app

Documento para o Claude Code construir o app. Escrito a partir de uma conversa de planejamento com o Breno (dono do projeto). Responda e comente sempre em **português do Brasil**.

## 1. O que é

Um app que roda **direto de um SSD externo** (SanDisk Extreme Portable 1 TB, exFAT), **sem internet e sem instalar nada**, e dá acesso a uma biblioteca de sobrevivência: enciclopédias (Wikipédia etc. em arquivos .zim do Kiwix), centenas de manuais em PDF/EPUB (Embrapa, saúde, sobrevivência, elétrica, solda), e uma IA local que responde perguntas **com base nessa biblioteca, citando a fonte**.

Cenário de uso: a internet e a energia caíram; o Breno liga o notebook numa bateria solar, pluga o SSD, dá dois cliques e pesquisa "como tratar água do rio" ou "como plantar mandioca". Tem que funcionar sempre, de forma simples, em português.

Princípios:
- **Uma caixa de busca para tudo.** O usuário não deve precisar saber em qual pasta ou arquivo está a informação.
- **Fonte sempre visível.** Todo resultado e toda resposta da IA mostram de onde veio, com link para abrir o original na página certa.
- **Funciona com o que existir.** Se faltar a IA, um ZIM ou os PDFs, o app segue funcionando com o resto e explica o que falta. Nunca quebra.
- **Zero dependência de rede.** Nenhum CDN, nenhuma fonte externa, nenhuma telemetria. Tudo local.
- **Portátil.** A letra do SSD muda de PC para PC (E:, F:, ...). Todos os caminhos são relativos à raiz do SSD.

## 2. Ambiente

- Windows 10/11 x64. Notebook principal: Intel i7-13620H, 16 GB RAM, sem GPU dedicada (Intel UHD). O app também deve rodar num PC com 8 GB (com o modelo de IA pequeno).
- Python: **Python embeddable 3.12.7** em `Programas\Python\` (extraído pelo baixador). Não tem pip nem tkinter. Tem `sqlite3` (verifique se o FTS5 está disponível; se não estiver, use FTS4 ou outra solução e documente).
- Bibliotecas Python: só **pure-Python, vendorizadas** em `App\vendor\` (ex.: `pypdf`). Nada que precise compilar. Prefira a biblioteca padrão.
- Kiwix: `Programas\KiwixServe\...\kiwix-serve.exe` (kiwix-tools) e `Programas\Kiwix\` (Kiwix Desktop portátil).
- IA: `Programas\IA\llamafile.exe` + modelos `.gguf` em `4_IA\` (Qwen3-8B Q4_K_M e Qwen3-1.7B). No Windows, rode o llamafile com o modelo externo (`-m arquivo.gguf`), porque o Windows não executa arquivos acima de 4 GB. Use o servidor compatível com OpenAI (`/v1/chat/completions`).
- Os nomes exatos de pastas e executáveis dentro dos zips extraídos podem variar com a versão: **descubra procurando (glob)**, não fixe no código.

## 3. Estrutura do SSD (já criada pelo baixador `_Baixador\baixar.ps1`)

```
<SSD>\
  ABRIR_BIBLIOTECA.bat          <- o app cria/entrega este lançador
  App\                          <- código do app (você cria)
  _Baixador\                    <- baixador (já existe; não alterar sem pedir)
  1_Enciclopedia\*.zim          <- Wikipédia PT/EN, Wikilivros, Wikcionário, iFixit, Appropedia, MDWiki, Stack Exchanges...
  2_Mapas\brasil.map            <- mapa Mapsforge (abre no GPXSee, fora do app)
  4_IA\*.gguf                   <- modelos de IA
  5_Acervo\<Tema>\...           <- PDFs e EPUBs por tema: Saude, Agua, Agricultura\Embrapa, Eletrica, Solda,
                                   Sobrevivencia\Cursos_EN, Desastres... O usuário pode jogar arquivos novos aqui.
  5_Acervo\Agricultura\Embrapa\_indice.txt   <- "nome_do_arquivo<TAB>Título" (use como título bonito)
  6_Instaladores\               <- instaladores e APKs (o app só lista)
  Programas\                    <- Python, KiwixServe, Kiwix, IA (llamafile)
```

Arquivos `*.ok` ao lado dos baixados são marcadores do baixador: ignore-os.

## 4. Arquitetura

- `ABRIR_BIBLIOTECA.bat` na raiz: chama `Programas\Python\python.exe App\main.py`. Ao fechar a janela, encerra tudo.
- `App\main.py` sobe:
  1. **Servidor web do app** (biblioteca padrão `http.server`/`ThreadingHTTPServer`, ou um micro-framework pure-Python vendorizado) em `127.0.0.1:8800`.
  2. **kiwix-serve** em `127.0.0.1:8801` servindo todos os `.zim` de `1_Enciclopedia` (use `--library` com um library.xml gerado por `kiwix-manage`, se existir, ou passe os arquivos na linha de comando).
  3. **llamafile** em `127.0.0.1:8802`, **só quando a IA for usada pela primeira vez** (economiza bateria e RAM), e desligado após X minutos sem uso (configurável, padrão 15).
  4. Abre o navegador padrão no app.
- Processos filhos encerrados ao sair (inclusive se a janela for fechada). Portas ocupadas: tente a próxima livre.
- **Modo rede da casa** (opcional, botão nas configurações): escutar em `0.0.0.0` para celulares conectados ao Hotspot móvel do Windows acessarem pelo navegador. Mostrar o endereço (IP:porta) na tela. Desligado por padrão.

## 5. Funcionalidades

### 5.1 Índice dos documentos (PDF, EPUB, TXT, MD, HTML)
- Varre `5_Acervo\` recursivamente. Extrai texto **por página** (PDF) ou **por capítulo** (EPUB = zip de HTML).
- Guarda num SQLite com busca de texto completo em `App\dados\indice.db` (no SSD), com tokenizador que **ignora acentos** (ex.: `unicode61 remove_diacritics 2`), para "agua" achar "água".
- **Incremental:** reindexa só o que mudou (caminho + tamanho + data). Arquivos novos jogados na pasta entram sozinhos ao abrir o app (ou por botão "Atualizar biblioteca").
- Indexação em segundo plano com barra de progresso; o app já pode ser usado enquanto indexa.
- PDF escaneado sem texto: marcar como "sem texto pesquisável" e ainda listar pelo nome/título. (OCR fica fora do escopo inicial; ver seção 9.)
- Título: metadado do PDF/EPUB, ou `_indice.txt` da Embrapa, ou nome do arquivo limpo.
- Tema = primeira subpasta de `5_Acervo` (Saude, Agua...). Idioma estimado (PT/EN) para rotular resultados.

### 5.2 Busca única
- Uma caixa de busca que consulta ao mesmo tempo: o índice de documentos **e** a busca de texto completo do kiwix-serve em todos os ZIMs.
  - Verifique a API de busca da versão instalada do kiwix-serve (`/search?pattern=...` com `books.name=` ou `books.filter.lang=`, formato `xml`/RSS quando disponível). Se a busca multi-livro não existir, busque livro a livro em paralelo, com limite de tempo.
- Resultados agrupados por fonte (Manuais / Enciclopédias / ...), com trecho destacado, tema, idioma e **link para abrir**:
  - PDF: abre no visualizador do navegador **na página certa** (`#page=N`), servido pelo app.
  - EPUB/TXT: página de leitura simples do app, rolada até o trecho.
  - ZIM: o artigo no kiwix-serve.
- Filtros: tema, idioma, tipo (manual/enciclopédia).
- **Busca bilíngue (opcional, se a IA estiver disponível):** botão "incluir fontes em inglês" que traduz os termos para inglês com a IA e busca também.

### 5.3 Pergunte à IA (RAG)
- O usuário pergunta em português. O app:
  1. Busca os trechos mais relevantes (índice de documentos + top artigos do Kiwix, com o texto do artigo baixado do kiwix-serve e limpo do HTML).
  2. Gera também a pergunta em inglês (pela IA) para recuperar trechos dos manuais em inglês.
  3. Monta o prompt com os trechos numerados [1], [2]... (respeitando o contexto do modelo).
  4. Pede ao modelo: responder em **português do Brasil**, prático e em passos, **usando só os trechos**, citando [n]; se os trechos não bastarem, dizer isso claramente em vez de inventar.
- Resposta em streaming na tela; abaixo, a lista de fontes clicáveis (abre na página certa).
- Em temas de saúde, mostrar o aviso: "Confira no manual original. Não substitui atendimento médico."
- Qwen3: desligar o modo de raciocínio longo (`/no_think`) para ganhar velocidade, com opção "pensar mais" nas configurações.
- **Escolha do modelo automática** pela RAM livre (via `ctypes`/`GlobalMemoryStatusEx`): ≥ 12 GB disponíveis no total da máquina → 8B; senão → 1.7B. Trocável nas configurações.
- Use um número de threads adequado à CPU e contexto de ~8k tokens. Mostre "carregando a IA..." na primeira vez (pode levar dezenas de segundos).

### 5.4 Traduzir
- Página simples: colar texto em inglês → tradução para português pela IA local. Botão "traduzir este trecho" nos resultados em inglês.

### 5.5 Estante (navegar)
- Lista por tema/pasta com contagem; abre qualquer documento. Lista de ZIMs com link para a página inicial de cada um no kiwix-serve.
- Atalhos na página inicial por necessidade: **Água, Fogo, Abrigo, Primeiros socorros, Plantio, Criação de animais, Conservação de alimentos, Elétrica, Solda/serralheria, Marcenaria, Navegação, Sinais/comunicação**. Cada atalho dispara buscas pré-definidas (PT + EN).

### 5.6 Painel "Situação"
- O que está disponível e o que falta (ZIMs, nº de documentos indexados, modelos de IA, kiwix-serve, llamafile), espaço livre no SSD, RAM, e instruções curtas para o que falta (ex.: "rode o _Baixador").
- Links para `6_Instaladores` e instrução para mapas (GPXSee + `2_Mapas\brasil.map`; Organic Maps no celular).

## 6. Interface

- Web local, **sem nenhum recurso externo** (fontes, ícones, JS e CSS todos dentro de `App\static`). JavaScript simples, sem build step.
- Português do Brasil em tudo. Letras grandes, contraste alto, **modo escuro** (tela à noite / economia de bateria), funciona bem em tela de celular (modo rede da casa).
- Rápida e calma: poucas telas — Início (busca + atalhos), Resultados, Pergunte, Traduzir, Estante, Situação.
- Visual sóbrio e próprio (pense em "manual de campo"), sem cara de template.

## 7. Requisitos não funcionais

- Iniciar em < 10 s até a página aparecer (IA à parte).
- Busca respondendo em < 2 s com dezenas de milhares de páginas indexadas.
- Baixo consumo: nada rodando em segundo plano quando ocioso (IA desliga sozinha).
- Logs em `App\dados\log.txt`. Erros mostrados em português claro.
- Não escrever fora do SSD (nada no `C:`), exceto o que o próprio Windows faz.
- Se o SSD for somente leitura ou estiver cheio, avisar e funcionar sem atualizar o índice.

## 8. Critérios de aceite (testar de verdade, no notebook, com o SSD)

1. Com o Wi-Fi desligado, `ABRIR_BIBLIOTECA.bat` abre o app no navegador.
2. Buscar "tratamento de água" traz resultados de PDFs (com página) **e** de ZIMs; clicar abre o original no lugar certo.
3. Buscar "agua" (sem acento) acha "água".
4. Copiar um PDF novo para `5_Acervo\Teste\` e clicar em "Atualizar biblioteca": ele aparece na busca.
5. Perguntar "como purificar água sem fervura?" → resposta em português, com citações [n] que abrem as fontes.
6. Perguntar algo que não está na biblioteca → a IA diz que não encontrou nas fontes.
7. Remover temporariamente a pasta `4_IA` → o app funciona, com a IA marcada como indisponível.
8. Ligar o "modo rede da casa", conectar um celular ao Hotspot do Windows e usar a busca pelo celular.
9. Trocar a letra do SSD (ou testar em outro PC) → tudo continua funcionando.
10. Fechar a janela do lançador → kiwix-serve e llamafile encerram.

Durante o desenvolvimento: se os downloads completos ainda não terminaram, baixe um subconjunto pequeno (ex.: Wikivoyage PT, alguns PDFs, Qwen3-1.7B) para testar. Comece pelo índice + busca de PDFs, depois Kiwix, depois IA.

## 9. Fora do escopo agora (anotar para depois)

- OCR de PDFs escaneados (Tesseract portátil) — avaliar depois.
- Visualizador de mapas dentro do app.
- Versão Android.
- Cursos do Kolibri (Khan Academy) — por enquanto só um link, se estiver instalado.

## 10. Entregáveis

- `App\` completo (código, `vendor\`, `static\`), `ABRIR_BIBLIOTECA.bat` na raiz do SSD.
- `App\LEIA-ME.txt` curto, para leigo: como abrir, como adicionar documentos, o que fazer se algo falhar.
- Ao terminar, rodar os critérios de aceite e relatar o resultado de cada um.
