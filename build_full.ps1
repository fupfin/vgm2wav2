$env:TEMP = $env:USERPROFILE + "\AppData\Local\Temp"
$env:TMP  = $env:TEMP

# MinGW를 PATH 앞에 추가 - cmake가 올바른 컴파일러/ninja를 찾도록
$env:PATH = "C:\msys64\mingw64\bin;C:\msys64\usr\bin;" + $env:PATH

Set-Location $PSScriptRoot

cmake -B build -S . `
    -G Ninja `
    -DCMAKE_C_COMPILER=gcc `
    -DCMAKE_CXX_COMPILER=g++ `
    -DCMAKE_BUILD_TYPE=Release
if ($LASTEXITCODE -ne 0) { Write-Error "CMake configure failed"; exit 1 }

cmake --build build --target vgm2wav2
if ($LASTEXITCODE -ne 0) { Write-Error "Build failed"; exit 1 }

# Copy MinGW runtime DLLs required by vgm2wav2.exe
$mingw = "C:\msys64\mingw64\bin"
foreach ($dll in @("libgme.dll", "libwinpthread-1.dll", "zlib1.dll", "libstdc++-6.dll", "libgcc_s_seh-1.dll")) {
    $src = Join-Path $mingw $dll
    if (Test-Path $src) { Copy-Item $src build\ -Force; Write-Host "Copied $dll" }
}
