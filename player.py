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
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import (
    DirectoryTree, Footer, Header, Input, Label, ListItem, ListView,
    Static, TabbedContent, TabPane,
)

# ── Constants ─────────────────────────────────────────────────────────────

AUDIO_EXTS = {
    '.vgm', '.vgz', '.s98', '.dro', '.gym',
    '.nsf', '.nsfe', '.spc', '.gbs', '.ay', '.hes', '.kss', '.sap',
}

SAMPLE_RATE  = 44100
CHANNELS     = 2
CHUNK_FRAMES = 2048
VIS_BINS     = 60           # spectrum bars
MAX_BUF_FRAMES = SAMPLE_RATE * 3  # 3-second backpressure limit

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
        self._buf = np.zeros(0, dtype=np.int16)
        self._buf_lock = threading.Lock()
        self._running = False
        self._paused = False
        self._elapsed = 0.0
        self._duration = 0.0
        self.vis_data = np.zeros(VIS_BINS)
        self._vis_lock = threading.Lock()
        self._on_finish: Optional[callable] = None

    def play(self, filepath: str, loops: int = 2, fade: float = 8.0,
             on_finish=None):
        self.stop()
        self._running = True
        self._paused = False
        self._elapsed = 0.0
        self._duration = 0.0
        self._on_finish = on_finish
        self._buf = np.zeros(0, dtype=np.int16)

        cmd = [self._bin, "--stdout", "--bps", "16",
               f"--loops={loops}", f"--fade={fade}", filepath]
        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

        # Parse duration from first stderr line:  "filename  [12.3s]"
        def _stderr_reader():
            for raw in self._proc.stderr:
                line = raw.decode("utf-8", errors="replace")
                m = re.search(r'\[(\d+\.?\d*)s\]', line)
                if m:
                    self._duration = float(m.group(1))
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
            # backpressure: don't get too far ahead of playback
            with self._buf_lock:
                buf_frames = len(self._buf) // CHANNELS
            if buf_frames > MAX_BUF_FRAMES:
                threading.Event().wait(0.05)
                continue
            data = self._proc.stdout.read(chunk_bytes)
            if not data:
                break
            samples = np.frombuffer(data, dtype=np.int16)
            with self._buf_lock:
                self._buf = np.concatenate([self._buf, samples])
        self._running = False
        if self._on_finish:
            self._on_finish()

    def _audio_cb(self, outdata, frames, time_info, status):
        need = frames * CHANNELS
        with self._buf_lock:
            avail = len(self._buf)
            if avail >= need:
                chunk = self._buf[:need].copy()
                self._buf = self._buf[need:]
            else:
                chunk = np.zeros(need, dtype=np.int16)
                chunk[:avail] = self._buf
                self._buf = np.zeros(0, dtype=np.int16)

        if not self._paused:
            self._elapsed += frames / SAMPLE_RATE

        # Spectrum via FFT on left channel
        mono = chunk[::2].astype(np.float32) / 32768.0
        if len(mono) >= CHUNK_FRAMES:
            windowed = mono[:CHUNK_FRAMES] * np.hanning(CHUNK_FRAMES)
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

        outdata[:] = chunk.reshape(frames, CHANNELS)

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

    def filter_paths(self, paths):
        return [p for p in paths if p.is_dir() or p.suffix.lower() in AUDIO_EXTS]


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


# ── Help modal ────────────────────────────────────────────────────────────

HELP_TEXT = """\
[bold cyan]VGM TUI Player — 키보드 단축키[/bold cyan]

[bold]재생 제어[/bold]
  [yellow]Space[/yellow]      재생 / 일시정지
  [yellow]N[/yellow]          다음 곡
  [yellow]P[/yellow]          이전 곡 (3초 이내: 현재 곡 처음부터)

[bold]재생목록[/bold]
  [yellow]↑ / ↓[/yellow]     재생목록 이동
  [yellow]Enter[/yellow]      선택한 곡 재생

[bold]탭 전환[/bold]
  [yellow]L[/yellow]          재생목록
  [yellow]F[/yellow]          파일 탐색기 (방향키로 탐색, Enter로 재생)
  [yellow]S[/yellow]          재생목록을 M3U 파일로 저장

[bold]기타[/bold]
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


# ── Main app ──────────────────────────────────────────────────────────────

class PlayerApp(App):
    CSS = """
    Screen { layout: vertical; }
    TabbedContent { height: 1fr; }
    TabPane { height: 1fr; padding: 0; }
    #playlist { border: solid $primary; height: 1fr; }
    #filetree { border: solid $primary; height: 1fr; }
    ListView > ListItem.--highlight { background: $accent 30%; }
    """

    BINDINGS = [
        Binding("space",     "toggle_pause",   "Play/Pause"),
        Binding("n",         "next_track",     "Next"),
        Binding("p",         "prev_track",     "Prev"),
        Binding("l",         "show_playlist",  "재생목록"),
        Binding("f",         "show_files",     "파일탐색기"),
        Binding("s",         "save_playlist",  "저장"),
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
                    *[ListItem(Label(title or Path(fp).name), id=f"track_{i}")
                      for i, (fp, title) in enumerate(self.playlist)],
                    id="playlist",
                )
            with TabPane("파일", id="tab-files"):
                yield AudioFileTree(str(Path.cwd()), id="filetree")
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

    def action_show_playlist(self):
        self.query_one("#tabs", TabbedContent).active = "tab-playlist"
        self.call_after_refresh(lambda: self.query_one("#playlist", ListView).focus())

    def action_show_files(self):
        self.query_one("#tabs", TabbedContent).active = "tab-files"
        self.call_after_refresh(lambda: self.query_one("#filetree", AudioFileTree).focus())

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        path = event.path
        if path.suffix.lower() not in AUDIO_EXTS:
            return
        title = path.stem
        self.playlist.append((str(path), None))
        idx = len(self.playlist) - 1
        lv = self.query_one("#playlist", ListView)
        lv.append(ListItem(Label(title), id=f"track_{idx}"))
        self.current = idx
        self._play_current()
        self.action_show_playlist()

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

    def on_list_view_selected(self, event: ListView.Selected):
        # Use item id ("track_N") for reliable index resolution
        item_id = event.item.id
        if item_id and item_id.startswith("track_"):
            self.current = int(item_id[6:])
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
