# Leitor de texto em imagens (OCR) usando o que o próprio Windows 10/11 já tem:
#   Windows.Data.Pdf  -> desenha a página do PDF como imagem
#   Windows.Media.Ocr -> reconhece o texto (offline, nos idiomas de OCR instalados no Windows)
# O app (biblioteca/ocr.py) manda este texto pela entrada padrão de "powershell -Command -" e depois
# um pedido por linha. Cada resposta sai numa linha começando com @@RESULTADO@@ (JSON).
# Obs.: no modo "-Command -", cada bloco de várias linhas precisa terminar com uma linha em branco.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.DataReader, Windows.Storage.Streams, ContentType = WindowsRuntime]
$Global:AsTask1 = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$Global:AsTaskA = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
$Global:PdfAberto = $null
$Global:PdfCaminho = ''
$Global:Motores = @{}

function Esperar($op, [Type]$tipo) {
    $t = $Global:AsTask1.MakeGenericMethod($tipo).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    return $t.Result
}

function EsperarAcao($op) {
    $t = $Global:AsTaskA.Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
}

function Texto64([string]$b64) {
    return [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64))
}

function Motor([string]$idioma) {
    if ($Global:Motores.ContainsKey($idioma)) { return $Global:Motores[$idioma] }
    $m = $null
    if ($idioma) {
        $l = New-Object Windows.Globalization.Language $idioma
        if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($l)) { $m = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($l) }
    }
    if (-not $m) { $m = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
    if (-not $m) { $m = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage(([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Select-Object -First 1)) }
    $Global:Motores[$idioma] = $m
    return $m
}

function Reconhecer($bmp, [string]$idioma) {
    $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($bmp.PixelWidth -gt $max -or $bmp.PixelHeight -gt $max) { throw "imagem grande demais ($($bmp.PixelWidth)x$($bmp.PixelHeight))" }
    $bmp = [Windows.Graphics.Imaging.SoftwareBitmap]::Convert($bmp, [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)
    $m = Motor $idioma
    $r = Esperar ($m.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $linhas = @()
    foreach ($ln in $r.Lines) {
        $x0 = 1e9; $y0 = 1e9; $x1 = 0; $y1 = 0
        foreach ($w in $ln.Words) {
            $b = $w.BoundingRect
            if ($b.X -lt $x0) { $x0 = $b.X }
            if ($b.Y -lt $y0) { $y0 = $b.Y }
            if ($b.X + $b.Width -gt $x1) { $x1 = $b.X + $b.Width }
            if ($b.Y + $b.Height -gt $y1) { $y1 = $b.Y + $b.Height }
        }
        $linhas += , @($ln.Text, [int]$x0, [int]$y0, [int]$x1, [int]$y1)
    }
    return @{ linhas = $linhas; idioma = $m.RecognizerLanguage.LanguageTag; largura = $bmp.PixelWidth; altura = $bmp.PixelHeight; angulo = $r.TextAngle }
}

function LerImagem([string]$b64caminho, [string]$idioma) {
    $arq = Esperar ([Windows.Storage.StorageFile]::GetFileFromPathAsync((Texto64 $b64caminho))) ([Windows.Storage.StorageFile])
    $fluxo = Esperar ($arq.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $dec = Esperar ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($fluxo)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
        $w = $dec.OrientedPixelWidth; $h = $dec.OrientedPixelHeight
        $tr = New-Object Windows.Graphics.Imaging.BitmapTransform
        if ($w -gt $max -or $h -gt $max) {
            $f = [Math]::Min(($max - 10) / $w, ($max - 10) / $h)
            $tr.ScaledWidth = [uint32]($w * $f); $tr.ScaledHeight = [uint32]($h * $f)
        }
        $bmp = Esperar ($dec.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8, [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $tr, [Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation, [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        return Reconhecer $bmp $idioma
    } finally { $fluxo.Dispose() }
}

function AbrirPdf([string]$caminho) {
    if ($Global:PdfCaminho -ne $caminho) {
        $arq = Esperar ([Windows.Storage.StorageFile]::GetFileFromPathAsync($caminho)) ([Windows.Storage.StorageFile])
        $Global:PdfAberto = Esperar ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($arq)) ([Windows.Data.Pdf.PdfDocument])
        $Global:PdfCaminho = $caminho
    }
    return $Global:PdfAberto
}

function LerPaginaPdf([string]$b64caminho, [int]$pagina, [string]$idioma, [string]$b64png) {
    $doc = AbrirPdf (Texto64 $b64caminho)
    if ($pagina -lt 1 -or $pagina -gt $doc.PageCount) { throw "página $pagina não existe (o PDF tem $($doc.PageCount))" }
    $pg = $doc.GetPage($pagina - 1)
    try {
        $fluxo = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
        $op = New-Object Windows.Data.Pdf.PdfPageRenderOptions
        $op.DestinationWidth = [uint32]([Math]::Max(1200, [Math]::Min(2200, $pg.Size.Width * 2.6)))
        EsperarAcao ($pg.RenderToStreamAsync($fluxo, $op))
        if ($b64png) {
            # guarda a imagem da página para o app mostrar ao lado do texto
            $fluxo.Seek(0)
            $leitor = New-Object Windows.Storage.Streams.DataReader($fluxo.GetInputStreamAt(0))
            $n = Esperar ($leitor.LoadAsync([uint32]$fluxo.Size)) ([uint32])
            $bytes = New-Object byte[] $n
            $leitor.ReadBytes($bytes)
            [IO.File]::WriteAllBytes((Texto64 $b64png), $bytes)
            $leitor.Dispose()
        }
        $fluxo.Seek(0)
        $dec = Esperar ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($fluxo)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bmp = Esperar ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $r = Reconhecer $bmp $idioma
        $r.paginas = $doc.PageCount
        return $r
    } finally { $pg.Dispose() }
}

function Responder([string]$cmd, [string]$a, [string]$b, [string]$c, [string]$d) {
    try {
        if ($cmd -eq 'idiomas') { $r = @{ idiomas = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | ForEach-Object { $_.LanguageTag }) } }
        elseif ($cmd -eq 'imagem') { $r = LerImagem $a $b }
        elseif ($cmd -eq 'pdf') { $r = LerPaginaPdf $a ([int]$b) $c $d }
        elseif ($cmd -eq 'fechar_pdf') { $Global:PdfAberto = $null; $Global:PdfCaminho = ''; $r = @{} }
        else { throw "comando desconhecido: $cmd" }
        $r.ok = $true
    } catch {
        $r = @{ ok = $false; erro = $_.Exception.Message }
    }
    [Console]::Out.WriteLine('@@RESULTADO@@' + (ConvertTo-Json $r -Compress -Depth 5))
    [Console]::Out.Flush()
}

[Console]::Out.WriteLine('@@RESULTADO@@{"ok":true,"pronto":true}')

