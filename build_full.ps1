$env:TEMP = $env:USERPROFILE + "\AppData\Local\Temp"
$env:TMP  = $env:TEMP
Set-Location $PSScriptRoot
cmake -B build -S . 2>&1
cmake --build build --target vgm2wav2 2>&1

# Copy MinGW runtime DLLs required by vgm2wav2.exe
$mingw = "C:\msys64\mingw64\bin"
foreach ($dll in @("libgme.dll", "libwinpthread-1.dll", "zlib1.dll", "libstdc++-6.dll", "libgcc_s_seh-1.dll")) {
    $src = Join-Path $mingw $dll
    if (Test-Path $src) { Copy-Item $src build\ -Force; Write-Host "Copied $dll" }
}
