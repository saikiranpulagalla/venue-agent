from __future__ import annotations

from pathlib import Path
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time


MAX_RENDERED_PDF_BYTES = 96 * 1024 * 1024
MAX_CONCURRENT_RENDERS = max(1, int(os.getenv("VENUE_MAX_CONCURRENT_RENDERS", "2")))
RENDER_QUEUE_TIMEOUT_S = max(0.1, float(os.getenv("VENUE_RENDER_QUEUE_TIMEOUT_SECONDS", "8")))
_RENDER_SEMAPHORE = threading.BoundedSemaphore(MAX_CONCURRENT_RENDERS)


class RenderError(RuntimeError):
    pass


def libreoffice_version() -> str | None:
    exe = shutil.which("libreoffice") or shutil.which("soffice")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or out.stderr.strip()
    except Exception:
        return None


def _terminate_process_tree(proc: subprocess.Popen, *, grace_s: float = 1.0) -> None:
    """Terminate the whole renderer process group, not only its launcher."""
    if proc.poll() is not None:
        return
    if os.name == "posix":
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass
        deadline = time.monotonic() + grace_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
    else:
        try:
            proc.terminate()
            proc.wait(timeout=grace_s)
        except Exception:
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def render_pptx_to_pdf(source: Path, out_dir: Path, timeout_s: int = 30) -> Path:
    exe = shutil.which("libreoffice") or shutil.which("soffice")
    if not exe:
        raise RenderError("LibreOffice not available")
    if not _RENDER_SEMAPHORE.acquire(timeout=RENDER_QUEUE_TIMEOUT_S):
        raise RenderError("Renderer capacity is busy; retry later")
    try:
        _secure_dir(out_dir)
        pdf = out_dir / f"{source.stem}.pdf"
        pdf.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
            cmd = [
                exe,
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(out_dir),
                str(source),
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    # LibreOffice diagnostics are not presentation evidence and
                    # an untrusted input must not be able to grow Python memory
                    # through an unbounded captured renderer log.
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    start_new_session=(os.name == "posix"),
                )
            except OSError as e:
                raise RenderError(f"LibreOffice could not start: {e}") from e
            try:
                stdout, stderr = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired as e:
                _terminate_process_tree(proc)
                raise RenderError("LibreOffice render timeout") from e
        if proc.returncode != 0 or not pdf.exists():
            raise RenderError("LibreOffice render failed")
        try:
            rendered_size = pdf.stat().st_size
        except OSError as e:
            raise RenderError("Rendered PDF could not be inspected") from e
        if rendered_size <= 0 or rendered_size > MAX_RENDERED_PDF_BYTES:
            pdf.unlink(missing_ok=True)
            raise RenderError("Rendered PDF size outside processing limits")
        try:
            os.chmod(pdf, 0o600)
        except OSError:
            pass
        return pdf
    finally:
        _RENDER_SEMAPHORE.release()
