# vgm2wav2

VGM/S98/DRO/GYM 형식의 게임 음악 파일을 WAV/MP3/AAC 등으로 변환하는 CLI 도구.
[libvgm](https://github.com/ValleyBell/libvgm)을 기반으로 하며, 단일 파일 외에 **폴더**와 **ZIP 아카이브** 일괄 변환을 지원합니다.

## 지원 형식

### 입력

| 형식 | 설명 |
|------|------|
| `.vgm` | Video Game Music |
| `.vgz` | gzip 압축 VGM (자동 감지) |
| `.s98` | S98 (PC-88/PC-98 등) |
| `.dro` | DOSBox Raw OPL |
| `.gym` | Genesis YM2612 |
| 디렉토리 | 하위 폴더까지 재귀 탐색 |
| `.zip` | ZIP 내 지원 파일 일괄 변환 |

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
- [ffmpeg](https://ffmpeg.org/) — WAV 이외 포맷 출력 시 필요 (런타임)

libzip 없이도 빌드 및 실행 가능합니다. ZIP 파일 입력 시에만 에러가 출력됩니다.

macOS (Homebrew):
```bash
brew install libzip ffmpeg
```

### 빌드

```bash
git clone --recurse-submodules https://github.com/yourname/vgm2wav2
cd vgm2wav2
cmake -B build -S .
cmake --build build
```

빌드 결과물:
- `build/vgm2wav2` — 이 프로젝트의 메인 도구 (폴더/ZIP/포맷 변환 지원)
- `build/libvgm/bin/vgm2wav` — libvgm 원본 (단일 파일 전용, stdout 출력 가능)

## 사용법

```
vgm2wav2 [options] <input> <output>

  input   : VGM/VGZ/S98/DRO/GYM 파일, 디렉토리, 또는 .zip 아카이브
  output  : 오디오 파일 (단일 파일 입력) 또는 디렉토리 (폴더/ZIP 입력)

Options:
  --format fmt     출력 포맷: wav, mp3, aac, flac, ... (기본값: wav)
  --samplerate n   샘플레이트 (기본값: 44100)
  --bps n          비트 심도: 16 / 24 / 32 (기본값: 16)
  --fade x         페이드아웃 길이, 초 단위 (기본값: 8.0)
  --loops n        루프 횟수 (기본값: 2)
```

### 예시

```bash
# 단일 파일 (WAV)
vgm2wav2 bgm.vgm bgm.wav
vgm2wav2 bgm.vgz bgm.wav

# MP3 / AAC 변환
vgm2wav2 --format mp3 bgm.vgz bgm.mp3
vgm2wav2 --format aac bgm.vgz bgm.m4a

# 폴더 일괄 변환 (디렉토리 구조 유지)
vgm2wav2 --format mp3 music/ output/

# ZIP 아카이브
vgm2wav2 --format aac songs.zip output/

# 옵션 지정
vgm2wav2 --format mp3 --samplerate 48000 --loops 1 --fade 3.0 bgm.vgm bgm.mp3
```

폴더 및 ZIP 입력 시 출력 디렉토리가 없으면 자동 생성되며, 원본 하위 디렉토리 구조가 그대로 유지됩니다.

WAV 이외 포맷은 렌더링한 PCM을 ffmpeg에 파이프로 전달해 인코딩합니다. ffmpeg가 설치되어 있지 않으면 시작 시 에러가 출력됩니다.

## 라이선스

libvgm의 라이선스를 따릅니다.
