"""
G-Mini Agent - Vision Engine.
Screen capture + OCR + visual analysis with production-oriented retries.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import ctypes.wintypes
import io
import platform
import time
from typing import Any

from loguru import logger

try:
    import mss
    import mss.tools

    HAS_MSS = True
except ImportError:
    HAS_MSS = False
    logger.warning("mss no disponible - capturas de pantalla deshabilitadas")

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    logger.warning("Pillow no disponible - procesamiento de imagen limitado")

from backend.config import config
from backend.core.resilience import OCRExecutionError, RetryPolicy, VisionCaptureError, VisionHealthSnapshot

# Establecer DPI awareness al inicio para que mss y pyautogui usen coordenadas consistentes
if platform.system() == "Windows":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _get_logical_screen_size() -> tuple[int, int]:
    """
    Gets the logical screen size used by desktop automation libraries.
    Must match the coordinate system that pyautogui uses for clicks.
    """
    try:
        import pyautogui

        return pyautogui.size()
    except Exception:
        pass

    if platform.system() == "Windows":
        try:
            user32 = ctypes.windll.user32
            width = user32.GetSystemMetrics(0)
            height = user32.GetSystemMetrics(1)
            if width > 0 and height > 0:
                return width, height
        except Exception:
            pass

    return 0, 0


class VisionEngine:
    """
    Vision subsystem with resilient capture/OCR flows.
    """

    def __init__(self):
        self._ocr_engine = None
        self._ocr_type: str = "none"
        self._initialized = False
        self._last_screenshot: bytes | None = None
        self._last_capture_time: float = 0.0
        self._capture_lock = asyncio.Lock()
        self._health = VisionHealthSnapshot()

    async def initialize(self) -> None:
        """Initializes the preferred OCR engine with graceful fallback."""
        ocr_preference = config.get("vision", "ocr_engine", default="tesseract")
        self._ocr_engine = None
        self._ocr_type = "none"

        for engine in dict.fromkeys([ocr_preference, "tesseract", "easyocr", "paddleocr"]):
            if await self._try_init_ocr(str(engine)):
                break

        self._initialized = True
        self._health.initialized = True
        self._health.ocr_type = self._ocr_type
        self._health.degraded = self._ocr_type == "none"
        logger.info(f"VisionEngine inicializado (OCR: {self._ocr_type})")

    async def _try_init_ocr(self, engine: str) -> bool:
        """Attempts to initialize a specific OCR engine."""
        try:
            if engine == "tesseract":
                import pytesseract

                pytesseract.get_tesseract_version()
                self._ocr_engine = pytesseract
                self._ocr_type = "tesseract"
                logger.info("OCR: Tesseract inicializado")
                return True

            if engine == "easyocr":
                import easyocr

                self._ocr_engine = easyocr.Reader(
                    ["es", "en"],
                    gpu=config.get("vision", "ocr_gpu", default=False),
                )
                self._ocr_type = "easyocr"
                logger.info("OCR: EasyOCR inicializado")
                return True

            if engine == "paddleocr":
                from paddleocr import PaddleOCR

                self._ocr_engine = PaddleOCR(
                    use_angle_cls=True,
                    lang="es",
                    show_log=False,
                )
                self._ocr_type = "paddleocr"
                logger.info("OCR: PaddleOCR inicializado")
                return True
        except Exception as exc:
            logger.debug(f"OCR {engine} no disponible: {exc}")
            return False

        return False

    def _retry_policy(self, kind: str) -> RetryPolicy:
        max_attempts = config.get(
            "vision",
            "capture_retry_attempts" if kind == "capture" else "ocr_retry_attempts",
            default=3 if kind == "capture" else 2,
        )
        return RetryPolicy(
            max_attempts=int(max_attempts),
            initial_delay_ms=int(config.get("vision", "retry_initial_delay_ms", default=250)),
            backoff_multiplier=float(config.get("vision", "retry_backoff_multiplier", default=2.0)),
            max_delay_ms=int(config.get("vision", "retry_max_delay_ms", default=1500)),
        )

    def _mark_failure(self, kind: str, message: str) -> None:
        if kind == "capture":
            self._health.capture_failures += 1
        else:
            self._health.ocr_failures += 1
        self._health.last_error = message
        self._health.degraded = True

    def _mark_recovered(self) -> None:
        self._health.last_error = ""
        self._health.degraded = False
        self._health.ocr_type = self._ocr_type

    async def _recover_ocr_engine(self) -> None:
        logger.warning("Intentando recuperar OCR tras un fallo transitorio")
        self._health.recoveries += 1
        self._health.last_recovery_ts = time.time()
        await self.initialize()

    def list_monitors(self) -> list[dict]:
        if not HAS_MSS:
            return []
        with mss.mss() as sct:
            result = []
            for i, mon in enumerate(sct.monitors):
                if i == 0:
                    continue
                result.append({
                    "index": i,
                    "x": mon["left"],
                    "y": mon["top"],
                    "width": mon["width"],
                    "height": mon["height"],
                    "primary": i == 1,
                })
            return result

    @staticmethod
    def _draw_cursor_on_image(img: "Image.Image", monitor_left: int = 0, monitor_top: int = 0) -> "Image.Image":
        """Draws the mouse cursor position onto the screenshot as a visible circle+crosshair.
        mss does not capture the OS cursor, so we composite it manually."""
        if platform.system() != "Windows":
            return img
        try:
            from PIL import ImageDraw
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            cx = point.x - monitor_left
            cy = point.y - monitor_top
            if 0 <= cx < img.width and 0 <= cy < img.height:
                draw = ImageDraw.Draw(img)
                r = 8
                draw.ellipse(
                    [(cx - r, cy - r), (cx + r, cy + r)],
                    outline=(255, 80, 80, 200), width=2,
                )
                cr = 3
                draw.ellipse(
                    [(cx - cr, cy - cr), (cx + cr, cy + cr)],
                    fill=(255, 60, 60, 220),
                )
                line_len = 14
                draw.line([(cx - line_len, cy), (cx + line_len, cy)], fill=(255, 80, 80, 160), width=1)
                draw.line([(cx, cy - line_len), (cx, cy + line_len)], fill=(255, 80, 80, 160), width=1)
        except Exception as exc:
            logger.debug(f"No se pudo dibujar cursor en captura: {exc}")
        return img

    def _capture_screen_once(
        self,
        monitor: int = 0,
        region: dict[str, int] | None = None,
    ) -> bytes:
        if not HAS_MSS:
            raise VisionCaptureError("mss no disponible")

        start = time.perf_counter()
        with mss.mss() as sct:
            if region:
                mon = region
            elif monitor == 0:
                mon = sct.monitors[0]
            else:
                mon = sct.monitors[min(monitor, len(sct.monitors) - 1)]

            screenshot = sct.grab(mon)

            # Composite mouse cursor onto the screenshot (mss doesn't capture OS cursor)
            if HAS_PIL:
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                img = self._draw_cursor_on_image(img, mon.get("left", 0), mon.get("top", 0))
                buf = io.BytesIO()
                img.save(buf, format="PNG", optimize=True)
                png_bytes = buf.getvalue()
            else:
                png_bytes = mss.tools.to_png(screenshot.rgb, screenshot.size)

        elapsed = (time.perf_counter() - start) * 1000
        self._last_screenshot = png_bytes
        self._last_capture_time = time.time()
        self._health.last_capture_ts = self._last_capture_time
        logger.debug(f"Captura: {screenshot.size[0]}x{screenshot.size[1]} en {elapsed:.1f}ms")
        return png_bytes

    async def _capture_screen_with_retry(
        self,
        monitor: int = 0,
        region: dict[str, int] | None = None,
    ) -> bytes:
        policy = self._retry_policy("capture")
        last_error = "captura no disponible"

        async with self._capture_lock:
            for attempt in range(1, policy.max_attempts + 1):
                try:
                    png_bytes = await asyncio.to_thread(self._capture_screen_once, monitor, region)
                    self._health.capture_failures = 0
                    self._mark_recovered()
                    return png_bytes
                except Exception as exc:
                    last_error = str(exc)
                    self._mark_failure("capture", last_error)
                    if attempt >= policy.max_attempts:
                        break
                    delay = policy.delay_seconds(attempt)
                    logger.warning(
                        f"Captura falló (intento {attempt}/{policy.max_attempts}): {last_error}. "
                        f"Reintentando en {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)

        raise VisionCaptureError(last_error)

    def capture_screen(
        self,
        monitor: int = 0,
        region: dict[str, int] | None = None,
    ) -> bytes | None:
        """
        Performs a single screen capture.
        """
        try:
            return self._capture_screen_once(monitor=monitor, region=region)
        except Exception as exc:
            logger.error(f"Error capturando pantalla: {exc}")
            self._mark_failure("capture", str(exc))
            return None

    def _encode_capture(
        self,
        png_bytes: bytes,
        max_width: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        physical_w = 0
        physical_h = 0
        sent_w = 0
        sent_h = 0

        if HAS_PIL:
            try:
                img = Image.open(io.BytesIO(png_bytes))
                physical_w, physical_h = img.width, img.height
                sent_w, sent_h = img.width, img.height
            except Exception as exc:
                logger.debug(f"No se pudieron leer dimensiones físicas del screenshot: {exc}")

        logical_w, logical_h = _get_logical_screen_size()
        if logical_w <= 0 or logical_h <= 0:
            logical_w, logical_h = physical_w, physical_h

        dpi_scale_x = (physical_w / logical_w) if logical_w > 0 else 1.0
        dpi_scale_y = (physical_h / logical_h) if logical_h > 0 else 1.0

        encoded_bytes = png_bytes
        if max_width and HAS_PIL and physical_w > 0:
            try:
                img = Image.open(io.BytesIO(png_bytes))
                if img.width > max_width:
                    ratio = max_width / img.width
                    new_size = (max_width, int(img.height * ratio))
                    resampling = getattr(Image, "Resampling", Image).LANCZOS
                    img = img.resize(new_size, resampling)
                    sent_w, sent_h = new_size
                    buffer = io.BytesIO()
                    img.save(buffer, format="PNG", optimize=True)
                    encoded_bytes = buffer.getvalue()
                else:
                    sent_w, sent_h = img.width, img.height
            except Exception as exc:
                logger.warning(f"Error redimensionando captura: {exc}")

        dims = {
            "physical_w": physical_w,
            "physical_h": physical_h,
            "logical_w": logical_w,
            "logical_h": logical_h,
            "sent_w": sent_w,
            "sent_h": sent_h,
            "dpi_scale_x": round(dpi_scale_x, 4),
            "dpi_scale_y": round(dpi_scale_y, 4),
        }

        if dpi_scale_x != 1.0 or dpi_scale_y != 1.0:
            logger.debug(
                f"DPI scaling detectado: {dpi_scale_x:.2f}x{dpi_scale_y:.2f} "
                f"(physical={physical_w}x{physical_h}, logical={logical_w}x{logical_h})"
            )

        return base64.b64encode(encoded_bytes).decode("utf-8"), dims

    def capture_to_base64(
        self,
        monitor: int = 0,
        region: dict[str, int] | None = None,
        max_width: int | None = None,
        quality: int = 85,
    ) -> tuple[str, dict[str, Any]] | None:
        """
        Performs a single capture and returns the encoded image + dimension metadata.
        """
        del quality
        png_bytes = self.capture_screen(monitor=monitor, region=region)
        if not png_bytes:
            return None
        return self._encode_capture(png_bytes, max_width=max_width)

    async def _capture_to_base64_with_retry(
        self,
        monitor: int = 0,
        region: dict[str, int] | None = None,
        max_width: int | None = None,
        quality: int = 85,
    ) -> tuple[str, dict[str, Any]] | None:
        del quality
        png_bytes = await self._capture_screen_with_retry(monitor=monitor, region=region)
        return await asyncio.to_thread(self._encode_capture, png_bytes, max_width)

    async def extract_text(
        self,
        image_bytes: bytes | None = None,
        monitor: int = 0,
    ) -> str:
        """
        Extracts OCR text from an image, retrying and attempting OCR recovery.
        """
        if image_bytes is None:
            try:
                image_bytes = await self._capture_screen_with_retry(monitor=monitor)
            except VisionCaptureError as exc:
                logger.error(f"Error capturando pantalla para OCR: {exc}")
                return ""

        if self._ocr_type == "none":
            logger.warning("No hay motor OCR disponible")
            return ""

        policy = self._retry_policy("ocr")
        last_error = "OCR sin resultado"
        for attempt in range(1, policy.max_attempts + 1):
            try:
                loop = asyncio.get_running_loop()
                text = await loop.run_in_executor(None, self._run_ocr, image_bytes)
                self._health.ocr_failures = 0
                self._mark_recovered()
                return text
            except Exception as exc:
                last_error = str(exc)
                self._mark_failure("ocr", last_error)
                logger.error(f"Error OCR (intento {attempt}/{policy.max_attempts}): {exc}")
                if attempt == 1:
                    try:
                        await self._recover_ocr_engine()
                    except Exception as recovery_exc:
                        logger.warning(f"No se pudo recuperar OCR: {recovery_exc}")
                if attempt >= policy.max_attempts:
                    break
                await asyncio.sleep(policy.delay_seconds(attempt))

        raise OCRExecutionError(last_error)

    def _run_ocr(self, image_bytes: bytes) -> str:
        """Runs OCR synchronously in a worker thread."""
        if not HAS_PIL:
            return ""

        img = Image.open(io.BytesIO(image_bytes))

        if self._ocr_type == "tesseract":
            return self._ocr_engine.image_to_string(img, lang="spa+eng").strip()

        if self._ocr_type == "easyocr":
            import numpy as np

            img_np = np.array(img)
            results = self._ocr_engine.readtext(img_np)
            return "\n".join(item[1] for item in results)

        if self._ocr_type == "paddleocr":
            import numpy as np

            img_np = np.array(img)
            results = self._ocr_engine.ocr(img_np, cls=True)
            if results and results[0]:
                return "\n".join(line[1][0] for line in results[0])
            return ""

        return ""

    async def analyze_screen(
        self,
        mode: str | None = None,
        monitor: int = 0,
    ) -> dict[str, Any]:
        """
        Full screen analysis for token-saver / computer-use workflows.
        """
        if mode is None:
            mode = config.get("vision", "mode", default="computer_use")

        result: dict[str, Any] = {
            "mode": mode,
            "timestamp": time.time(),
            "health": self.get_health(),
        }

        if mode in ("token_saver", "hybrid"):
            try:
                text = await self.extract_text(monitor=monitor)
            except OCRExecutionError as exc:
                logger.error(f"OCR degradado durante analyze_screen: {exc}")
                text = ""
            result["text"] = text
            result["text_tokens_estimate"] = len(text) // 4

        if mode in ("computer_use", "hybrid"):
            max_width_cfg = config.get("vision", "max_screenshot_width", default=1920)
            max_width = int(max_width_cfg) if int(max_width_cfg) > 0 else None
            try:
                capture_result = await self._capture_to_base64_with_retry(
                    monitor=monitor,
                    max_width=max_width,
                )
            except VisionCaptureError as exc:
                logger.error(f"Captura degradada durante analyze_screen: {exc}")
                capture_result = None

            if capture_result:
                image_b64, dims = capture_result
                result["image_base64"] = image_b64
                result["image_tokens_estimate"] = 1000
                result["screen_dimensions"] = dims
            else:
                result["image_base64"] = None
                result["screen_dimensions"] = {
                    "physical_w": 0,
                    "physical_h": 0,
                    "logical_w": 0,
                    "logical_h": 0,
                    "sent_w": 0,
                    "sent_h": 0,
                    "dpi_scale_x": 1.0,
                    "dpi_scale_y": 1.0,
                }

        result["health"] = self.get_health()
        return result

    def get_last_screenshot_base64(self) -> str | None:
        """Returns the last successful screenshot as base64."""
        if self._last_screenshot:
            return base64.b64encode(self._last_screenshot).decode("utf-8")
        return None

    def get_health(self) -> dict[str, Any]:
        return self._health.model_dump()
