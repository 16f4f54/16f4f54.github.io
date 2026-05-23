# launcher.ps1
# Uruchamiany przez uruchom.bat – bez widocznego okna terminala.
# Sprawdza czy Python jest zainstalowany, instaluje jeśli nie ma, odpala aplikację.

Add-Type -AssemblyName System.Windows.Forms

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$APP_TITLE = "OtoMoto → Autoplac.pl"
$PYTHON_URL = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
$PYW_FILE = Join-Path $ScriptDir "uruchom.pyw"


# ── Szukaj Pythona ────────────────────────────────────────────────────────────

function Find-Pythonw {
    # 1. py launcher (Windows Python Launcher – najlepsze źródło)
    if (Get-Command "py" -ErrorAction SilentlyContinue) {
        $exe = & py -3 -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" 2>$null
        if ($exe -and (Test-Path $exe)) { return $exe }
    }

    # 2. Typowe lokalizacje instalacji użytkownika i systemowej
    foreach ($v in @("313","312","311","310")) {
        foreach ($base in @(
            "$env:LOCALAPPDATA\Programs\Python\Python$v",
            "C:\Python$v",
            "C:\Program Files\Python$v",
            "$env:ProgramFiles\Python$v"
        )) {
            $p = Join-Path $base "pythonw.exe"
            if (Test-Path $p) { return $p }
        }
    }

    # 3. PATH
    $found = Get-Command "pythonw" -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }

    return $null
}


# ── Instalacja Pythona ────────────────────────────────────────────────────────

function Install-Python {
    $ans = [System.Windows.Forms.MessageBox]::Show(
        "Python nie jest zainstalowany na tym komputerze.`n`n" +
        "Czy pobrać i zainstalować Python 3.12 automatycznie?`n" +
        "(~25 MB, instalacja tylko dla tego konta – bez uprawnień administratora)",
        $APP_TITLE,
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )

    if ($ans -ne [System.Windows.Forms.DialogResult]::Yes) { return $false }

    [System.Windows.Forms.MessageBox]::Show(
        "Trwa pobieranie Pythona (~25 MB).`nKliknij OK i poczekaj chwilę – może potrwać kilka minut.",
        $APP_TITLE,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null

    $installer = Join-Path $env:TEMP "python_setup_$([System.Guid]::NewGuid().ToString('N')).exe"

    try {
        Invoke-WebRequest -Uri $PYTHON_URL -OutFile $installer -UseBasicParsing -ErrorAction Stop
    } catch {
        [System.Windows.Forms.MessageBox]::Show(
            "Błąd pobierania Pythona.`nSprawdź połączenie z internetem i spróbuj ponownie.`n`n$_",
            $APP_TITLE, [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
        return $false
    }

    # Instalacja dla bieżącego użytkownika (bez UAC), Python dodany do PATH
    $proc = Start-Process -FilePath $installer `
        -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1" `
        -Wait -PassThru
    Remove-Item $installer -ErrorAction SilentlyContinue

    if ($proc.ExitCode -ne 0) {
        [System.Windows.Forms.MessageBox]::Show(
            "Instalacja Pythona nie powiodła się (kod: $($proc.ExitCode)).`n" +
            "Pobierz Python ręcznie z python.org",
            $APP_TITLE, [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
        return $false
    }

    return $true
}


# ── Main ──────────────────────────────────────────────────────────────────────

$pythonw = Find-Pythonw

if (-not $pythonw) {
    $installed = Install-Python
    if (-not $installed) { exit 1 }

    # Szukaj ponownie po instalacji
    $pythonw = Find-Pythonw

    if (-not $pythonw) {
        [System.Windows.Forms.MessageBox]::Show(
            "Python zainstalowany!`n`nUruchom plik ponownie – przy kolejnym otwarciu aplikacja wystartuje normalnie.",
            $APP_TITLE, [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        exit 0
    }
}

# Uruchom GUI aplikacji (pythonw.exe = Python bez okna konsoli)
Start-Process -FilePath $pythonw -ArgumentList "`"$PYW_FILE`""
