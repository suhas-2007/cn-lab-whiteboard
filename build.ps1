$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m unittest -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed' }
python -m PyInstaller --noconfirm --clean --onefile --name WhiteboardServer --distpath release --workpath tmp/server-build --specpath tmp server.py
if ($LASTEXITCODE -ne 0) { throw 'Server build failed' }
python -m PyInstaller --noconfirm --clean --onefile --windowed --name WhiteboardClient --distpath release --workpath tmp/client-build --specpath tmp client.py
if ($LASTEXITCODE -ne 0) { throw 'Client build failed' }
Write-Host 'Executables are in release. Python is not needed on the demo computers.'
