"""Screenshot capture with explicit persistent and managed temporary lifetimes."""

from __future__ import annotations

import contextlib
import importlib
import os
import re
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jarvis.computer.errors import ComputerValidationError, ScreenshotError
from jarvis.computer.windows import WindowBounds, WindowsController

_SAFE_KIND = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ImageLike(Protocol):
    def save(self, file_path: str | Path, format: str) -> None: ...

    def close(self) -> None: ...


class ScreenshotProvider(Protocol):
    """Mockable image-capture boundary."""

    def capture(self, bbox: WindowBounds | None = None) -> ImageLike: ...


class PillowImageGrabProvider:
    """Lazily use Pillow's platform screenshot provider."""

    def __init__(self, image_grab_module: Any | None = None) -> None:
        self._image_grab = image_grab_module

    def capture(self, bbox: WindowBounds | None = None) -> ImageLike:
        if self._image_grab is None:
            try:
                self._image_grab = importlib.import_module("PIL.ImageGrab")
            except (ImportError, OSError) as exc:
                raise ScreenshotError(
                    "screenshots require the optional 'Pillow' dependency "
                    "and an interactive desktop"
                ) from exc
        coordinates = bbox.as_bbox() if bbox else None
        try:
            try:
                return self._image_grab.grab(bbox=coordinates, all_screens=True)
            except TypeError:
                return self._image_grab.grab(bbox=coordinates)
        except OSError as exc:
            raise ScreenshotError("the desktop screenshot could not be captured") from exc


@dataclass(frozen=True, slots=True)
class Screenshot:
    path: Path
    bounds: WindowBounds | None
    persistent: bool


class ScreenshotStore:
    """Store generated PNG names under one managed absolute directory."""

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.time,
        unique_id: Callable[[], Any] = uuid.uuid4,
    ) -> None:
        root = Path(root)
        if not root.is_absolute():
            raise ComputerValidationError("screenshot storage path must be absolute")
        self.root = root
        self._clock = clock
        self._unique_id = unique_id

    def save(
        self,
        provider: ScreenshotProvider,
        *,
        kind: str = "screen",
        bounds: WindowBounds | None = None,
        persistent: bool = True,
    ) -> Screenshot:
        """Capture a PNG with a generated name; callers state its lifetime."""

        if not isinstance(kind, str) or not _SAFE_KIND.fullmatch(kind):
            raise ComputerValidationError("screenshot kind must be a short safe identifier")
        try:
            root_existed = self.root.exists()
            if self.root.is_symlink():
                raise OSError("screenshot directory cannot be a symbolic link")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not self.root.is_dir():
                raise OSError("screenshot storage path is not a directory")
            if not root_existed:
                os.chmod(self.root, 0o700)
        except OSError as exc:
            raise ScreenshotError(f"could not secure screenshot directory {self.root}") from exc
        timestamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(self._clock()))
        identifier = str(self._unique_id()).replace("-", "")[:12]
        path = self.root / f"{kind}-{timestamp}-{identifier}.png"
        image: ImageLike | None = None
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            image = provider.capture(bounds)
            image.save(path, format="PNG")
            os.chmod(path, 0o600)
        except ScreenshotError:
            path.unlink(missing_ok=True)
            raise
        except (OSError, ValueError) as exc:
            path.unlink(missing_ok=True)
            raise ScreenshotError(f"could not store screenshot in {self.root}") from exc
        finally:
            if image is not None:
                image.close()
        return Screenshot(path=path, bounds=bounds, persistent=persistent)

    @contextlib.contextmanager
    def temporary(
        self,
        provider: ScreenshotProvider,
        *,
        kind: str = "screen",
        bounds: WindowBounds | None = None,
    ) -> Iterator[Screenshot]:
        """Yield a screenshot and remove it even if the consumer fails."""

        screenshot = self.save(
            provider,
            kind=f"temporary-{kind}",
            bounds=bounds,
            persistent=False,
        )
        try:
            yield screenshot
        finally:
            screenshot.path.unlink(missing_ok=True)


class ScreenController:
    """Coordinate full-screen and active-window capture lifetimes."""

    def __init__(
        self,
        store: ScreenshotStore,
        *,
        provider: ScreenshotProvider | None = None,
        windows: WindowsController | None = None,
    ) -> None:
        self._store = store
        self._provider = provider or PillowImageGrabProvider()
        self._windows = windows or WindowsController()

    def capture_screen(self) -> Screenshot:
        """Create an explicitly persistent full-screen capture."""

        return self._store.save(self._provider, kind="screen", persistent=True)

    def capture_active_window(self) -> Screenshot:
        """Create an explicitly persistent active-window capture."""

        bounds = self._active_bounds()
        return self._store.save(
            self._provider,
            kind="active-window",
            bounds=bounds,
            persistent=True,
        )

    def temporary_screen(self) -> contextlib.AbstractContextManager[Screenshot]:
        return self._store.temporary(self._provider, kind="screen")

    def temporary_active_window(self) -> contextlib.AbstractContextManager[Screenshot]:
        return self._store.temporary(
            self._provider,
            kind="active-window",
            bounds=self._active_bounds(),
        )

    def _active_bounds(self) -> WindowBounds:
        window = self._windows.active_window()
        if window.bounds is None:
            raise ScreenshotError("active-window bounds are unavailable")
        return window.bounds
