# vgm2wav2

VGM/S98/DRO/GYM/NSF/SPC 등 게임 음악 파일을 WAV/MP3/AAC 등으로 변환하는 CLI 도구.
[libvgm](https://github.com/ValleyBell/libvgm)과 [libgme](https://github.com/libgme/game-music-emu)를 기반으로 하며, 단일 파일 외에 **폴더**와 **ZIP 아카이브** 일괄 변환을 지원합니다.

## 지원 형식

### 입력

| 형식 | 엔진 | 설명 |
|------|------|------|
| `.vgm` / `.vgz` | libvgm | Video Game Music (gzip 압축 자동 감지) |
| `.s98` | libvgm | PC-88/PC-98 등 |
| `.dro` | libvgm | DOSBox Raw OPL |
| `.gym` | libvgm | Genesis YM2612 |
| `.nsf` / `.nsfe` | GME | NES/Famicom |
| `.spc` | GME | SNES/Super Famicom |
| `.gbs` | GME | Game Boy |
| `.ay` | GME | ZX Spectrum / Amstrad CPC |
| `.hes` | GME | PC-Engine/TurboGrafx-16 |
| `.kss` | GME | MSX / SMS |
| `.sap` | GME | Atari |
| 디렉토리 | — | 하위 폴더까지 재귀 탐색 |
| `.zip` | — | ZIP 내 지원 파일 일괄 변환 |

### 출력

| 포맷 | `--format` 값 | 의존성 |
|------|--------------|--------|
| WAV  | `wav` (기본값) | 없음 |
| MP3  | `mp3` | ffmpeg |
| AAC  | `aac` | ffmpeg |
| FLAC | `flac` | ffmpeg |
| 기타 | ffmpeg 지원 포맷 이름 | ffmpeg |

## 빌드

### 의존성

- CMake 3.12+
- C++17 컴파일러
- zlib (libvgm 의존성, macOS 기본 포함)
- [libzip](https://libzip.org/) — ZIP 지원 시 필요 (선택)
- [libgme](https://github.com/libgme/game-music-emu) — GME 엔진 지원 시 필요 (선택)
- [ffmpeg](https://ffmpeg.org/) — WAV 이외 포맷 출력 시 필요 (런타임)

libzip, libgme 없이도 빌드 및 실행 가능합니다. 각 기능 사용 시에만 에러가 출력됩니다.

macOS (Homebrew):
```bash
brew install libzip game-music-emu ffmpeg
```

### 빌드

```bash
git clone --recurse-submodules https://github.com/yourname/vgm2wav2
cd vgm2wav2
cmake -B build -S .
cmake --build build
```

빌드 시 libgme가 감지되면 `libgme found: GME engine support enabled` 메시지가 출력됩니다.

빌드 결과물:
- `build/vgm2wav2` — 이 프로젝트의 메인 도구 (폴더/ZIP/포맷 변환 지원)
- `build/libvgm/bin/vgm2wav` — libvgm 원본 (단일 파일 전용, stdout 출력 가능)

## TUI 플레이어

터미널에서 직접 재생할 수 있는 Textual 기반 TUI 플레이어입니다.

### 의존성 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 실행

```bash
# 단일 파일
.venv/bin/python player.py bgm.vgm

# M3U 재생목록
.venv/bin/python player.py playlist.m3u

# 여러 파일
.venv/bin/python player.py *.spc
```

### 키 조작

| 키 | 동작 |
|----|------|
| `Space` | 재생 / 일시정지 |
| `N` | 다음 곡 |
| `P` | 이전 곡 (3초 이내: 현재 곡 처음부터) |
| `Enter` | 재생목록에서 선택한 곡 재생 |
| `H` | 도움말 |
| `Q` | 종료 |

### yazi 연동

yazi 파일 매니저에서 지원 확장자 파일에 Enter를 누르면 플레이어가 실행되도록 설정할 수 있습니다.

래퍼 스크립트 설치:
```bash
# 프로젝트 경로를 실제 경로로 수정
cat > ~/.local/bin/vgm-play <<'EOF'
#!/bin/bash
exec /path/to/vgm2wav2/.venv/bin/python /path/to/vgm2wav2/player.py "$@"
EOF
chmod +x ~/.local/bin/vgm-play
```

`~/.config/yazi/yazi.toml`:
```toml
[opener]
vgm = [
  { run = 'vgm-play "$@"', block = true, desc = "VGM Player" },
]

[open]
rules = [
  { name = "*.{vgm,vgz,s98,dro,gym,nsf,nsfe,spc,ay,gbs,hes,kss,sap}", use = "vgm" },
]
```

## 변환 사용법

```
vgm2wav2 [options] <input> [output]

  input   : 오디오 파일, 디렉토리, 또는 .zip 아카이브
  output  : 오디오 파일 (단일 파일 입력) 또는 디렉토리 (폴더/ZIP 입력)
            (--play / --stdout 사용 시 생략 가능)

Options:
  --play           터미널에서 직접 재생 (ffplay 필요)
  --stdout         raw PCM (s16le 44100 stereo) 을 stdout으로 출력
  --format fmt     출력 포맷: wav, mp3, aac, flac, ... (기본값: wav)
  --engine e       엔진 선택: auto, libvgm, gme (기본값: auto)
  --samplerate n   샘플레이트 (기본값: 44100)
  --bps n          비트 심도: 16 / 24 / 32 (기본값: 16; GME 엔진에서는 무시됨)
  --fade x         페이드아웃 길이, 초 단위 (기본값: 8.0)
  --loops n        루프 횟수 (기본값: 2)
  --skip           출력 파일이 이미 존재하면 건너뜀
  --dryrun         실제 변환 없이 동작만 출력
```

### 엔진 선택

확장자에 따라 엔진이 자동 선택됩니다:

- `.vgm` / `.vgz` / `.s98` / `.dro` / `.gym` → libvgm
- `.nsf` / `.spc` / `.gbs` / `.ay` / `.hes` / `.kss` / `.sap` / `.nsfe` → GME

`--engine gme` 또는 `--engine libvgm`으로 강제 지정할 수 있습니다.

NSF 등 다중 트랙 파일은 모든 트랙을 자동으로 추출합니다 (`game_01.wav`, `game_02.wav`, ...).

### 예시

```bash
# 단일 파일 (WAV)
vgm2wav2 bgm.vgm bgm.wav
vgm2wav2 game.spc game.wav

# NES 음악 전체 트랙 추출
vgm2wav2 game.nsf output/
# → output/game_01.wav, output/game_02.wav, ...

# MP3 / AAC 변환
vgm2wav2 --format mp3 bgm.vgz bgm.mp3
vgm2wav2 --format flac game.spc game.flac

# 폴더 일괄 변환 (디렉토리 구조 유지)
vgm2wav2 --format mp3 music/ output/

# ZIP 아카이브
vgm2wav2 --format aac songs.zip output/

# 엔진 강제 지정
vgm2wav2 --engine gme game.vgm game.wav

# 옵션 지정
vgm2wav2 --format mp3 --samplerate 48000 --loops 1 --fade 3.0 bgm.vgm bgm.mp3
```

폴더 및 ZIP 입력 시 출력 디렉토리가 없으면 자동 생성되며, 원본 하위 디렉토리 구조가 그대로 유지됩니다.

WAV 이외 포맷은 렌더링한 PCM을 ffmpeg에 파이프로 전달해 인코딩합니다. ffmpeg가 설치되어 있지 않으면 시작 시 에러가 출력됩니다.

## 라이선스

libvgm의 라이선스를 따릅니다.
