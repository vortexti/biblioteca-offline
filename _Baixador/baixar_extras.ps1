# =====================================================================
#  BIBLIOTECA OFFLINE - baixador complementar (itens que falharam no baixar.ps1)
#
#  - Corrige os 19 cursos do trueprepper (o baixar.ps1 tem um bug: em
#    @('nome', $tp + 'arquivo') o PowerShell junta a VIRGULA antes do '+',
#    e o link virava so a pasta .../uploads/).
#  - Usa enderecos novos/arquivados para os links que morreram ou bloqueiam robos.
#  - Baixa substitutos do WikiHow (que saiu do catalogo do Kiwix).
#  Usa os MESMOS nomes de arquivo e marcas .ok do baixar.ps1: rodando o
#  baixar.ps1 de novo, esses itens aparecem como "Ja baixado".
#
#  Uso:  BAIXAR_EXTRAS.bat                 (pergunta o que baixar)
#        powershell -File baixar_extras.ps1 -Verificar   (so testa os links)
#        powershell -File baixar_extras.ps1 -SemPerguntas (baixa o recomendado)
# =====================================================================
param([switch]$Verificar, [switch]$SemPerguntas, [switch]$SurvivorLibrary)

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$Raiz = Split-Path -Parent $PSScriptRoot
$Log  = Join-Path $PSScriptRoot 'registro.txt'
$UA   = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) BibliotecaOffline/1.0'
$UA_CURL = 'curl/8.4.0'   # SourceForge so entrega o arquivo para "nao navegadores"
$script:Falhas = @()

function Escreve($msg, $cor = 'Gray') {
    $linha = "[{0}] {1}" -f (Get-Date -Format 'dd/MM HH:mm'), $msg
    Write-Host $linha -ForegroundColor $cor
    if (-not $Verificar) { Add-Content -Path $Log -Value $linha -Encoding UTF8 }
}
function Falha($msg) { $script:Falhas += $msg; Escreve $msg 'Red' }
function Pasta($rel) {
    $p = Join-Path $Raiz $rel
    if (-not (Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }
    return $p
}

function TipoCerto($arquivo) {
    $ext = [IO.Path]::GetExtension($arquivo).ToLower()
    $fs = [IO.File]::OpenRead($arquivo)
    try { $b = New-Object byte[] 4; [void]$fs.Read($b, 0, 4) } finally { $fs.Close() }
    $ini = [Text.Encoding]::ASCII.GetString($b)
    switch ($ext) {
        '.pdf' { return $ini.StartsWith('%PDF') }
        '.exe' { return $ini.StartsWith('MZ') }
        '.zim' { return ($b[0] -eq 0x5A -and $b[1] -eq 0x49 -and $b[2] -eq 0x4D -and $b[3] -eq 0x04) }
        default { return $true }
    }
}

# Modo -Verificar: baixa so os primeiros bytes e confere o tipo
function Testa($url, $nome, $ua) {
    $tmp = Join-Path $env:TEMP ('bo_teste' + [IO.Path]::GetExtension($nome))
    & curl.exe -s -L -f -r 0-15 --connect-timeout 30 -m 90 -A $ua -o "$tmp" "$url"
    $ok = ($LASTEXITCODE -eq 0) -and (Test-Path $tmp) -and (TipoCerto $tmp)
    Remove-Item $tmp -ErrorAction SilentlyContinue
    if ($ok) { Escreve "  link OK: $nome" 'Green' } else { Falha "  link FALHOU: $nome ($url)" }
    return $ok
}

function Baixa($url, $destino, $nome, $ua = $UA) {
    if ($Verificar) { return (Testa $url $nome $ua) }
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
            Falha "O link de '$nome' nao entregou o arquivo certo. Baixe a mao: $url"
            return $false
        }
        Escreve "Falhou (codigo $cod), tentativa $t de 5. Esperando 30 s..." 'Yellow'
        Start-Sleep -Seconds 30
    }
    Falha "NAO CONSEGUI BAIXAR: $nome  ($url)"
    return $false
}

function Pagina($url, $ua = $UA) {
    try { return (Invoke-WebRequest -Uri $url -UseBasicParsing -UserAgent $ua -TimeoutSec 60).Content }
    catch { Escreve "Nao abriu a pagina $url" 'Yellow'; return '' }
}

# ZIM pelo nome exato no catalogo oficial do Kiwix (pega sempre a versao mais nova)
function ZimPorNome($nomeLivro, $rotulo) {
    $txt = Pagina ("https://library.kiwix.org/catalog/v2/entries?name=" + $nomeLivro)
    $m = [regex]::Match($txt, 'href="([^"]+\.zim)\.meta4"')
    if (-not $m.Success) { Falha "Nao achei '$nomeLivro' ($rotulo) no catalogo do Kiwix."; return }
    $href = $m.Groups[1].Value
    $arq = Split-Path $href -Leaf
    # ja tem uma versao (qualquer data) desse livro completa? entao pula
    $existentes = @(Get-ChildItem (Pasta '1_Enciclopedia') -Filter ($nomeLivro + '_*.zim') -ErrorAction SilentlyContinue |
                    Where-Object { Test-Path ($_.FullName + '.ok') })
    if ($existentes.Count -gt 0 -and -not $Verificar) { Escreve "Ja baixado: $rotulo ($($existentes[0].Name))" 'DarkGray'; return }
    Baixa $href (Join-Path (Pasta '1_Enciclopedia') $arq) "$rotulo ($arq)" | Out-Null
}

# ---------------------------------------------------------------------
Clear-Host
Write-Host ''
Write-Host '  BIBLIOTECA OFFLINE - baixador complementar' -ForegroundColor White
Write-Host "  Destino: $Raiz" -ForegroundColor Gray
if ($Verificar) { Write-Host '  MODO VERIFICAR: so testa os links, nao baixa nada.' -ForegroundColor Yellow }
Write-Host ''

$querSubst = $true
$querSurvivor = [bool]$SurvivorLibrary
if (-not $SemPerguntas -and -not $Verificar) {
    $querSubst = -not ((Read-Host '  Baixar as bibliotecas praticas que substituem o WikiHow (~3,5 GB)? [S/n]') -match '^[nN]')
    $querSurvivor = (Read-Host '  Baixar a SURVIVOR LIBRARY (milhares de livros antigos de oficios, ~205 GB)? [s/N]') -match '^[sS]'
}
Escreve '===== Inicio (extras) =====' 'White'

# 1. Cursos de sobrevivencia (ingles) ------------------------------------
$tp = 'https://trueprepper.com/wp-content/uploads/'
$wb = 'https://web.archive.org/web/2024id_/'   # copia arquivada (Internet Archive)
$cursos = @(
    ,@('01_AFH_10-644_SERE_Forca_Aerea_EUA_(o_mais_completo).pdf', ($wb + 'https://irp.fas.org/doddir/usaf/afh10-644.pdf'))
    ,@('02_ATP_3-50.21_Survival_Exercito_EUA_2018.pdf',           ($tp + 'ATP-3-50.21-Survival.pdf'))
    ,@('03_FM_3-05.70_Survival_2002.pdf',                         ($tp + 'FM-3-05-70-Survival-2002.pdf'))
    ,@('04_FM_21-76_Survival.pdf',                                ($tp + '2022/11/FM-21-76-US-Army-Survival-Manual.pdf'))
    ,@('05_FM_21-76-1_Survival_Evasion_Recovery_bolso.pdf',       ($tp + '2023/03/FM-21-76-1-Survival-Evasion-and-Recovery-Multiservice-Procedures.pdf'))
    ,@('06_USMC_Curso_Sobrevivencia_Verao.pdf',                   ($tp + 'USMC-Summer-Survival-Course-Handbook.pdf'))
    ,@('07_USMC_Curso_Sobrevivencia_Inverno.pdf',                 ($tp + 'USMC-Winter-Survival-Course-Handbook.pdf'))
    ,@('08_Navy_Marines_SERE_Apostila_do_Aluno.pdf',              ($tp + 'Navy-Marine-Corps-SERE-Student-Handbook.pdf'))
    ,@('09_FM_90-5_Operacoes_na_Selva.pdf',                       ($tp + 'FM-90-5-Jungle-Operations.pdf'))
    ,@('10_FM_3-25.26_Leitura_de_Mapas_e_Navegacao.pdf',          ($tp + 'FM-3-25-26-Map-Reading-and-Land-Navigation.pdf'))
    ,@('11_FM_21-60_Sinais_Visuais.pdf',                          ($tp + 'FM-21-60-Visual-Signals.pdf'))
    ,@('12_TC_3-97.61_Montanhismo.pdf',                           ($tp + 'TC-3-97-61-Military-Mountaineering.pdf'))
    ,@('13_FM_21-10_Higiene_e_Saneamento_de_Campo.pdf',           ($tp + 'FM-21-10-Field-Hygiene-and-Sanitation.pdf'))
    ,@('14_FM_4-25.11_Primeiros_Socorros.pdf',                    ($tp + 'FM-4-25-11-First_Aid.pdf'))
    ,@('15_ST_31-91B_Manual_Medico_Forcas_Especiais.pdf',         ($tp + '2022/12/ST-31-91B-US-Army-Special-Forces-Medical-Handbook.pdf'))
    ,@('17_FEMA_Are_You_Ready_Guia_do_Cidadao.pdf',               ($tp + 'FEMA-Citizen-Preparedness-Guide.pdf'))
    ,@('18_Nuclear_War_Survival_Skills_Kearny.pdf',               ($tp + '2022/09/Nuclear-War-Survival-Skills-Cresson-Kearny.pdf'))
    ,@('19_Canada_Down_But_Not_Out_Sobrevivencia.pdf',            ($tp + '2023/03/Down-But-Not-Out-Canadian-Survival-Manual.pdf'))
    ,@('20_Canada_Military_Fieldcraft.pdf',                       ($tp + 'Canadian-Military-Fieldcraft.pdf'))
    ,@('21_Seton_Book_of_Woodcraft_1913.pdf',                     ($tp + 'The-Book-of-Woodcraft-1913.pdf'))
)
foreach ($c in $cursos) {
    Baixa $c[1] (Join-Path (Pasta '5_Acervo\Sobrevivencia\Cursos_EN') $c[0]) $c[0] | Out-Null
}

# 2. PDFs brasileiros/portugues ------------------------------------------
$pt = @(
    ,@('Agua',            'SODIS_Desinfeccao_Solar_da_Agua.pdf', 'https://bdd.pseau.org/outils/ouvrages/eawag_sodis_desinfeccao_solar_da_agua_2005.pdf')
    # apostila original da ESAB (site oficial saiu do ar; copia de 2016 no Internet Archive)
    ,@('Solda',           'ESAB_Eletrodos_Revestidos.pdf', 'https://web.archive.org/web/2016id_/http://www.esab.com.br/br/pt/education/apostilas/upload/1901097rev1_apostilaeletrodosrevestidos_ok.pdf')
    # complemento pratico em portugues: cartilha do SENAR-PR
    ,@('Solda',           'SENAR_Soldador_Eletrodos_Revestidos.pdf', 'https://sistemafaep.org.br/wp-content/uploads/2021/05/PR.0302-Soldador_web.pdf')
    ,@('Saude\Hesperian', 'pt__cgeh_2013_toc.pdf', 'https://hesperian.org/wp-content/uploads/pdf/pt_cgeh_2013/pt_cgeh_2013_toc.pdf')
)
foreach ($p in $pt) {
    Baixa $p[2] (Join-Path (Pasta ('5_Acervo\' + $p[0])) $p[1]) $p[1] | Out-Null
}

# 3. GPXSee (mapas no PC): versao Windows x64 mais nova do SourceForge ---
$rss = Pagina 'https://sourceforge.net/projects/gpxsee/rss?path=/Windows' $UA_CURL
$gm = [regex]::Match($rss, 'files/Windows/(GPXSee-[\d.]+_x64\.exe)/download')
if ($gm.Success) {
    $gurl = 'https://sourceforge.net/projects/gpxsee/files/Windows/' + $gm.Groups[1].Value + '/download'
    Baixa $gurl (Join-Path (Pasta '6_Instaladores') 'GPXSee_Instalador.exe') "GPXSee ($($gm.Groups[1].Value))" $UA_CURL | Out-Null
} else { Falha 'Nao achei o GPXSee para Windows no SourceForge.' }

# 4. ZIMs ----------------------------------------------------------------
# Wikcionario PT: so existe na versao sem imagens (nopic)
ZimPorNome 'wiktionary_pt_all' 'Wikcionario PT (dicionario)'

if ($querSubst) {
    # Substitutos do WikiHow (que saiu do Kiwix): bibliotecas praticas
    ZimPorNome 'usda-2015_en'                   'Guia USDA de conservas caseiras'
    ZimPorNome 'zimgit-medicine_en'             'Biblioteca medica (preppers)'
    ZimPorNome 'zimgit-knots_en'                'Biblioteca de nos'
    ZimPorNome 'wikiciv_en_all'                 'WikiCiv (reconstruir tecnologias basicas)'
    ZimPorNome 'nhs.uk_en_medicines'            'NHS - remedios de A a Z'
    ZimPorNome 'cooking.stackexchange.com_en_all' 'Cozinha (Stack Exchange)'
    ZimPorNome 'cd3wdproject.org_en_all'        'CD3WD (tecnologia apropriada)'
    ZimPorNome 'zimgit-post-disaster_en'        'Biblioteca pos-desastre'
    ZimPorNome 'energypedia_en_all'             'Energypedia (energia solar, biogas...)'
    ZimPorNome 'gardening.stackexchange.com_en_all' 'Horta e jardinagem (Stack Exchange)'
}
if ($querSurvivor) {
    ZimPorNome 'survivorlibrary.com_en_all'     'Survivor Library (~205 GB)'
}

# ---------------------------------------------------------------------
Escreve '===== Fim (extras) =====' 'White'
Write-Host ''
if ($script:Falhas.Count -gt 0) {
    Write-Host "  $($script:Falhas.Count) item(ns) com problema:" -ForegroundColor Yellow
    $script:Falhas | ForEach-Object { Write-Host "   - $_" -ForegroundColor Yellow }
} else {
    Write-Host '  Tudo certo.' -ForegroundColor Green
}
Write-Host ''
Write-Host '  Para a biblioteca usar o que chegou: abra o app e clique em' -ForegroundColor White
Write-Host '  Situacao > "Atualizar biblioteca" (ou so feche e abra o app de novo).' -ForegroundColor White
Write-Host ''
if (-not $SemPerguntas) { Read-Host '  Pressione ENTER para fechar' }
