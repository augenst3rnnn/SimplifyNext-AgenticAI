"""Local OpenCV monitor for Orca ego RGB and NaVILA decisions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, TypeVar

import numpy as np

from .contracts import RenderFrame


_T = TypeVar("_T")
_FONT_SUFFIXES = frozenset({".otf", ".ttc", ".ttf"})
_SYSTEM_QT_FONT_DIRS = (
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/ubuntu"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/freefont"),
)


def _contains_font_file(directory: Path) -> bool:
    try:
        return directory.is_dir() and any(
            entry.is_file() and entry.suffix.lower() in _FONT_SUFFIXES
            for entry in directory.iterdir()
        )
    except OSError:
        return False


def _configure_qt_font_dir() -> Path | None:
    """Replace OpenCV's missing wheel-font path before Qt initializes."""

    configured = os.environ.get("QT_QPA_FONTDIR")
    if configured and _contains_font_file(Path(configured).expanduser()):
        return Path(configured).expanduser()

    override = os.environ.get("NAVILA_ORCA_QT_FONTDIR")
    candidates = (
        *((Path(override).expanduser(),) if override else ()),
        *_SYSTEM_QT_FONT_DIRS,
    )
    for candidate in candidates:
        if _contains_font_file(candidate):
            os.environ["QT_QPA_FONTDIR"] = str(candidate)
            return candidate

    # Without a valid explicit path, Qt can fall back to the host fontconfig
    # database instead of repeatedly probing cv2/qt/fonts.
    os.environ.pop("QT_QPA_FONTDIR", None)
    return None


class LiveMonitorError(RuntimeError):
    """Raised when the requested desktop monitor cannot be created."""


class LiveNavigationMonitor:
    """Display fresh ego RGB beside instruction and action diagnostics."""

    _PANEL_MARGIN_X = 20
    _TITLE_SCALE = 0.86
    _META_SCALE = 0.64
    _SECTION_SCALE = 0.72
    _BODY_SCALE = 0.68

    def __init__(
        self,
        *,
        window_name: str = "live monitor",
        panel_width: int = 560,
        cv2_module: Any | None = None,
    ) -> None:
        if panel_width < 320:
            raise ValueError("panel_width must be at least 320 pixels")
        if cv2_module is None:
            try:
                import cv2 as cv2_module
            except ImportError as exc:
                raise LiveMonitorError(
                    "OpenCV is required for --live-monitor"
                ) from exc
        if sys.platform.startswith("linux"):
            _configure_qt_font_dir()
        self.cv2 = cv2_module
        self.window_name = str(window_name)
        self.panel_width = int(panel_width)
        self._opened = False
        self._last_canvas: np.ndarray | None = None

    def update(
        self,
        frame: RenderFrame,
        *,
        instruction: str,
        vlm_output: str = "Waiting for first VLM decision...",
        command: str = "none",
        status: str = "initializing",
        decision: int = 0,
        chunk_result: str = "none completed yet",
    ) -> None:
        rgb = np.asarray(frame.rgb)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise LiveMonitorError(f"invalid ego RGB shape {rgb.shape}")
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        ego_bgr = np.ascontiguousarray(rgb[..., ::-1])
        height = int(ego_bgr.shape[0])
        sections = (
            ("STATUS", status, (130, 230, 130)),
            ("INSTRUCTION", instruction, (235, 235, 235)),
            ("VLM OUTPUT", vlm_output, (120, 210, 255)),
            ("EXECUTED CHUNK", command, (255, 190, 100)),
            ("LAST CHUNK: IDEAL VS ACTUAL", chunk_result, (180, 230, 140)),
        )
        panel_height = max(
            height,
            self._required_panel_height(sections),
        )
        panel = np.full((panel_height, self.panel_width, 3), 24, dtype=np.uint8)
        ego_panel = np.full((panel_height, ego_bgr.shape[1], 3), 24, dtype=np.uint8)
        ego_panel[:height] = ego_bgr

        y = 38
        y = self._draw_line(
            panel, "live monitor", y, (80, 220, 255), self._TITLE_SCALE
        )
        y = self._draw_line(
            panel,
            f"decision={decision}  step={frame.step_id}  sim={frame.sim_time_s:.2f}s",
            y + 7,
            (190, 190, 190),
            self._META_SCALE,
        )
        for title, value, color in sections:
            y = self._draw_section(panel, title, value, y + 12, color)

        canvas = np.concatenate((ego_panel, panel), axis=1)
        self._last_canvas = canvas
        self._show(canvas)

    def _draw_line(
        self,
        panel: np.ndarray,
        text: str,
        y: int,
        color: tuple[int, int, int],
        scale: float,
    ) -> int:
        self.cv2.putText(
            panel,
            text,
            (self._PANEL_MARGIN_X, y),
            self.cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            color,
            1,
            self.cv2.LINE_AA,
        )
        return y + self._line_height(scale)

    def _draw_section(
        self,
        panel: np.ndarray,
        title: str,
        value: str,
        y: int,
        color: tuple[int, int, int],
    ) -> int:
        y = self._draw_line(panel, title, y, color, self._SECTION_SCALE)
        for line in self._wrap_text(str(value), self._BODY_SCALE):
            y = self._draw_line(panel, line, y + 3, (225, 225, 225), self._BODY_SCALE)
        return y

    def _required_panel_height(
        self,
        sections: tuple[tuple[str, str, tuple[int, int, int]], ...],
    ) -> int:
        """Reserve enough vertical space for every wrapped diagnostic line."""

        y = 38
        y += self._line_height(self._TITLE_SCALE)
        y += 7 + self._line_height(self._META_SCALE)
        for _title, value, _color in sections:
            y += 12 + self._line_height(self._SECTION_SCALE)
            y += sum(
                3 + self._line_height(self._BODY_SCALE)
                for _line in self._wrap_text(str(value), self._BODY_SCALE)
            )
        return y + 18

    def _line_height(self, scale: float) -> int:
        """Return a readable, non-overlapping baseline-to-baseline distance."""

        get_text_size = getattr(self.cv2, "getTextSize", None)
        if callable(get_text_size):
            try:
                (_width, text_height), baseline = get_text_size(
                    "Ag",
                    self.cv2.FONT_HERSHEY_SIMPLEX,
                    scale,
                    1,
                )
                return max(18, int(text_height) + int(baseline) + 4)
            except Exception:
                pass
        return max(18, int(round(28 * max(scale, 0.6))) + 4)

    def _wrap_text(self, value: str, scale: float) -> list[str]:
        """Wrap text to the visible panel width without dropping any lines."""

        available_width = self.panel_width - (2 * self._PANEL_MARGIN_X)
        lines: list[str] = []
        for paragraph in value.splitlines() or [""]:
            words = paragraph.split()
            if not words:
                lines.append("")
                continue

            line = ""
            for word in words:
                candidate = word if not line else f"{line} {word}"
                if not line or self._text_width(candidate, scale) <= available_width:
                    line = candidate
                    continue

                lines.append(line)
                line = word
                if self._text_width(line, scale) > available_width:
                    split_lines = self._split_long_word(line, available_width, scale)
                    lines.extend(split_lines[:-1])
                    line = split_lines[-1]
            lines.append(line)
        return lines or [""]

    def _split_long_word(
        self, word: str, available_width: int, scale: float
    ) -> list[str]:
        pieces: list[str] = []
        piece = ""
        for character in word:
            candidate = f"{piece}{character}"
            if piece and self._text_width(candidate, scale) > available_width:
                pieces.append(piece)
                piece = character
            else:
                piece = candidate
        if piece or not pieces:
            pieces.append(piece)
        return pieces

    def _text_width(self, text: str, scale: float) -> int:
        get_text_size = getattr(self.cv2, "getTextSize", None)
        if callable(get_text_size):
            try:
                (width, _height), _baseline = get_text_size(
                    text,
                    self.cv2.FONT_HERSHEY_SIMPLEX,
                    scale,
                    1,
                )
                return int(width)
            except Exception:
                pass
        return int(round(len(text) * 12 * scale))

    def _show(self, canvas: np.ndarray) -> None:
        try:
            if not self._opened:
                self.cv2.namedWindow(self.window_name, self.cv2.WINDOW_NORMAL)
                self.cv2.resizeWindow(
                    self.window_name,
                    int(canvas.shape[1]),
                    int(canvas.shape[0]),
                )
                self._opened = True
            self.cv2.imshow(self.window_name, canvas)
            self.cv2.waitKey(1)
        except Exception as exc:
            raise LiveMonitorError(
                "failed to open the ego/VLM monitor window; check DISPLAY and OpenCV GUI support"
            ) from exc

    def pump(self) -> None:
        """Keep the desktop window responsive while VLM inference blocks."""

        if self._opened:
            self.cv2.waitKey(20)

    def run_while_responsive(self, operation: Callable[[], _T]) -> _T:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="navila-vlm") as pool:
            future = pool.submit(operation)
            while not future.done():
                self.pump()
                time.sleep(0.02)
            return future.result()

    def close(self) -> None:
        if not self._opened:
            return
        try:
            self.cv2.destroyWindow(self.window_name)
            self.cv2.waitKey(1)
        except Exception:
            pass
        finally:
            self._opened = False
            self._last_canvas = None
