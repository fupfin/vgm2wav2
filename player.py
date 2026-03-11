#!/usr/bin/env python3
"""VGM TUI Player — Textual frontend for vgm2wav2

Usage:
    python player.py [--bin PATH] <file.vgm|file.m3u> [file2 ...]

Dependencies:
    pip install textual sounddevice numpy
"""

import ctypes
import gzip
import os
import queue as _queue
import re
import signal
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

IS_WINDOWS = sys.platform == "win32"


def _suspend_process(pid: int):
    if IS_WINDOWS:
        handle = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)
        if handle:
            ctypes.windll.ntdll.NtSuspendProcess(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGSTOP)


def _resume_process(pid: int):
    if IS_WINDOWS:
        handle = ctypes.windll.kernel32.OpenProcess(0x0800, False, pid)
        if handle:
            ctypes.windll.ntdll.NtResumeProcess(handle)
            ctypes.windll.kernel32.CloseHandle(handle)
    else:
        os.kill(pid, signal.SIGCONT)


import numpy as np
import sounddevice as sd
from rich.text import Text as RichText
from textual.app import App, ComposeResult
from textual.message import Message
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import (
    Checkbox, DirectoryTree, Footer, Header, Input, Label, ListItem, ListView,
    Select, Static, TabbedContent, TabPane,
)

# ── Constants ─────────────────────────────────────────────────────────────

AUDIO_EXTS = {
    '.vgm', '.vgz', '.s98', '.dro', '.gym',
    '.nsf', '.nsfe', '.spc', '.gbs', '.ay', '.hes', '.kss', '.sap',
}
STD_AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.aac', '.ogg', '.opus', '.m4a'}
PLAYLIST_EXTS = {'.m3u'}

SAMPLE_RATE  = 44100
CHANNELS     = 2
CHUNK_FRAMES = 4096         # was 2048 — larger block = fewer callbacks, less underrun risk
VIS_BINS     = 60           # spectrum bars
_QUEUE_MAX   = 80           # ~80 * 4096/44100 ≈ 7.4 s of buffer headroom

VIS_CHARS = " ▁▂▃▄▅▆▇█"

# ── Metadata parsing ──────────────────────────────────────────────────────

def _read_bytes(path: str) -> bytes:
    p = Path(path)
    if p.suffix.lower() == ".vgz":
        with gzip.open(path, "rb") as f:
            return f.read()
    with open(path, "rb") as f:
        return f.read()


def _parse_vgm(path: str) -> dict:
    meta = {"title": "", "game": "", "system": "", "author": "", "date": ""}
    try:
        data = _read_bytes(path)
        if data[:4] != b"Vgm ":
            return meta
        gd3_rel = struct.unpack_from("<I", data, 0x14)[0]
        if gd3_rel == 0:
            return meta
        pos = 0x14 + gd3_rel
        if pos + 12 > len(data) or data[pos:pos+4] != b"Gd3 ":
            return meta
        pos += 12  # skip tag + version + length

        def next_str() -> str:
            nonlocal pos
            end = pos
            while end + 1 < len(data) and (data[end] or data[end + 1]):
                end += 2
            s = data[pos:end].decode("utf-16-le", errors="replace")
            pos = end + 2
            return s

        meta["title"]  = next_str(); next_str()   # EN, JP
        meta["game"]   = next_str(); next_str()
        meta["system"] = next_str(); next_str()
        meta["author"] = next_str(); next_str()
        meta["date"]   = next_str()
    except Exception:
        pass
    return meta


def _parse_spc(path: str) -> dict:
    meta = {"title": "", "game": "", "system": "SPC700 (SNES)", "author": "", "date": ""}
    try:
        with open(path, "rb") as f:
            h = f.read(256)
        if not h[:27].startswith(b"SNES-SPC700 Sound File Data"):
            return meta
        if h[0x23] == 0x1a:
            def s(a, b): return h[a:b].rstrip(b"\x00").decode("ascii", errors="replace")
            meta["title"]  = s(0x2e, 0x4e)
            meta["game"]   = s(0x4e, 0x5e)
            meta["author"] = s(0xb1, 0xc1)
    except Exception:
        pass
    return meta


def _parse_nsf(path: str) -> dict:
    meta = {"title": "", "game": "", "system": "NES", "author": "", "date": ""}
    try:
        with open(path, "rb") as f:
            h = f.read(128)
        if h[:5] != b"NESM\x1a":
            return meta
        def s(a, b): return h[a:b].rstrip(b"\x00").decode("ascii", errors="replace")
        meta["title"]  = s(0x0e, 0x2e)
        meta["author"] = s(0x2e, 0x4e)
    except Exception:
        pass
    return meta


def get_metadata(path: str) -> dict:
    ext = Path(path).suffix.lower()
    if ext in (".vgm", ".vgz"):
        m = _parse_vgm(path)
    elif ext == ".spc":
        m = _parse_spc(path)
    elif ext in (".nsf", ".nsfe"):
        m = _parse_nsf(path)
    else:
        m = {"title": "", "game": "", "system": "", "author": "", "date": ""}
    if not m.get("title"):
        m["title"] = Path(path).stem
    return m


# ── M3U parsing ───────────────────────────────────────────────────────────

def parse_m3u(path: str) -> list[tuple[str, Optional[str]]]:
    """Returns list of (filepath, extinf_title|None)."""
    entries: list[tuple[str, Optional[str]]] = []
    base = Path(path).parent
    extinf_title: Optional[str] = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#EXTINF:"):
                    parts = line[8:].split(",", 1)
                    extinf_title = parts[1].strip() if len(parts) > 1 else None
                elif not line.startswith("#"):
                    fp = Path(line)
                    if not fp.is_absolute():
                        fp = base / fp
                    entries.append((str(fp), extinf_title))
                    extinf_title = None
    except Exception:
        pass
    return entries


def write_m3u(path: str, playlist: list[tuple[str, Optional[str]]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        for fp, title in playlist:
            display = title or Path(fp).stem
            f.write(f"#EXTINF:-1,{display}\n")
            f.write(f"{fp}\n")


# ── Audio Engine ──────────────────────────────────────────────────────────

class AudioEngine:
    def __init__(self, binary: str):
        self._bin = binary
        self._proc: Optional[subprocess.Popen] = None
        self._stream: Optional[sd.OutputStream] = None
        # Queue of int16 numpy chunks — avoids large array concat under a lock.
        # queue.Queue operations are O(1) pointer ops, minimising GIL hold time
        # in the audio callback and reader thread.
        self._chunk_q: _queue.Queue = _queue.Queue(maxsize=_QUEUE_MAX)
        self._leftover = np.zeros(0, dtype=np.int16)
        self._running = False
        self._paused = False
        self._elapsed = 0.0
        self._duration = 0.0
        self.vis_data = np.zeros(VIS_BINS)
        self._vis_lock = threading.Lock()
        self._on_finish: Optional[callable] = None
        # Pre-computed Hanning window — avoids per-callback allocation
        self._hanning = np.hanning(CHUNK_FRAMES)

    def play(self, filepath: str, loops: int = 2, fade: float = 8.0,
             on_finish=None):
        self.stop()
        self._running = True
        self._paused = False
        self._elapsed = 0.0
        self._duration = 0.0
        self._on_finish = on_finish
        self._leftover = np.zeros(0, dtype=np.int16)
        # drain any leftover chunks from a previous play
        while not self._chunk_q.empty():
            try:
                self._chunk_q.get_nowait()
            except _queue.Empty:
                break

        ext = Path(filepath).suffix.lower()
        if ext in STD_AUDIO_EXTS:
            cmd = ['ffmpeg', '-hide_banner', '-i', filepath,
                   '-f', 's16le', '-ac', '2', '-ar', str(SAMPLE_RATE), '-']
        else:
            cmd = [self._bin, "--stdout", "--bps", "16",
                   f"--loops={loops}", f"--fade={fade}", filepath]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # Parse duration from stderr:
        #   vgm2wav2 → "filename  [12.3s]"
        #   ffmpeg   → "  Duration: 00:03:45.23, ..."
        def _stderr_reader():
            for raw in self._proc.stderr:
                line = raw.decode("utf-8", errors="replace")
                m = re.search(r'\[(\d+\.?\d*)s\]', line)
                if m:
                    self._duration = float(m.group(1))
                    break
                m = re.search(r'Duration:\s*(\d+):(\d+):(\d+\.?\d*)', line)
                if m:
                    h, mn, sc = int(m.group(1)), int(m.group(2)), float(m.group(3))
                    self._duration = h * 3600 + mn * 60 + sc
                    break
            self._proc.stderr.read()  # drain rest

        threading.Thread(target=_stderr_reader, daemon=True).start()

        self._stream = sd.OutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_FRAMES,
            callback=self._audio_cb,
        )
        self._stream.start()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        chunk_bytes = CHUNK_FRAMES * CHANNELS * 2
        while self._running:
            data = self._proc.stdout.read(chunk_bytes)
            if not data:
                break
            # .copy() so the bytes object can be freed and we own the buffer
            samples = np.frombuffer(data, dtype=np.int16).copy()
            # queue.put blocks when full, giving natural backpressure
            # without any numpy allocation under a lock
            while self._running:
                try:
                    self._chunk_q.put(samples, timeout=0.05)
                    break
                except _queue.Full:
                    continue
        self._running = False
        if self._on_finish:
            self._on_finish()

    def _audio_cb(self, outdata, frames, time_info, status):
        need = frames * CHANNELS

        # Fill from leftover + queue chunks.
        # queue.get_nowait() is a fast O(1) deque pop — no large array allocation
        # under a shared lock, so the GIL is held only briefly.
        buf = self._leftover
        while len(buf) < need:
            try:
                chunk = self._chunk_q.get_nowait()
                buf = np.concatenate([buf, chunk]) if len(buf) else chunk
            except _queue.Empty:
                break

        if len(buf) >= need:
            outdata[:] = buf[:need].reshape(frames, CHANNELS)
            self._leftover = buf[need:]
        else:
            # underrun — output silence for missing samples
            flat = np.zeros(need, dtype=np.int16)
            flat[:len(buf)] = buf
            outdata[:] = flat.reshape(frames, CHANNELS)
            self._leftover = np.zeros(0, dtype=np.int16)

        if not self._paused:
            self._elapsed += frames / SAMPLE_RATE

        # Spectrum via FFT on left channel (pre-computed Hanning avoids alloc)
        mono = outdata[:, 0].astype(np.float32) / 32768.0
        if frames >= CHUNK_FRAMES:
            windowed = mono[:CHUNK_FRAMES] * self._hanning
            fft = np.abs(np.fft.rfft(windowed))
            n = len(fft)
            # Log-scale bin mapping (start at ~200 Hz to skip always-on sub-bass)
            edges = np.logspace(np.log10(max(n * 200 / SAMPLE_RATE, 1)),
                                np.log10(n), VIS_BINS + 1).astype(int)
            edges = np.clip(edges, 1, n)
            bins = np.array([fft[edges[i]:edges[i+1]].mean() if edges[i] < edges[i+1]
                             else fft[edges[i]]
                             for i in range(VIS_BINS)])
            peak = bins.max()
            bins = np.clip(bins / (peak + 1e-9), 0.0, 1.0) if peak > 1e-9 else bins
            bins = np.power(bins, 1.5)  # power curve: quiet bins fall toward zero
            with self._vis_lock:
                self.vis_data = bins

    def pause(self):
        if self._proc and not self._paused:
            self._paused = True
            _suspend_process(self._proc.pid)

    def resume(self):
        if self._proc and self._paused:
            self._paused = False
            _resume_process(self._proc.pid)

    def toggle_pause(self):
        if self._paused:
            self.resume()
        else:
            self.pause()

    def stop(self):
        self._running = False
        self._paused = False
        self._on_finish = None  # clear before killing to prevent spurious callback
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if self._proc:
            try:
                _resume_process(self._proc.pid)  # unfreeze if paused
            except Exception:
                pass
            self._proc.kill()
            self._proc.wait()
            self._proc = None
        while not self._chunk_q.empty():
            try:
                self._chunk_q.get_nowait()
            except _queue.Empty:
                break
        self._leftover = np.zeros(0, dtype=np.int16)
        with self._vis_lock:
            self.vis_data = np.zeros(VIS_BINS)
        self._elapsed = 0.0

    @property
    def elapsed(self) -> float:
        return self._elapsed

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def is_playing(self) -> bool:
        return self._running

    @property
    def is_paused(self) -> bool:
        return self._paused

    def get_vis(self) -> np.ndarray:
        with self._vis_lock:
            return self.vis_data.copy()


# ── Textual widgets ───────────────────────────────────────────────────────

class MetadataPanel(Static):
    DEFAULT_CSS = """
    MetadataPanel {
        height: 6;
        border: solid $primary;
        padding: 0 1;
    }
    """

    def update_meta(self, meta: dict):
        parts = [f"[bold white]{meta['title']}[/bold white]"]
        row2 = []
        if meta.get("game"):
            row2.append(f"[cyan]Game:[/cyan] {meta['game']}")
        if meta.get("system"):
            row2.append(f"[cyan]System:[/cyan] {meta['system']}")
        if row2:
            parts.append("  ".join(row2))
        if meta.get("author"):
            parts.append(f"[cyan]Author:[/cyan] {meta['author']}")
        if meta.get("date"):
            parts.append(f"[cyan]Date:[/cyan] {meta['date']}")
        self.update("\n".join(parts))


_BLOCK = " ▁▂▃▄▅▆▇█"
_BAR_ROWS = 4  # number of spectrum rows

class SpectrumPanel(Static):
    DEFAULT_CSS = """
    SpectrumPanel {
        height: 8;
        border: solid $accent;
        padding: 0 1;
    }
    """

    def render_spectrum(self, vis: np.ndarray, elapsed: float,
                        duration: float, paused: bool):
        n = len(vis)
        total_levels = _BAR_ROWS * 8   # each row = 8 sub-levels

        # Build rows bottom-to-top, then reverse for display top-to-bottom
        rows = []
        colors = ["bright_green", "green", "yellow", "red"]
        for row_idx in range(_BAR_ROWS):          # 0 = bottom
            row_str = ""
            for v in vis:
                level = v * total_levels           # 0..total_levels float
                sub = level - row_idx * 8          # portion in this row
                sub = max(0.0, min(8.0, sub))
                row_str += _BLOCK[int(sub)]
            rows.append((row_str, colors[row_idx]))

        rows.reverse()  # top row first

        status = "⏸ " if paused else "▶ "
        e_min, e_sec = int(elapsed // 60), int(elapsed % 60)
        e_str = f"{e_min}:{e_sec:02d}"

        # Indent spectrum bars to align with the progress bar's bar portion
        indent = " " * (len(status) + len(e_str) + 1)

        if duration > 0:
            d_min, d_sec = int(duration // 60), int(duration % 60)
            d_str = f"{d_min}:{d_sec:02d}"
            pct = min(elapsed / duration, 1.0)
            bar_w = n - len(e_str) - len(d_str) - len(status) - 3
            if bar_w > 0:
                filled = int(pct * bar_w)
                bar = "█" * filled + "░" * (bar_w - filled)
                prog = f"{status}[cyan]{e_str}[/cyan] {bar} [cyan]{d_str}[/cyan]"
            else:
                prog = f"{status}[cyan]{e_str}[/cyan] / [cyan]{d_str}[/cyan]"
        else:
            prog = f"{status}[cyan]{e_str}[/cyan]"

        lines = "\n".join(f"{indent}[{c}]{r}[/{c}]" for r, c in rows) + "\n\n" + prog
        self.update(lines)


# ── File browser ──────────────────────────────────────────────────────────

class AudioFileTree(DirectoryTree):
    """DirectoryTree that shows only directories and supported audio files."""

    class SelectionChanged(Message):
        def __init__(self, tree: "AudioFileTree", count: int) -> None:
            self.tree = tree
            self.count = count
            super().__init__()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_paths: set[Path] = set()

    def filter_paths(self, paths):
        return [p for p in paths
                if p.is_dir() or p.suffix.lower() in AUDIO_EXTS | STD_AUDIO_EXTS | PLAYLIST_EXTS]

    def render_label(self, node, base_style, style):
        label = super().render_label(node, base_style, style)
        if node.data and hasattr(node.data, "path") and node.data.path in self.selected_paths:
            prefix = RichText("✓ ", style="bold green")
            prefix.append_text(label)
            return prefix
        return label

    def on_key(self, event) -> None:
        if event.key == "backspace":
            parent = self.path.parent
            if parent != self.path:   # stop at filesystem root
                self.path = parent
            event.stop()             # don't collapse node
        elif event.key == "space":
            node = self.cursor_node
            if node and node.data and hasattr(node.data, "path"):
                path = node.data.path
                if path.is_file() and path.suffix.lower() in AUDIO_EXTS | STD_AUDIO_EXTS:
                    if path in self.selected_paths:
                        self.selected_paths.discard(path)
                    else:
                        self.selected_paths.add(path)
                    # Immediately repaint just this node, then the whole widget
                    node.refresh()
                    self.refresh()
                    self.post_message(self.SelectionChanged(self, len(self.selected_paths)))
                    self.action_cursor_down()
            event.stop()
        elif event.key == "escape":
            if self.selected_paths:
                self.selected_paths.clear()
                self.refresh()
                self.post_message(self.SelectionChanged(self, 0))
                event.stop()


# ── Goto directory modal ──────────────────────────────────────────────────

class GotoDirectoryScreen(ModalScreen):
    DEFAULT_CSS = """
    GotoDirectoryScreen { align: center middle; }
    GotoDirectoryScreen > Vertical {
        width: 70;
        height: auto;
        padding: 2 3;
        border: double $accent;
        background: $surface;
    }
    GotoDirectoryScreen Label { margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, current_path: str):
        super().__init__()
        self._current_path = current_path

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("디렉토리 이동 — 경로 입력 후 Enter, 취소는 Esc")
            yield Input(value=self._current_path, id="dirpath")

    def on_mount(self):
        inp = self.query_one("#dirpath", Input)
        inp.focus()
        inp.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


# ── Save playlist modal ───────────────────────────────────────────────────

class SavePlaylistScreen(ModalScreen):
    DEFAULT_CSS = """
    SavePlaylistScreen { align: center middle; }
    SavePlaylistScreen > Vertical {
        width: 60;
        height: auto;
        padding: 2 3;
        border: double $accent;
        background: $surface;
    }
    SavePlaylistScreen Label { margin-bottom: 1; }
    """

    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("재생목록을 M3U 파일로 저장\n파일명 입력 후 Enter, 취소는 Esc")
            yield Input(value="playlist.m3u", id="filename")

    def on_mount(self):
        self.query_one("#filename", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())


# ── Convert modal ─────────────────────────────────────────────────────────

CONVERT_FORMATS = ["mp3", "wav", "aac", "flac", "ogg", "opus"]


class ConvertScreen(ModalScreen):
    DEFAULT_CSS = """
    ConvertScreen { align: center middle; }
    ConvertScreen > Vertical {
        width: 70;
        height: auto;
        padding: 2 3;
        border: double $accent;
        background: $surface;
    }
    ConvertScreen Label { margin-bottom: 1; margin-top: 1; }
    ConvertScreen Select { margin-bottom: 1; }
    ConvertScreen Checkbox { margin-top: 1; }
    """

    BINDINGS = [Binding("escape", "dismiss", show=False)]

    def __init__(self, count: int, default_dir: str):
        super().__init__()
        self._count = count
        self._default = default_dir

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(f"{self._count}개 파일 변환  —  Enter로 확인, Esc 취소")
            yield Label("출력 형식:")
            yield Select(
                options=[(f.upper(), f) for f in CONVERT_FORMATS],
                value="mp3",
                id="fmt",
            )
            yield Label("출력 디렉토리:")
            yield Input(value=self._default, id="outdir")
            yield Checkbox("변환 후 재생목록에 추가", value=True, id="add-playlist")

    def on_mount(self):
        inp = self.query_one("#outdir", Input)
        inp.focus()
        inp.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        fmt_widget = self.query_one("#fmt", Select)
        fmt = fmt_widget.value if fmt_widget.value != Select.BLANK else "mp3"
        add = self.query_one("#add-playlist", Checkbox).value
        self.dismiss((event.value.strip(), fmt, add))


# ── Help modal ────────────────────────────────────────────────────────────

HELP_TEXT = """\
[bold cyan]VGM TUI Player — 키보드 단축키[/bold cyan]

[bold]재생 제어[/bold]
  [yellow]Space[/yellow]      재생 / 일시정지  (탐색기에서는 파일 선택/해제)
  [yellow]N[/yellow]          다음 곡
  [yellow]P[/yellow]          이전 곡 (3초 이내: 현재 곡 처음부터)

[bold]재생목록[/bold]
  [yellow]↑ / ↓[/yellow]     재생목록 이동
  [yellow]Enter[/yellow]      선택한 곡 재생
  [yellow]Delete[/yellow]     선택한 곡 삭제

[bold]파일 탐색기[/bold]
  [yellow]Space[/yellow]      파일 다중 선택 / 해제  (✓ 표시)
  [yellow]Esc[/yellow]        선택 전체 해제
  [yellow]A[/yellow]          선택된 파일을 재생목록에 추가
  [yellow]C[/yellow]          선택된 파일 일괄 변환 (WAV)
  [yellow]Backspace[/yellow]  상위 폴더로 이동
  [yellow]G[/yellow]          경로 직접 입력으로 이동

[bold]기타[/bold]
  [yellow]L / F[/yellow]      재생목록 ↔ 파일 탐색기 토글
  [yellow]S[/yellow]          재생목록을 M3U 파일로 저장
  [yellow]H[/yellow]          이 도움말 표시 / 닫기
  [yellow]Q[/yellow]          종료

[dim]아무 키나 누르면 닫힙니다[/dim]
"""


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Static {
        width: 52;
        padding: 2 3;
        border: double $accent;
        background: $surface;
    }
    """

    BINDINGS = [Binding("escape,h,q,space,enter", "dismiss", show=False)]

    def compose(self) -> ComposeResult:
        yield Static(HELP_TEXT)


_track_seq = 0  # monotonically increasing; never reuse IDs across clear/rebuild


def _new_track_id() -> str:
    global _track_seq
    _track_seq += 1
    return f"t{_track_seq}"


# ── Main app ──────────────────────────────────────────────────────────────

class PlayerApp(App):
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #playlist { border: solid $primary; height: 1fr; }
    #filetree { border: solid $primary; height: 1fr; }
    #selection-status { height: 1; padding: 0 1; background: $surface; }
    ListView > ListItem.--highlight { background: $accent 30%; }
    """

    BINDINGS = [
        Binding("space",     "toggle_pause",   "Play/Pause"),
        Binding("n",         "next_track",     "Next"),
        Binding("p",         "prev_track",     "Prev"),
        Binding("l",         "toggle_tab",     "목록/탐색기"),
        Binding("f",         "toggle_tab",     "목록/탐색기", show=False),
        Binding("g",         "goto_dir",       "경로이동"),
        Binding("s",         "save_playlist",  "저장"),
        Binding("a",         "add_selected",   "선택추가"),
        Binding("c",         "convert_selected", "변환"),
        Binding("delete",    "delete_track",   "삭제"),
        Binding("h",         "help",           "Help"),
        Binding("q",         "quit",           "Quit"),
    ]

    def __init__(self, playlist: list[tuple[str, Optional[str]]], binary: str,
                 loops: int = 2, fade: float = 8.0):
        super().__init__()
        self.playlist = playlist
        self.binary   = binary
        self.loops    = loops
        self.fade     = fade
        self.current  = 0
        self.engine   = AudioEngine(binary)

    # ── Layout ────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield MetadataPanel(id="meta")
        yield SpectrumPanel(id="spectrum")
        with TabbedContent(id="tabs"):
            with TabPane("재생목록", id="tab-playlist"):
                yield ListView(
                    *[ListItem(Label(title or Path(fp).name), id=_new_track_id())
                      for (fp, title) in self.playlist],
                    id="playlist",
                )
            with TabPane("파일", id="tab-files"):
                yield AudioFileTree(str(Path.cwd()), id="filetree")
                yield Label("", id="selection-status")
        yield Footer()

    def on_mount(self):
        self.set_interval(1 / 20, self._tick)
        if self.playlist:
            self._play_current()
        else:
            self.call_after_refresh(self.action_show_files)

    # ── UI update loop ────────────────────────────────────────────────────

    def _tick(self):
        vis = self.engine.get_vis()
        sp = self.query_one("#spectrum", SpectrumPanel)
        sp.render_spectrum(vis, self.engine.elapsed, self.engine.duration,
                           self.engine.is_paused)

    # ── Playback ──────────────────────────────────────────────────────────

    def _play_current(self):
        if not self.playlist:
            return
        fp, extinf_title = self.playlist[self.current]
        meta = get_metadata(fp)
        if extinf_title:
            meta["title"] = extinf_title
        self.query_one("#meta", MetadataPanel).update_meta(meta)
        self.title = meta["title"]

        lv = self.query_one("#playlist", ListView)
        lv.index = self.current

        self.engine.play(
            fp,
            loops=self.loops,
            fade=self.fade,
            on_finish=lambda: self.call_from_thread(self.action_next_track),
        )

    # ── Actions ───────────────────────────────────────────────────────────

    def action_help(self):
        self.push_screen(HelpScreen())

    def action_goto_dir(self):
        tree = self.query_one("#filetree", AudioFileTree)
        current = str(tree.path)

        def _on_goto(path_str: str | None) -> None:
            if not path_str:
                return
            p = Path(path_str)
            if p.is_dir():
                tree.path = p
                self.action_show_files()
            else:
                self.notify(f"디렉토리를 찾을 수 없습니다: {path_str}", severity="error")

        self.push_screen(GotoDirectoryScreen(current), _on_goto)

    def action_save_playlist(self):
        if not self.playlist:
            self.notify("재생목록이 비어 있습니다", severity="warning")
            return

        def _on_save(filename: str | None) -> None:
            if not filename:
                return
            path = Path(filename)
            if not path.suffix:
                path = path.with_suffix(".m3u")
            try:
                write_m3u(str(path), self.playlist)
                self.notify(f"저장됨: {path}")
            except Exception as e:
                self.notify(f"저장 실패: {e}", severity="error")

        self.push_screen(SavePlaylistScreen(), _on_save)

    def action_toggle_tab(self):
        tabs = self.query_one("#tabs", TabbedContent)
        if tabs.active == "tab-playlist":
            tabs.active = "tab-files"
            self.call_after_refresh(lambda: self.query_one("#filetree", AudioFileTree).focus())
        else:
            tabs.active = "tab-playlist"
            self.call_after_refresh(lambda: self.query_one("#playlist", ListView).focus())

    def action_show_files(self):
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = "tab-files"
        self.call_after_refresh(lambda: self.query_one("#filetree", AudioFileTree).focus())

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        ext = path.suffix.lower()
        lv = self.query_one("#playlist", ListView)

        if ext in PLAYLIST_EXTS:
            # Replace playlist with tracks from the M3U
            tracks = parse_m3u(str(path))
            if not tracks:
                self.notify("재생목록이 비어 있거나 읽을 수 없습니다", severity="warning")
                return
            self.engine.stop()
            self.playlist = list(tracks)
            self.current = 0
            self._rebuild_list(lv)
        elif ext in AUDIO_EXTS | STD_AUDIO_EXTS:
            self.current = len(self.playlist)
            self.playlist.append((str(path), None))
            lv.append(ListItem(Label(path.stem), id=_new_track_id()))
        else:
            return

        self._play_current()
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = "tab-playlist"
        self.call_after_refresh(lambda: self.query_one("#playlist", ListView).focus())

    def action_toggle_pause(self):
        self.engine.toggle_pause()

    def action_next_track(self):
        if self.current < len(self.playlist) - 1:
            self.current += 1
            self._play_current()
        else:
            self.engine.stop()

    def action_prev_track(self):
        # restart current if played > 3s, else go back
        if self.engine.elapsed > 3.0:
            self._play_current()
        elif self.current > 0:
            self.current -= 1
            self._play_current()

    def _rebuild_list(self, lv: "ListView") -> None:
        """Clear and repopulate ListView using fresh unique IDs."""
        lv.clear()
        for fp, title in self.playlist:
            lv.append(ListItem(Label(title or Path(fp).stem), id=_new_track_id()))

    def action_delete_track(self):
        if not self.playlist:
            return
        lv = self.query_one("#playlist", ListView)
        del_idx = lv.index if lv.index is not None else self.current

        was_playing_deleted = (del_idx == self.current and self.engine.is_playing)
        self.playlist.pop(del_idx)

        self._rebuild_list(lv)

        if not self.playlist:
            self.engine.stop()
            self.current = 0
            return

        if del_idx < self.current:
            self.current -= 1
        elif del_idx == self.current:
            self.current = min(self.current, len(self.playlist) - 1)

        lv.index = self.current

        if was_playing_deleted:
            self._play_current()

    def on_audio_file_tree_selection_changed(self, event: AudioFileTree.SelectionChanged):
        label = self.query_one("#selection-status", Label)
        count = event.count
        if count > 0:
            label.update(
                f"[bold green]{count}개 선택됨[/bold green]"
                "  —  [yellow]A[/yellow]: 재생목록 추가  [yellow]C[/yellow]: WAV 변환"
                "  [yellow]Esc[/yellow]: 선택 해제"
            )
        else:
            label.update("")

    def _add_files_to_playlist(self, paths: list[Path]) -> None:
        lv = self.query_one("#playlist", ListView)
        for path in paths:
            self.playlist.append((str(path), None))
            lv.append(ListItem(Label(path.stem), id=_new_track_id()))
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = "tab-playlist"
        self.call_after_refresh(lambda: self.query_one("#playlist", ListView).focus())

    def action_add_selected(self):
        tree = self.query_one("#filetree", AudioFileTree)
        selected = sorted(tree.selected_paths)
        if not selected:
            self.notify("선택된 파일이 없습니다", severity="warning")
            return
        count = len(selected)
        tree.selected_paths.clear()
        tree.refresh()
        self.query_one("#selection-status", Label).update("")
        self._add_files_to_playlist(selected)
        self.notify(f"{count}개 파일을 재생목록에 추가했습니다")

    def action_convert_selected(self):
        tree = self.query_one("#filetree", AudioFileTree)
        selected = sorted(tree.selected_paths)
        if not selected:
            self.notify("선택된 파일이 없습니다", severity="warning")
            return
        default_out = str(Path.cwd() / "output")

        def _on_convert(result: tuple[str, str, bool] | None) -> None:
            if not result:
                return
            out_dir, fmt, add_to_playlist = result
            if not out_dir:
                return
            out_path = Path(out_dir)
            try:
                out_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.notify(f"출력 디렉토리 생성 실패: {e}", severity="error")
                return

            binary = self.binary
            loops = self.loops
            fade = self.fade
            convertible = [fp for fp in selected if fp.suffix.lower() in AUDIO_EXTS]
            skipped = len(selected) - len(convertible)
            if skipped:
                self.notify(
                    f"{skipped}개 파일은 지원하지 않는 형식으로 건너뜁니다",
                    severity="warning",
                )
            if not convertible:
                return
            total = len(convertible)

            def _run():
                failed = 0
                converted: list[Path] = []
                for i, fp in enumerate(convertible, 1):
                    dest = out_path / (fp.stem + f".{fmt}")
                    self.call_from_thread(
                        self.notify, f"변환 중 ({i}/{total}): {fp.name}"
                    )
                    cmd = [binary, f"--loops={loops}", f"--fade={fade}",
                           "--format", fmt, str(fp), str(dest)]
                    proc = subprocess.run(cmd, capture_output=True)
                    if proc.returncode != 0:
                        failed += 1
                        self.call_from_thread(
                            self.notify, f"변환 실패: {fp.name}", severity="error"
                        )
                    else:
                        converted.append(dest)
                msg = f"변환 완료 ({fmt.upper()}): {total - failed}/{total}개 → {out_path}"
                self.call_from_thread(self.notify, msg)
                tree.selected_paths.clear()
                self.call_from_thread(tree.refresh)
                self.call_from_thread(
                    lambda: self.query_one("#selection-status", Label).update("")
                )
                if add_to_playlist and converted:
                    self.call_from_thread(self._add_files_to_playlist, converted)

            threading.Thread(target=_run, daemon=True).start()

        self.push_screen(ConvertScreen(len(selected), default_out), _on_convert)

    def on_list_view_selected(self, event: ListView.Selected):
        if event.list_view.index is not None:
            self.current = event.list_view.index
            self._play_current()

    def on_unmount(self):
        self.engine.stop()


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="VGM TUI Player")
    parser.add_argument("input", nargs="*",
                        help="VGM/SPC/NSF files or .m3u playlist (optional; omit to open file browser)")
    parser.add_argument("--bin", default="vgm2wav2",
                        help="path to vgm2wav2 binary (default: vgm2wav2)")
    parser.add_argument("--loops", type=int, default=2)
    parser.add_argument("--fade",  type=float, default=8.0)
    args = parser.parse_args()

    playlist: list[tuple[str, Optional[str]]] = []
    for inp in args.input:
        p = Path(inp)
        if p.suffix.lower() == ".m3u":
            playlist.extend(parse_m3u(str(p)))
        else:
            playlist.append((str(p), None))

    # resolve binary path
    binary = args.bin
    if binary == "vgm2wav2":
        exe = "vgm2wav2.exe" if IS_WINDOWS else "vgm2wav2"
        if getattr(sys, "frozen", False):
            # PyInstaller bundle: binaries are in sys._MEIPASS (_internal/)
            local = Path(sys._MEIPASS) / exe
        else:
            local = Path(__file__).parent / "build" / exe
        if local.exists():
            binary = str(local)

    PlayerApp(playlist, binary, loops=args.loops, fade=args.fade).run()


if __name__ == "__main__":
    main()
