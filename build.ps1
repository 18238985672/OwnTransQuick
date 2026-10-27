$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

New-Item -ItemType Directory -Force build | Out-Null
python -c "import app; app.save_logo_ico('build/logo.ico'); app.write_version_file('build/version.txt')"
if ($LASTEXITCODE -ne 0) {
    throw "Icon or version file generation failed."
}

python -m PyInstaller --noconfirm --clean --onefile --windowed --name OwnTranslation --icon build/logo.ico --version-file build/version.txt --collect-all pystray --collect-all PIL --collect-all pykakasi app.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed."
}

python -c "import os; os.replace('dist/OwnTranslation.exe', 'dist/Own\u7ffb\u8bd1.exe')"
if ($LASTEXITCODE -ne 0) {
    throw "EXE rename failed."
}

Write-Host "Build completed successfully."
