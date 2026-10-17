"""Local / LAN single-line English handwriting recognition experiment."""
import argparse
import io
import ipaddress
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from time import perf_counter
from urllib.parse import urlsplit

# Allow running this script directly from the project checkout.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from workshop.ocr import MODEL_NAME, load_model, recognize


class Handler(BaseHTTPRequestHandler):
    def reply(self, status, payload, content_type="application/json; charset=utf-8"):
        body = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, dict) else payload
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        try:
            target = urlsplit("http://" + self.headers.get("Host", ""))
            if target.port != self.server.server_port or target.username or target.password or target.path or target.query or target.fragment:
                return False
            name = target.hostname
            if name in {"localhost", "127.0.0.1"} or name in self.server.allowed_hosts:
                return True
            address = ipaddress.ip_address(name)
            return self.server.lan_enabled and address.version == 4 and address.is_private and not address.is_unspecified
        except (ValueError, TypeError):
            return False

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "请使用本机或局域网 IP 访问"})
        if self.path != "/":
            return self.reply(404, {"error": "页面不存在"})
        self.reply(200, Path(__file__).with_suffix(".html").read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        if not self.valid_host() or self.headers.get("Origin") != f"http://{self.headers.get('Host')}":
            return self.reply(403, {"error": "请从同一地址的试写页面提交"})
        if self.path != "/recognize":
            return self.reply(404, {"error": "接口不存在"})
        if self.headers.get("Content-Type", "").split(";")[0] not in {"image/png", "image/jpeg", "image/webp"}:
            return self.reply(415, {"error": "请选择 PNG、JPEG 或 WebP 图片"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 5 * 1024 * 1024:
                return self.reply(413, {"error": "图片需小于 5 MB"})
            self.connection.settimeout(30)
            raw = self.rfile.read(size)
            self.reply(200, recognize(raw))
        except ValueError as exc:
            self.reply(400, {"error": str(exc)})
        except Exception:
            import traceback
            traceback.print_exc()
            self.reply(500, {"error": "识别失败，请查看启动终端中的错误信息"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="Use 0.0.0.0 to allow LAN access")
    parser.add_argument("--allow-host", action="append", default=[], help="Additional allowed hostname (no port)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--prepare", action="store_true", help="Download/cache the model and exit")
    args = parser.parse_args()
    load_model()
    if not args.prepare:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.allowed_hosts = set(args.allow_host)
        server.lan_enabled = args.host != "127.0.0.1"
        print(f"OCR 试写页：http://127.0.0.1:{args.port}（Ctrl+C 停止）", flush=True)
        if server.lan_enabled:
            print(f"局域网访问：http://<此电脑的局域网IP>:{args.port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()
