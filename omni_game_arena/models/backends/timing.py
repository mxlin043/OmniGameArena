"""Socket-level timed HTTP POST.

Captures per-stage timing so we can decompose wall_clock into network vs
server-side processing. Each call uses a fresh TCP+TLS connection (no
keep-alive) so every request carries its own RTT measurement, removing
the need for separate calibration probes.

The decomposition:

    wall_ms = tcp_ms + tls_ms + send_ms + ttfb_ms + download_ms
              +------ pure network ------+            + network +
                                          + server + 1 RTT -+

    pure_inference_ms ~= ttfb_ms - tcp_ms        (TCP handshake ~= 1 RTT)
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse


@dataclass
class CallLatency:
    """Per-call HTTP timing breakdown + provider-side fields."""

    # -- HTTP-layer timing (always populated on a successful call) --
    wall_ms: float = 0.0
    tcp_ms: float = 0.0           # TCP handshake ~= 1 full RTT
    tls_ms: float = 0.0           # TLS handshake 1-2 RTT (depending on version)
    send_ms: float = 0.0          # Request body upload (large for image prompts)
    ttfb_ms: float = 0.0          # last-byte-sent -> first-byte-received
    download_ms: float = 0.0      # rest of response body after first byte

    # -- Provider-side fields (populated by backend after parsing body) --
    server_latency_ms: Optional[float] = None  # engine_ttlt / Bedrock invocation
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None

    # -- Bookkeeping --
    status_code: Optional[int] = None
    error: Optional[str] = None

    @property
    def pure_inference_ms(self) -> float:
        """Estimated server-side processing time, network subtracted.

        TTFB = last_byte_up (~=0.5 RTT) + server_processing + first_byte_down (~=0.5 RTT)
             = 1 RTT + server_processing

        So pure_inference ~= ttfb_ms - 1 RTT, and tcp_ms (TCP 3-way handshake)
        ~= 1 RTT. Returns 0 (not negative) if the math goes south on a noisy
        measurement.
        """
        if self.tcp_ms <= 0:
            # Connection was reused or TCP timing missing - fall back to
            # server-reported latency if present, else give up and return
            # raw TTFB (will overestimate by 1 RTT).
            if self.server_latency_ms is not None:
                return self.server_latency_ms
            return max(0.0, self.ttfb_ms)
        return max(0.0, self.ttfb_ms - self.tcp_ms)


def timed_post(
    url: str,
    headers: dict[str, str],
    body: bytes,
    *,
    timeout: float = 120.0,
) -> tuple[int, dict[str, str], bytes, CallLatency]:
    """POST ``body`` to ``url``, returning (status, headers, body, latency).

    Implementation notes:
      - Uses raw socket + manual HTTP/1.1 to get per-stage timing.
      - Always sets ``Connection: close`` so every call carries fresh TCP/TLS
        timing (used to estimate RTT). Cost: ~50-200 ms extra latency vs
        keep-alive, paid in exchange for self-calibrating network estimates.
      - Handles ``Content-Length`` and ``Transfer-Encoding: chunked`` response
        bodies. No gzip/deflate (we don't advertise Accept-Encoding).
      - On network errors, raises the underlying exception; the partial
        ``CallLatency`` is attached as ``exc._call_latency`` for callers
        that want to log it.
    """
    parsed = urlparse(url)
    host = parsed.hostname
    if host is None:
        raise ValueError(f"URL missing host: {url!r}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    latency = CallLatency()
    t0 = time.perf_counter()

    # -- TCP connect ----------------------------------------------------
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except Exception as exc:
        latency.error = f"tcp_connect: {exc!r}"
        latency.wall_ms = (time.perf_counter() - t0) * 1000
        exc._call_latency = latency  # type: ignore[attr-defined]
        raise
    t_tcp = time.perf_counter()
    latency.tcp_ms = (t_tcp - t0) * 1000

    # -- TLS handshake --------------------------------------------------
    if parsed.scheme == "https":
        ctx = ssl.create_default_context()
        try:
            sock = ctx.wrap_socket(sock, server_hostname=host)
        except Exception as exc:
            latency.error = f"tls_handshake: {exc!r}"
            latency.wall_ms = (time.perf_counter() - t0) * 1000
            sock.close()
            exc._call_latency = latency  # type: ignore[attr-defined]
            raise
        t_tls = time.perf_counter()
        latency.tls_ms = (t_tls - t_tcp) * 1000
    else:
        t_tls = t_tcp

    # -- Build & send HTTP/1.1 request ---------------------------------
    # Force Connection: close so the server doesn't keep the socket
    # half-open (and our per-call TCP/TLS timing stays meaningful).
    merged_headers = {k: v for k, v in headers.items()
                      if k.lower() not in ("host", "connection", "content-length")}
    merged_headers["Host"] = host
    merged_headers["Connection"] = "close"
    merged_headers["Content-Length"] = str(len(body))

    request_head = (
        f"POST {path} HTTP/1.1\r\n"
        + "\r\n".join(f"{k}: {v}" for k, v in merged_headers.items())
        + "\r\n\r\n"
    ).encode("utf-8")

    try:
        sock.sendall(request_head + body)
    except Exception as exc:
        latency.error = f"send: {exc!r}"
        latency.wall_ms = (time.perf_counter() - t0) * 1000
        sock.close()
        exc._call_latency = latency  # type: ignore[attr-defined]
        raise
    t_sent = time.perf_counter()
    latency.send_ms = (t_sent - t_tls) * 1000

    # -- TTFB: wait for first byte -------------------------------------
    sock.settimeout(timeout)
    try:
        first = sock.recv(1)
        if not first:
            raise RuntimeError("connection closed before any response data")
    except Exception as exc:
        latency.error = f"ttfb: {exc!r}"
        latency.wall_ms = (time.perf_counter() - t0) * 1000
        sock.close()
        exc._call_latency = latency  # type: ignore[attr-defined]
        raise
    t_ttfb = time.perf_counter()
    latency.ttfb_ms = (t_ttfb - t_sent) * 1000

    # -- Drain the rest of the response --------------------------------
    chunks = [first]
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except Exception as exc:
        latency.error = f"download: {exc!r}"
    finally:
        sock.close()
    t_end = time.perf_counter()
    latency.download_ms = (t_end - t_ttfb) * 1000
    latency.wall_ms = (t_end - t0) * 1000

    # -- Parse HTTP response -------------------------------------------
    raw = b"".join(chunks)
    sep = raw.find(b"\r\n\r\n")
    if sep < 0:
        latency.error = "malformed_response_no_header_separator"
        latency.status_code = -1
        return -1, {}, raw, latency

    header_block = raw[:sep].decode("iso-8859-1", errors="replace")
    body_raw = raw[sep + 4:]

    lines = header_block.split("\r\n")
    status_line = lines[0] if lines else ""
    parts = status_line.split(" ", 2)
    try:
        status = int(parts[1])
    except (IndexError, ValueError):
        status = -1
    latency.status_code = status

    resp_headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        resp_headers[k.strip()] = v.strip()

    # Decode chunked transfer encoding if needed.
    te = resp_headers.get("Transfer-Encoding", "").lower()
    if "chunked" in te:
        body_raw = _decode_chunked(body_raw)

    return status, resp_headers, body_raw, latency


def _decode_chunked(data: bytes) -> bytes:
    """Decode HTTP/1.1 chunked transfer encoding to flat bytes."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        eol = data.find(b"\r\n", i)
        if eol < 0:
            break
        size_str = data[i:eol].split(b";", 1)[0].strip()
        try:
            size = int(size_str, 16)
        except ValueError:
            break
        i = eol + 2
        if size == 0:
            break
        out.extend(data[i:i + size])
        i += size + 2  # past chunk data + trailing \r\n
    return bytes(out)
