# =====================================================================
#  BIBLIOTECA OFFLINE - baixador automatico
#  A pasta _Baixador fica na raiz do SSD. Rode BAIXAR_BIBLIOTECA.bat.
#  Pode interromper a qualquer momento: rodando de novo, ele continua
#  de onde parou (inclusive no meio de um arquivo grande).
# =====================================================================
$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$Raiz   = Split-Path -Parent $PSScriptRoot      # raiz do SSD
$Log    = Join-Path $PSScriptRoot 'registro.txt'
$UA     = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
$script:Falhas = @()

function Escreve($msg, $cor = 'Gray') {
    $linha = "[{0}] {1}" -f (Get-Date -Format 'dd/MM HH:mm'), $msg
    Write-Host $linha -ForegroundColor $cor
    Add-Content -Path $Log -Value $linha -Encoding UTF8
}
function Falha($msg) { $script:Falhas += $msg; Escreve $msg 'Red' }

function Pasta($rel) {
    $p = Join-Path $Raiz $rel
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    return $p
}

# Confere se o arquivo baixado e do tipo esperado (evita salvar pagina de erro)
function TipoCerto($arquivo) {
    $ext = [IO.Path]::GetExtension($arquivo).ToLower()
    $fs = [IO.File]::OpenRead($arquivo)
    try { $b = New-Object byte[] 4; [void]$fs.Read($b, 0, 4) } finally { $fs.Close() }
    $ini = [Text.Encoding]::ASCII.GetString($b)
    switch ($ext) {
        '.pdf' { return $ini.StartsWith('%PDF') }
        '.zip' { return $ini.StartsWith('PK') }
        '.apk' { return $ini.StartsWith('PK') }
        '.exe' { return $ini.StartsWith('MZ') }
        '.zim' { return ($b[0] -eq 0x5A -and $b[1] -eq 0x49 -and $b[2] -eq 0x4D -and $b[3] -eq 0x04) }
        '.gguf' { return $ini -eq 'GGUF' }
        default { return $true }
    }
}

function Baixa($url, $destino, $nome, $ua = $UA) {
    $ok = "$destino.ok"
    if ((Test-Path $destino) -and (Test-Path $ok)) { Escreve "Ja baixado: $nome" 'DarkGray'; return $true }
    # arquivo de 0 byte de uma tentativa anterior: recomeca do zero
    if ((Test-Path $destino) -and ((Get-Item $destino).Length -eq 0)) { Remove-Item $destino -Force }
    Escreve "Baixando: $nome" 'Cyan'
    for ($t = 1; $t -le 5; $t++) {
        & curl.exe -L -f -C - --retry 5 --retry-delay 10 --connect-timeout 30 -A $ua -o "$destino" "$url"
        $cod = $LASTEXITCODE
        if (($cod -eq 0 -or $cod -eq 33) -and (Test-Path $destino) -and ((Get-Item $destino).Length -gt 0)) {
            if (TipoCerto $destino) {
                Set-Content -Path $ok -Value $url
                Escreve "OK: $nome" 'Green'; return $true
            }
            Remove-Item $destino -Force
            Falha "O link de '$nome' nao entregou o arquivo certo (pode ter mudado de endereco). Baixe a mao: $url"
            return $false
        }
        Escreve "Falhou (codigo $cod), tentativa $t de 5. Esperando 30 s..." 'Yellow'
        Start-Sleep -Seconds 30
    }
    Falha "NAO CONSEGUI BAIXAR: $nome  ($url)"
    return $false
}

function Pagina($url) {
    try { return (Invoke-WebRequest -Uri $url -UseBasicParsing -UserAgent $UA -TimeoutSec 60).Content }
    catch { Escreve "Nao abriu a pagina $url" 'Yellow'; return '' }
}

# Chave de ordenacao que entende numeros (3.10 vem depois de 3.9)
function Chave($s) { return [regex]::Replace($s, '\d+', { param($m) $m.Value.PadLeft(10, '0') }) }

# Acha o arquivo mais recente numa listagem do download.kiwix.org
function MaisRecente($pastaUrl, $regex) {
    $html = Pagina $pastaUrl
    $nomes = @([regex]::Matches($html, 'href="(' + $regex + ')"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
    if ($nomes.Count -eq 0) { return $null }
    return ($nomes | Sort-Object { Chave $_ })[-1]
}

function Zim($projeto, $prefixo, $rotulo) {
    $base = "https://download.kiwix.org/zim/$projeto/"
    $arq = MaisRecente $base ([regex]::Escape($prefixo) + '_\d{4}-\d{2}\.zim')
    if (-not $arq) { Falha "Nao achei '$prefixo'. Procure '$rotulo' no catalogo do app Kiwix."; return }
    $dest = Join-Path (Pasta '1_Enciclopedia') $arq
    Baixa ($base + $arq) $dest "$rotulo ($arq)" | Out-Null
}

function Gguf($repo, $preferidos, $pastaDestino) {
    try {
        $info = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$repo" -UserAgent $UA -TimeoutSec 60
        $todos = $info.siblings | ForEach-Object { $_.rfilename } | Where-Object { $_ -like '*.gguf' }
        foreach ($p in $preferidos) {
            $f = $todos | Where-Object { $_ -match $p } | Select-Object -First 1
            if ($f) {
                $dest = Join-Path $pastaDestino (Split-Path $f -Leaf)
                Baixa "https://huggingface.co/$repo/resolve/main/$f" $dest "Modelo de IA $f" | Out-Null
                return
            }
        }
        Falha "Nenhum arquivo GGUF adequado em $repo"
    } catch { Falha "Nao consegui consultar o modelo $repo" }
}

function GithubAsset($repo, $regex, $destPasta, $novoNome) {
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/$repo/releases/latest" -UserAgent $UA -TimeoutSec 60
        $a = $rel.assets | Where-Object { $_.name -match $regex } | Select-Object -First 1
        if (-not $a) { Falha "Nao achei o arquivo '$regex' em $repo"; return }
        $nome = if ($novoNome) { $novoNome } else { $a.name }
        Baixa $a.browser_download_url (Join-Path $destPasta $nome) "$repo ($($a.name))" | Out-Null
    } catch { Falha "Nao consegui consultar $repo no GitHub" }
}

# Busca no catalogo oficial do Kiwix por palavra-chave e baixa o primeiro titulo que bate
function ZimCatalogo($busca, $lang, $tituloRegex, $rotulo) {
    $url = "https://library.kiwix.org/catalog/v2/entries?count=30&lang=$lang&q=" + [uri]::EscapeDataString($busca)
    $txt = Pagina $url
    if (-not $txt) { Falha "Catalogo do Kiwix fora do ar para '$rotulo'"; return }
    try { [xml]$x = $txt } catch { Falha "Resposta estranha do catalogo para '$rotulo'"; return }
    $e = @($x.feed.entry) | Where-Object { $_.title -match $tituloRegex } | Select-Object -First 1
    if (-not $e) { Escreve "Nao existe '$rotulo' no catalogo do Kiwix (ok, segue)." 'DarkGray'; return }
    $lnk = @($e.link) | Where-Object { $_.type -eq 'application/x-zim' } | Select-Object -First 1
    if (-not $lnk) { Falha "Sem link de download para '$rotulo'"; return }
    $href = ($lnk.href -replace '\.meta4$', '')
    $arq = Split-Path $href -Leaf
    Baixa $href (Join-Path (Pasta '1_Enciclopedia') $arq) "$rotulo ($arq)" | Out-Null
}

# Embrapa: percorre TODAS as colecoes Criar / Plantar / ABC / 500 Perguntas
function Embrapa {
    $base = 'https://www.infoteca.cnptia.embrapa.br/infoteca'
    $dest = Pasta '5_Acervo\Agricultura\Embrapa'
    $feitos = Join-Path $PSScriptRoot 'embrapa_feitos.txt'
    $jaFeitos = @{}
    if (Test-Path $feitos) { Get-Content $feitos | ForEach-Object { $jaFeitos[$_] = 1 } }
    $indice = Join-Path $dest '_indice.txt'

    $cols = @()
    $lista = Pagina "$base/community-list"
    foreach ($m in [regex]::Matches($lista, 'handle/item/(\d+)"[^>]*>(?:\s|<[^>]+>)*Cole[^<]*Criar, Plantar')) { $cols += $m.Groups[1].Value }
    if ($cols.Count -lt 5) {   # reserva, caso a pagina mude
        $cols += @(481,47,285,69,593,89,75,607,17398,103,61670,117,131,145,159,621,173,187,201,17368,215,229,243,257,299,313,17353,327,341,369,397,411,439,495,579)
    }
    $cols = $cols | Sort-Object -Unique
    Escreve "Embrapa: $($cols.Count) colecoes para percorrer" 'White'

    $docs = @()
    foreach ($c in $cols) {
        for ($o = 0; $o -lt 3000; $o += 20) {
            $h = Pagina "$base/handle/item/$($c)?offset=$o"
            $novos = @([regex]::Matches($h, 'handle/doc/(\d+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
            $novos = @($novos | Where-Object { $docs -notcontains $_ })
            if ($novos.Count -eq 0) { break }
            $docs += $novos
            Start-Sleep -Milliseconds 300
        }
    }
    Escreve "Embrapa: $($docs.Count) publicacoes encontradas" 'White'

    foreach ($d in $docs) {
        if ($jaFeitos.ContainsKey($d)) { continue }
        $h = Pagina "$base/handle/doc/$d"
        if (-not $h) { continue }
        $tit = ''
        $mt = [regex]::Match($h, '<meta name="citation_title" content="([^"]+)"')
        if ($mt.Success) { $tit = [Net.WebUtility]::HtmlDecode($mt.Groups[1].Value) }
        $arqs = [regex]::Matches($h, 'href="((?:https?://[^"/]+)?/(?:infoteca/)?bitstream/doc/' + $d + '/\d+/[^"?]+\.(?:pdf|epub))"') |
                ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
        $tudoOk = $true
        foreach ($a in $arqs) {
            $u = if ($a -like 'http*') { $a -replace '^http:', 'https:' } else { 'https://www.infoteca.cnptia.embrapa.br' + $a }
            $nome = $d + '_' + [uri]::UnescapeDataString((Split-Path $u -Leaf))
            $nome = ($nome -replace '[\\/:*?"<>|]', '_')
            if (Baixa $u (Join-Path $dest $nome) "Embrapa: $tit") {
                Add-Content -Path $indice -Value "$nome`t$tit" -Encoding UTF8
            } else { $tudoOk = $false }
        }
        if ($tudoOk) { Add-Content -Path $feitos -Value $d }
        Start-Sleep -Milliseconds 300
    }
}

# Baixa todos os PDFs listados numa pagina (usado para a Hesperian)
function PdfsDaPagina($pagUrl, $pastaRel, $rotulo) {
    $h = Pagina $pagUrl
    $links = [regex]::Matches($h, 'href="([^"]+\.pdf)"') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
    if (-not $links) { Falha "Nenhum PDF encontrado em $rotulo ($pagUrl)"; return }
    $dest = Pasta $pastaRel
    foreach ($l in $links) {
        $u = ([uri]::new([uri]$pagUrl, $l)).AbsoluteUri
        $nome = [uri]::UnescapeDataString((Split-Path $u -Leaf)) -replace '[\\/:*?"<>| ]', '_'
        # a pagina da Hesperian tem um link com espaco a mais ("pt_ cgeh_2013_toc.pdf") que da erro 500
        $u = $u -replace '/pt_%20', '/pt_'
        Baixa $u (Join-Path $dest $nome) "$rotulo - $nome" | Out-Null
    }
}

# ZIM pelo nome exato no catalogo oficial do Kiwix (pega sempre a versao mais nova)
function ZimPorNome($nomeLivro, $rotulo) {
    $txt = Pagina ("https://library.kiwix.org/catalog/v2/entries?name=" + $nomeLivro)
    $m = [regex]::Match($txt, 'href="([^"]+\.zim)\.meta4"')
    if (-not $m.Success) { Falha "Nao achei '$nomeLivro' ($rotulo) no catalogo do Kiwix."; return }
    $href = $m.Groups[1].Value
    $existentes = @(Get-ChildItem (Pasta '1_Enciclopedia') -Filter ($nomeLivro + '_*.zim') -ErrorAction SilentlyContinue |
                    Where-Object { Test-Path ($_.FullName + '.ok') })
    if ($existentes.Count -gt 0) { Escreve "Ja baixado: $rotulo ($($existentes[0].Name))" 'DarkGray'; return }
    Baixa $href (Join-Path (Pasta '1_Enciclopedia') (Split-Path $href -Leaf)) "$rotulo ($(Split-Path $href -Leaf))" | Out-Null
}

function Expande($zip, $destino) {
    $marca = Join-Path $destino '.extraido'
    if (Test-Path $marca) { return }
    try {
        Expand-Archive -Path $zip -DestinationPath $destino -Force
        Set-Content $marca (Split-Path $zip -Leaf)
        Escreve "Extraido: $(Split-Path $zip -Leaf)" 'Green'
    } catch { Falha "Falha ao extrair $zip" }
}

# ---------------------------------------------------------------------
Clear-Host
Write-Host ''
Write-Host '  BIBLIOTECA OFFLINE DE SOBREVIVENCIA - baixador' -ForegroundColor White
Write-Host "  Destino: $Raiz" -ForegroundColor Gray
try {
    $livre = (Get-PSDrive -Name ($Raiz.Substring(0, 1))).Free / 1GB
    Write-Host ("  Espaco livre: {0:N0} GB" -f $livre) -ForegroundColor Gray
} catch {}
Write-Host ''
Write-Host '  O essencial (~70-100 GB) sempre e baixado. Itens grandes opcionais, em ingles:' -ForegroundColor White
$querEN  = (Read-Host '  Wikipedia em INGLES completa, com imagens (~120 GB)? [s/n]') -match '^[sS]'
# O WikiHow saiu do catalogo do Kiwix; no lugar, bibliotecas praticas (CD3WD, pos-desastre, horta...)
$querHow = (Read-Host '  Bibliotecas praticas em ingles, guias "como fazer" (~3,5 GB)? [s/n]') -match '^[sS]'
$querSurvivor = (Read-Host '  SURVIVOR LIBRARY: milhares de livros antigos de oficios (~205 GB)? [s/n]') -match '^[sS]'
Write-Host ''
Write-Host '  Pode deixar rodando. Se precisar parar, feche a janela; depois rode de novo.' -ForegroundColor Gray
Write-Host ''
Escreve '===== Inicio =====' 'White'

# 1. Programas -----------------------------------------------------------
$prog = Pasta 'Programas'
$inst = Pasta '6_Instaladores'

$kz = MaisRecente 'https://download.kiwix.org/release/kiwix-desktop/' 'kiwix-desktop_windows_x64_[\d.\-]+\.zip'
if ($kz) {
    $d = Join-Path $inst $kz
    if (Baixa "https://download.kiwix.org/release/kiwix-desktop/$kz" $d "Kiwix Desktop") { Expande $d (Join-Path $prog 'Kiwix') }
} else { Falha 'Nao achei o Kiwix Desktop para Windows' }

$kt = MaisRecente 'https://download.kiwix.org/release/kiwix-tools/' 'kiwix-tools_win-x86_64[^"/]*\.zip'
if (-not $kt) { $kt = MaisRecente 'https://download.kiwix.org/release/kiwix-tools/' 'kiwix-tools_win[^"/]*\.zip' }
if ($kt) {
    $d = Join-Path $inst $kt
    if (Baixa "https://download.kiwix.org/release/kiwix-tools/$kt" $d "Kiwix servidor (usado pelo app)") { Expande $d (Join-Path $prog 'KiwixServe') }
} else { Falha 'Nao achei o kiwix-tools (servidor) para Windows' }

$ka = MaisRecente 'https://download.kiwix.org/release/kiwix-android/' 'kiwix-[\d.]+\.apk'
if ($ka) { Baixa "https://download.kiwix.org/release/kiwix-android/$ka" (Join-Path $inst $ka) 'Kiwix para Android' | Out-Null }

$py = Join-Path $inst 'python-3.12.7-embed-amd64.zip'
if (Baixa 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' $py 'Python portatil (motor do app)') { Expande $py (Join-Path $prog 'Python') }

GithubAsset 'Mozilla-Ocho/llamafile' '^llamafile-[\d.]+$' (Pasta 'Programas\IA') 'llamafile.exe'
GithubAsset 'organicmaps/organicmaps' '\.apk$' $inst $null
Baixa 'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=pt-BR' (Join-Path $inst 'Firefox_Instalador.exe') 'Firefox (instalador)' | Out-Null
# Ollama removido: o app usa o llamafile (Programas\IA)
# GPXSee: o SourceForge so entrega o .exe para "nao navegadores" (por isso o UA do curl)
$rss = ''
try { $rss = (Invoke-WebRequest -Uri 'https://sourceforge.net/projects/gpxsee/rss?path=/Windows' -UseBasicParsing -UserAgent 'curl/8.4.0' -TimeoutSec 60).Content } catch {}
$gm = [regex]::Match($rss, 'files/Windows/(GPXSee-[\d.]+_x64\.exe)/download')
if ($gm.Success) {
    Baixa ('https://sourceforge.net/projects/gpxsee/files/Windows/' + $gm.Groups[1].Value + '/download') (Join-Path $inst 'GPXSee_Instalador.exe') "GPXSee ($($gm.Groups[1].Value))" 'curl/8.4.0' | Out-Null
} else { Falha 'Nao achei o GPXSee para Windows no SourceForge.' }

# 2. Modelos de IA -------------------------------------------------------
$ia = Pasta '4_IA'
Gguf 'Qwen/Qwen3-1.7B-GGUF' @('Q4_K_M', 'Q8_0') $ia
Gguf 'Qwen/Qwen3-8B-GGUF'   @('Q4_K_M', 'Q5_K_M', 'Q8_0') $ia

# 3. Mapas ---------------------------------------------------------------
$map = Pasta '2_Mapas'
Baixa 'https://download.mapsforge.org/maps/v5/south-america/brazil.map' (Join-Path $map 'brasil.map') 'Mapa do Brasil (Mapsforge)' | Out-Null

# 4. PDFs - acervo brasileiro --------------------------------------------
$pdfs = @(
    @('Saude',         'Primeiros_Socorros_Fiocruz.pdf',                  'https://unijales.edu.br/library/downebook/id:1475'),
    @('Saude',         'Animais_Peconhentos_MS.pdf',                      'https://portaldeboaspraticas.iff.fiocruz.br/wp-content/uploads/2025/03/Manual-de-diagnostico-e-tratamento-de-acidentes-por-animais-peconhentos.pdf'),
    @('Agua',          'SODIS_Desinfeccao_Solar_da_Agua.pdf',             'https://bdd.pseau.org/outils/ouvrages/eawag_sodis_desinfeccao_solar_da_agua_2005.pdf'),
    @('Agua',          'Embrapa_A_chuva_no_Sertao.pdf',                   'https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/155876/1/A-chuva-no-sertao.pdf'),
    @('Agricultura',   'Embrapa_Sistemas_Agroflorestais.pdf',             'https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/1052078/1/ABC-Sistemas-agroflorestais-ed02-2016.pdf'),
    @('Agricultura',   'Embrapa_Alimentacao_criacoes_na_seca.pdf',        'https://www.infoteca.cnptia.embrapa.br/infoteca/bitstream/doc/155864/1/ID31771.pdf'),
    @('Eletrica',      'Instalacoes_Eletricas_Residenciais_Procobre.pdf', 'https://wiki.sj.ifsc.edu.br/images/a/a4/Inst.Eletr.Res.2003.pdf'),
    @('Eletrica',      'Manual_Instalacao_Prysmian.pdf',                  'https://content.voltimum.com/pt/18374/manual_instalacao_uld.pdf'),
    # apostila original da ESAB (o site oficial saiu do ar; copia de 2016 no Internet Archive)
    @('Solda',         'ESAB_Eletrodos_Revestidos.pdf',                   'https://web.archive.org/web/2016id_/http://www.esab.com.br/br/pt/education/apostilas/upload/1901097rev1_apostilaeletrodosrevestidos_ok.pdf'),
    @('Solda',         'SENAR_Soldador_Eletrodos_Revestidos.pdf',         'https://sistemafaep.org.br/wp-content/uploads/2021/05/PR.0302-Soldador_web.pdf'),
    @('Sobrevivencia', 'Exercito_Sobrevivencia_em_Campanha.pdf',          'https://bdex.eb.mil.br/jspui/bitstream/123456789/12384/1/Sobreviv%c3%aancia%20em%20Campanha.pdf'),
    @('Desastres',     'Defesa_Civil_Gestao_de_Risco.pdf',                'https://defesacivil.rs.gov.br/upload/arquivos/201710/05172051-01-gestao-de-risco.pdf')
)
foreach ($p in $pdfs) {
    $d = Join-Path (Pasta ('5_Acervo\' + $p[0])) $p[1]
    Baixa $p[2] $d $p[1] | Out-Null
}

# Cursos e manuais completos de sobrevivencia (ingles, dominio publico - governo dos EUA/Canada)
$tp = 'https://trueprepper.com/wp-content/uploads/'
# ATENCAO: $tp + '...' precisa de parenteses dentro de @( , ): sem eles o PowerShell junta a
# virgula antes do '+' e o link vira so a pasta (foi o que fez 19 cursos falharem).
# irp.fas.org bloqueia robos: AFH 10-644 vem do Internet Archive, ATP 3-50.21 do trueprepper.
$sobr = @(
    @('01_AFH_10-644_SERE_Forca_Aerea_EUA_(o_mais_completo).pdf', 'https://web.archive.org/web/2024id_/https://irp.fas.org/doddir/usaf/afh10-644.pdf'),
    @('02_ATP_3-50.21_Survival_Exercito_EUA_2018.pdf',           ($tp + 'ATP-3-50.21-Survival.pdf')),
    @('03_FM_3-05.70_Survival_2002.pdf',                         ($tp + 'FM-3-05-70-Survival-2002.pdf')),
    @('04_FM_21-76_Survival.pdf',                                ($tp + '2022/11/FM-21-76-US-Army-Survival-Manual.pdf')),
    @('05_FM_21-76-1_Survival_Evasion_Recovery_bolso.pdf',       ($tp + '2023/03/FM-21-76-1-Survival-Evasion-and-Recovery-Multiservice-Procedures.pdf')),
    @('06_USMC_Curso_Sobrevivencia_Verao.pdf',                   ($tp + 'USMC-Summer-Survival-Course-Handbook.pdf')),
    @('07_USMC_Curso_Sobrevivencia_Inverno.pdf',                 ($tp + 'USMC-Winter-Survival-Course-Handbook.pdf')),
    @('08_Navy_Marines_SERE_Apostila_do_Aluno.pdf',              ($tp + 'Navy-Marine-Corps-SERE-Student-Handbook.pdf')),
    @('09_FM_90-5_Operacoes_na_Selva.pdf',                       ($tp + 'FM-90-5-Jungle-Operations.pdf')),
    @('10_FM_3-25.26_Leitura_de_Mapas_e_Navegacao.pdf',          ($tp + 'FM-3-25-26-Map-Reading-and-Land-Navigation.pdf')),
    @('11_FM_21-60_Sinais_Visuais.pdf',                          ($tp + 'FM-21-60-Visual-Signals.pdf')),
    @('12_TC_3-97.61_Montanhismo.pdf',                           ($tp + 'TC-3-97-61-Military-Mountaineering.pdf')),
    @('13_FM_21-10_Higiene_e_Saneamento_de_Campo.pdf',           ($tp + 'FM-21-10-Field-Hygiene-and-Sanitation.pdf')),
    @('14_FM_4-25.11_Primeiros_Socorros.pdf',                    ($tp + 'FM-4-25-11-First_Aid.pdf')),
    @('15_ST_31-91B_Manual_Medico_Forcas_Especiais.pdf',         ($tp + '2022/12/ST-31-91B-US-Army-Special-Forces-Medical-Handbook.pdf')),
    @('16_FEMA_CERT_Curso_Basico_Resposta_a_Emergencias.pdf',    'https://em.uci.edu/training/_pdf/cert-participant-manual-english.pdf'),
    @('17_FEMA_Are_You_Ready_Guia_do_Cidadao.pdf',               ($tp + 'FEMA-Citizen-Preparedness-Guide.pdf')),
    @('18_Nuclear_War_Survival_Skills_Kearny.pdf',               ($tp + '2022/09/Nuclear-War-Survival-Skills-Cresson-Kearny.pdf')),
    @('19_Canada_Down_But_Not_Out_Sobrevivencia.pdf',            ($tp + '2023/03/Down-But-Not-Out-Canadian-Survival-Manual.pdf')),
    @('20_Canada_Military_Fieldcraft.pdf',                       ($tp + 'Canadian-Military-Fieldcraft.pdf')),
    @('21_Seton_Book_of_Woodcraft_1913.pdf',                     ($tp + 'The-Book-of-Woodcraft-1913.pdf'))
)
foreach ($s in $sobr) {
    Baixa $s[1] (Join-Path (Pasta '5_Acervo\Sobrevivencia\Cursos_EN') $s[0]) $s[0] | Out-Null
}

# Hesperian: Onde nao ha medico + Guia comunitario de saude ambiental (PT)
PdfsDaPagina 'https://languages.hesperian.org/pages/pt/pdf.html' '5_Acervo\Saude\Hesperian' 'Hesperian'

# Embrapa: todas as colecoes (centenas de manuais de plantio, criacao, agua, conservas)
Embrapa

# 5. Enciclopedias (ZIM) - por ultimo, porque sao os maiores -------------
# Selecao "preppers" do Kiwix (ingles; a IA e o Firefox traduzem)
ZimCatalogo 'water treatment'      'eng' 'Water'                 'Tratamento de agua'
ZimCatalogo 'food preppers'        'eng' 'Food'                  'Alimentacao para emergencias'
ZimCatalogo 'knots'                'eng' 'Knot'                  'Nos e amarras'
ZimCatalogo 'survival'             'eng' 'Surviv'                'Videos de sobrevivencia (Kiwix)'
ZimCatalogo 'grow your own'        'eng' 'Grow'                  'Videos: cultive seu alimento'
ZimCatalogo 'low-tech magazine'    'eng' 'Low.?tech'             'Low-tech Magazine'
ZimCatalogo 'ready.gov'            'eng' 'Ready'                 'Ready.gov (preparacao para desastres)'
ZimCatalogo 'military medicine'    'eng' 'Military Medicine'     'Medicina militar'
# MDWiki: o baixar_conhecimento.py pega a versao sem videos (2 GB em vez de 10 GB)
ZimCatalogo 'wikem'                'eng' 'WikEM|wikem'           'WikEM (emergencia medica)'
ZimCatalogo 'gardening'            'eng' 'Landscaping'           'Jardinagem e horta (Stack Exchange)'
ZimCatalogo 'home improvement'     'eng' 'Home Improvement'      'Reformas e manutencao da casa'
ZimCatalogo 'motor vehicle'        'eng' 'Motor Vehicle'         'Manutencao de veiculos'
ZimCatalogo 'amateur radio'        'eng' 'Amateur Radio'         'Radioamador'
ZimCatalogo 'homebrew'             'eng' 'Homebrew'              'Fermentacao e bebidas caseiras'
ZimCatalogo 'woodworking'          'eng' 'Woodworking'           'Marcenaria (Stack Exchange)'
ZimCatalogo 'electronics'          'eng' '^Electrical Engineering|Electronics' 'Eletrica e eletronica'
ZimCatalogo 'wikihow'              'por' 'wikiHow|WikiHow'       'WikiHow em portugues'

ZimPorNome 'wiktionary_pt_all' 'Wikcionario PT'   # so existe a versao sem imagens (nopic)
Zim 'wikibooks'   'wikibooks_pt_all_maxi'      'Wikilivros PT'
Zim 'wikivoyage'  'wikivoyage_pt_all_maxi'     'Wikivoyage PT'
Zim 'wikisource'  'wikisource_pt_all_maxi'     'Wikisource PT'
Zim 'wikiversity' 'wikiversity_pt_all_maxi'    'Wikiversidade PT'
Zim 'other'       'appropedia_en_all_maxi'     'Appropedia (tecnologia apropriada)'
Zim 'ifixit'      'ifixit_en_all'              'iFixit (consertos)'
# WikiMed removida: ja esta inteira dentro da Wikipedia EN
Zim 'wikipedia'   'wikipedia_pt_all_maxi'      'Wikipedia PT completa'
if ($querHow) {
    ZimPorNome 'usda-2015_en'                       'Guia USDA de conservas caseiras'
    ZimPorNome 'zimgit-medicine_en'                 'Biblioteca medica (preppers)'
    ZimPorNome 'zimgit-knots_en'                    'Biblioteca de nos'
    ZimPorNome 'wikiciv_en_all'                     'WikiCiv (reconstruir tecnologias basicas)'
    ZimPorNome 'nhs.uk_en_medicines'                'NHS - remedios de A a Z'
    ZimPorNome 'cooking.stackexchange.com_en_all'   'Cozinha (Stack Exchange)'
    ZimPorNome 'cd3wdproject.org_en_all'            'CD3WD (tecnologia apropriada)'
    ZimPorNome 'zimgit-post-disaster_en'            'Biblioteca pos-desastre'
    ZimPorNome 'energypedia_en_all'                 'Energypedia (energia solar, biogas...)'
    ZimPorNome 'gardening.stackexchange.com_en_all' 'Horta e jardinagem (Stack Exchange)'
}
if ($querSurvivor) { ZimPorNome 'survivorlibrary.com_en_all' 'Survivor Library (~205 GB)' }
if ($querEN)  { Zim 'wikipedia' 'wikipedia_en_all_maxi' 'Wikipedia EN completa' }

# ---------------------------------------------------------------------
Escreve '===== Fim =====' 'White'
Write-Host ''
if ($script:Falhas.Count -gt 0) {
    Write-Host "  $($script:Falhas.Count) item(ns) nao baixaram:" -ForegroundColor Yellow
    $script:Falhas | ForEach-Object { Write-Host "   - $_" -ForegroundColor Yellow }
    Write-Host '  Rode de novo mais tarde; o que ja baixou fica salvo.' -ForegroundColor Yellow
    Write-Host "  Registro completo: $Log" -ForegroundColor Yellow
} else {
    Write-Host '  Tudo baixado.' -ForegroundColor Green
}
Write-Host ''
Read-Host '  Pressione ENTER para fechar'
