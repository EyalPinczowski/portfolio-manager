"""Memory probe for a 512 MB host (Render free tier).

Starts the API in a subprocess (uvicorn, one worker, `SCHEDULER_IN_PROCESS=true`, a throwaway SQLite
database, Tesseract hidden from PATH and a dummy Gemini key so no OCR ever runs), drives it through the spiky
paths, and prints the process's resident set size after every stage plus the kernel's high-water
mark (`VmHWM`). Exits 1 if the peak is above the limit (default 400 MB, leaving headroom below
512 MB for the Python allocator, Postgres driver buffers and the OS).

    cd backend && .venv/bin/python scripts/memprobe.py [--limit-mb 400] [--users 8]

Linux only (reads /proc/<pid>/status). Needs `httpx`, `Pillow`, `numpy` (all app dependencies).
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import os
import re
import shutil
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"


@dataclass
class Probe:
    pid: int
    rows: list[tuple[str, float, float, str]] = field(default_factory=list)

    def read(self) -> tuple[float, float]:
        text = Path(f"/proc/{self.pid}/status").read_text()
        rss = int(re.search(r"VmRSS:\s+(\d+) kB", text).group(1)) / 1024  # type: ignore[union-attr]
        hwm = int(re.search(r"VmHWM:\s+(\d+) kB", text).group(1)) / 1024  # type: ignore[union-attr]
        return rss, hwm

    def mark(self, stage: str, note: str = "") -> None:
        rss, hwm = self.read()
        self.rows.append((stage, rss, hwm, note))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def png_screenshot(width: int = 3000, height: int = 6000) -> bytes:
    """A tall stitched screenshot: flat rows with a noisy band so the file is a few MB (< 8 MB)."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(1)
    arr = np.full((height, width, 3), 245, dtype=np.uint8)
    arr[::40, :, :] = 30  # row lines
    arr[1000:1500, :, :] = rng.integers(0, 256, (500, width, 3), dtype=np.uint8)
    out = io.BytesIO()
    Image.fromarray(arr).save(out, format="PNG", compress_level=1)
    return out.getvalue()


def png_flat(width: int, height: int) -> bytes:
    """A valid solid-colour PNG of any size, streamed through zlib (never a full raw copy)."""
    row = b"\x00" + b"\xff\xff\xff" * width
    comp = zlib.compressobj(1)
    parts = [comp.compress(row) for _ in range(height)]
    parts.append(comp.flush())
    idat = b"".join(parts)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def png_bomb(width: int = 60_000, height: int = 60_000) -> bytes:
    """Header claims 3.6 gigapixels; the pixel data is a short stream of zeros (truncated)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00" * 1_000_000, 9)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def wait_healthy(base: str, proc: subprocess.Popen[bytes], timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"the API exited early with code {proc.returncode}")
        try:
            if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise RuntimeError("the API did not become healthy in time")


def add_invites(db_path: Path, n: int) -> list[str]:
    codes = [f"probe-invite-{i}" for i in range(n)]
    now = datetime.now(UTC).replace(tzinfo=None)
    with sqlite3.connect(db_path, timeout=30) as con:
        for code in codes:
            con.execute(
                "INSERT INTO invite (code, expires_at, created_at) VALUES (?, ?, ?)",
                (code, (now + timedelta(days=1)).isoformat(sep=" "), now.isoformat(sep=" ")),
            )
    return codes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--limit-mb", type=float, default=float(os.environ.get("MEMPROBE_LIMIT_MB", 400))
    )
    ap.add_argument("--users", type=int, default=8)
    ap.add_argument("--keep-db", action="store_true")
    ap.add_argument(
        "--no-malloc-tuning",
        action="store_true",
        help="do not set MALLOC_ARENA_MAX=2 (Dockerfile.slim sets it)",
    )
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="memprobe-"))
    db_path = tmp / "probe.db"
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "ENV": "dev",
        "COOKIE_SECURE": "false",
        "SECRET_KEY": "memprobe-secret-0123456789abcdef0123456789",
        "DATABASE_URL": f"sqlite:///{db_path}",
        "AUTO_MIGRATE": "true",
        "SCHEDULER_IN_PROCESS": "true",
        "SCHEDULER_LOCK_PATH": str(tmp / "leader.lock"),
        # A dummy key selects the third-party provider: the server decodes and redacts the image (the
        # memory-heavy part), then refuses to send it because redaction needs Tesseract (hidden
        # below), so nothing is ever sent anywhere. This is "OCR mocked out".
        "GEMINI_API_KEY": "memprobe-dummy-key",
        "TELEGRAM_BOT_TOKEN": "",
        # Hide any tesseract binary (the slim image has none).
        "PATH": f"{Path(sys.executable).parent}{os.pathsep}/nonexistent",
        "PYTHONUNBUFFERED": "1",
    }
    if not args.no_malloc_tuning:
        env["MALLOC_ARENA_MAX"] = "2"  # same as Dockerfile.slim
    cmd = [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log",
    ]  # fmt: skip
    log = (tmp / "server.log").open("wb")
    proc = subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    ok = True
    probe = Probe(proc.pid)
    try:
        wait_healthy(base, proc)
        probe.mark("1. started, idle", "interpreter + app + scheduler thread")
        time.sleep(3)  # let the startup catch-up / quote priming finish
        probe.mark("2. after scheduler start-up jobs")

        codes = add_invites(db_path, args.users)

        def signup(i: int) -> httpx.Client:
            c = httpx.Client(base_url=base, timeout=120)
            r = c.post(
                "/api/auth/signup",
                json={
                    "invite_code": codes[i],
                    "email": f"probe{i}@example.com",
                    "password": PASSWORD,
                    "accept_disclaimer": True,
                    "locale": "en",
                },
            )
            assert r.status_code == 201, (r.status_code, r.text)
            c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
            return c

        with cf.ThreadPoolExecutor(args.users) as pool:
            clients = list(pool.map(signup, range(args.users)))
        probe.mark(f"3. {args.users} concurrent sign-ups", "argon2 hashing, semaphore-limited")

        def login(i: int) -> int:
            with httpx.Client(base_url=base, timeout=120) as c:
                return c.post(
                    "/api/auth/login", json={"email": f"probe{i}@example.com", "password": PASSWORD}
                ).status_code

        with cf.ThreadPoolExecutor(args.users) as pool:
            codes_ = list(pool.map(login, range(args.users)))
        assert codes_ == [200] * args.users, codes_
        probe.mark(f"4. {args.users} concurrent log-ins", "argon2 verify")

        c0 = clients[0]
        pid = c0.post("/api/portfolios", json={"name": "Probe", "base_currency": "ILS"}).json()[
            "id"
        ]
        assert c0.post("/api/auth/consent/ocr").status_code == 200
        big = png_screenshot()
        r = c0.post(
            f"/api/portfolios/{pid}/imports", content=big, headers={"Content-Type": "image/png"}
        )
        probe.mark(
            "5. 3000x6000 screenshot upload",
            f"{len(big) / 1e6:.1f} MB body -> HTTP {r.status_code}",
        )
        del big

        rows = [
            {"name": "Apple", "symbol": "AAPL", "quantity": 10, "price": 190, "value": 1900,
             "currency": "USD"},
            {"name": "טבע", "quantity": 1000, "price": 6500, "value": 65000, "currency": "ILS",
             "unit": "agorot"},
            {"name": "Microsoft", "symbol": "MSFT", "quantity": 5, "price": 420, "value": 2100,
             "currency": "USD"},
        ]  # fmt: skip
        r = c0.post(f"/api/portfolios/{pid}/imports/rows", json={"rows": rows})
        assert r.status_code == 201, r.text
        draft = r.json()["id"]
        r = c0.post(f"/api/imports/{draft}/confirm")
        probe.mark("6. 3 rows posted + confirmed", f"HTTP {r.status_code}")

        t0 = time.time()
        r = c0.get(f"/api/portfolios/{pid}/summary")
        probe.mark(
            "7. portfolio summary",
            f"HTTP {r.status_code} in {time.time() - t0:.1f}s (quote provider, pandas)",
        )

        bomb = png_bomb()
        r = c0.post(
            f"/api/portfolios/{pid}/imports", content=bomb, headers={"Content-Type": "image/png"}
        )
        probe.mark("8. pixel-bomb PNG (60000x60000 header)", f"HTTP {r.status_code}")

        legal = png_flat(5000, 4999)  # just under the 25 MP cap: the legal worst case
        r = c0.post(
            f"/api/portfolios/{pid}/imports", content=legal, headers={"Content-Type": "image/png"}
        )
        probe.mark("9. 25 MP image just under the cap", f"HTTP {r.status_code}")

        time.sleep(2)
        probe.mark("10. settled")
        for c in clients:
            c.close()
    except Exception as exc:
        ok = False
        print(f"probe failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        try:
            _, hwm = probe.read()
        except OSError:
            hwm = float("nan")
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        if not ok:
            print((tmp / "server.log").read_text()[-3000:], file=sys.stderr)
        if not args.keep_db:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"{'stage':<42} {'RSS MB':>8} {'VmHWM MB':>9}  note")
    for stage, rss, peak, note in probe.rows:
        print(f"{stage:<42} {rss:>8.0f} {peak:>9.0f}  {note}")
    print(f"\npeak RSS (VmHWM): {hwm:.0f} MB, limit {args.limit_mb:.0f} MB")
    if not ok:
        return 2
    if hwm > args.limit_mb:
        print("FAIL: peak memory is above the limit", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
