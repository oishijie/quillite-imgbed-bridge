#!/usr/bin/env python3
"""端到端验证 Quillite-ImgBed Bridge 的协议转换是否正确。

用一个 mock 图床顶替真实的 CloudFlare-ImgBed，完整跑一遍轻阅会走的链路：
    POST /heartbeat  ->  {"success":true}
    POST /upload (字段名 files)  ->  转发为  POST /upload?authCode=..&returnFormat=full (字段名 file)
                                 ->  相对路径补全为绝对 URL
                                 ->  {"success":true,"result":["https://..."]}

用法：python scripts/e2e-test.py
"""

import base64
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BRIDGE_DIR = pathlib.Path(__file__).resolve().parent.parent
BRIDGE_EXE = BRIDGE_DIR / "QuilliteImgBedBridge.exe"

MOCK_PORT = 39999
BRIDGE_PORT = 39998
AUTH_CODE = "E2E_TEST_CODE_123"
MOCK_IMAGE_PATH = "/file/e2e-test-abc123.png"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

received = []


class MockImgBed(BaseHTTPRequestHandler):
    """冒充 CloudFlare-ImgBed 的 /upload 接口。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        received.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "content_type": self.headers.get("Content-Type", ""),
                "body_len": len(body),
                "has_file_field": b'name="file"' in body,
                "has_files_field": b'name="files"' in body,
                "has_original_name": b'filename="e2e-sample.png"' in body,
            }
        )

        if b'filename="fail.png"' in body:
            payload = json.dumps({"error": "mock channel failure"}).encode()
            self.send_response(500)
        else:
            payload = json.dumps([{"src": MOCK_IMAGE_PATH}]).encode()
            self.send_response(200)

        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def post_multipart(url, field_name, filename, content, content_type="image/png", timeout=20):
    boundary = "----QuilliteBridgeE2EBoundary1234567890"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except Exception as error:  # noqa: BLE001
        return None, str(error).encode()


def post_empty(url, timeout=5):
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except Exception as error:  # noqa: BLE001
        return None, str(error).encode()


def wait_until_ready(url, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, _ = post_empty(url + "/heartbeat", timeout=1)
        if status == 200:
            return True
        time.sleep(0.15)
    return False


def main():
    if not BRIDGE_EXE.exists():
        print(f"[FAIL] 找不到可执行文件：{BRIDGE_EXE}")
        print("       请先执行：GOOS=windows GOARCH=amd64 go build -o QuilliteImgBedBridge.exe .")
        return 1

    workdir = tempfile.mkdtemp(prefix="quillite-bridge-e2e-")
    config_path = os.path.join(workdir, "config.json")
    log_path = os.path.join(workdir, "bridge.log")

    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "listen": f"127.0.0.1:{BRIDGE_PORT}",
                "baseUrl": f"http://127.0.0.1:{MOCK_PORT}",
                "authCode": AUTH_CODE,
                "logFile": log_path,
                "verbose": True,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    mock = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockImgBed)
    threading.Thread(target=mock.serve_forever, daemon=True).start()

    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    bridge = subprocess.Popen(
        [str(BRIDGE_EXE), "-config", config_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )

    base = f"http://127.0.0.1:{BRIDGE_PORT}"
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, bool(ok), detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f"  ->  {detail}" if detail else ""))

    try:
        if not wait_until_ready(base):
            print("[FAIL] 转接服务在 15 秒内没有就绪，请检查配置")
            print(f"       临时目录：{workdir}")
            return 1

        # 1. 探活
        status, body = post_empty(base + "/heartbeat")
        check("心跳 /heartbeat 返回 200", status == 200, f"HTTP {status}")
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            payload = {}
        check("心跳响应 success=true", payload.get("success") is True, body.decode(errors="replace"))

        # 2. 正常上传
        received.clear()
        status, body = post_multipart(base + "/upload", "files", "e2e-sample.png", PNG_1PX)
        try:
            result = json.loads(body)
        except Exception:  # noqa: BLE001
            result = {}

        expected = f"http://127.0.0.1:{MOCK_PORT}{MOCK_IMAGE_PATH}"
        check("上传 /upload 返回 200", status == 200, f"HTTP {status}")
        check("上传响应 success=true", result.get("success") is True, body.decode(errors="replace"))
        links = result.get("result") or []
        check("result 是数组且非空", isinstance(links, list) and len(links) > 0, json.dumps(result, ensure_ascii=False))
        check(
            "相对路径已补全为绝对 URL",
            links[:1] == [expected],
            f"实际={links[:1]} 期望=[{expected}]",
        )

        # 3. 检查转发出门的请求形态
        check("图床确实收到了转发请求", len(received) == 1, f"收到 {len(received)} 条")
        if received:
            got = received[0]
            check(
                "字段名已由 files 改写为 file",
                got["has_file_field"] and not got["has_files_field"],
                f'name="file"={got["has_file_field"]}  name="files"={got["has_files_field"]}',
            )
            check("原始文件名被保留", got["has_original_name"], got["content_type"])
            check(
                "authCode 已拼进查询串",
                f"authCode={AUTH_CODE}" in got["path"],
                got["path"],
            )
            check("已带上 returnFormat=full", "returnFormat=full" in got["path"], got["path"])
            check("请求路径指向 /upload", got["path"].startswith("/upload?"), got["path"])

        # 4. 上游失败时错误要正确传播
        status, body = post_multipart(base + "/upload", "files", "fail.png", PNG_1PX)
        check("上游 500 时返回非 2xx", status is not None and status >= 400, f"HTTP {status}")
        try:
            failure = json.loads(body)
        except Exception:  # noqa: BLE001
            failure = {}
        check("错误响应里 success=false", failure.get("success") is False, body.decode(errors="replace"))

        # 5. 非 POST 方法应被拒绝
        try:
            with urllib.request.urlopen(base + "/upload", timeout=5) as response:
                get_status = response.status
        except urllib.error.HTTPError as error:
            get_status = error.code
        except Exception:  # noqa: BLE001
            get_status = None
        check("GET /upload 被拒绝", get_status == 405, f"HTTP {get_status}")

    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bridge.kill()
        mock.shutdown()

    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print("-" * 62)
    print(f"结果：{passed}/{total} 通过")
    print(f"临时目录：{workdir}")

    if os.path.exists(log_path):
        print("-" * 62)
        print("转接服务日志：")
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            for line in handle.read().splitlines():
                print("  " + line)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
