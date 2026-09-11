# -*- coding: utf-8 -*-
"""真实端到端上传测试：轻阅协议 -> 本地转接服务 -> 你的 ImgBed。

会在你的图床创建一张 1x1 像素测试图（约 70 字节），用于验证：
  1) 转接服务的 User-Agent 能通过 Cloudflare（否则会被 error code 1010 拦截）
  2) API Token 认证有效
  3) 返回的相对路径被正确补全为可直接嵌入 Markdown 的绝对 URL

前置条件：QuilliteImgBedBridge.exe 已在 127.0.0.1:36678 运行。

用法：python scripts/real-upload-test.py
"""

import base64
import os
import sys
import urllib.error
import urllib.request

BRIDGE = "http://127.0.0.1:36678"

# 1x1 透明 PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# 不走系统代理，确保直连本地回环
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

results = []


def check(name, passed, detail=""):
    results.append((name, passed, detail))
    print("[%s] %s%s" % ("PASS" if passed else "FAIL", name, ("  ->  " + detail) if detail else ""))


def call(path, method="GET", data=None, headers=None, timeout=60):
    req = urllib.request.Request(BRIDGE + path, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with OPENER.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        return None, "%s: %s" % (type(exc).__name__, exc)


def build_multipart(field, filename, content, content_type):
    boundary = "----QuilliteBridgeE2EBoundary"
    body = b"".join(
        [
            ("--%s\r\n" % boundary).encode(),
            ('Content-Disposition: form-data; name="%s"; filename="%s"\r\n' % (field, filename)).encode(),
            ("Content-Type: %s\r\n\r\n" % content_type).encode(),
            content,
            ("\r\n--%s--\r\n" % boundary).encode(),
        ]
    )
    return body, "multipart/form-data; boundary=" + boundary


def main():
    print("转接服务: %s" % BRIDGE)
    print("-" * 62)

    # 1) 探活
    status, body = call("/heartbeat", method="POST", data=b"", timeout=10)
    check("探活 /heartbeat 返回 2xx", status is not None and 200 <= status < 300, "HTTP %s" % status)

    # 2) 真实上传（字段名用轻阅实际发的 files）
    payload, content_type = build_multipart(
        "files", "quillite-bridge-test.png", PNG_1X1, "image/png"
    )
    status, body = call("/upload", method="POST", data=payload, headers={"Content-Type": content_type})
    if status is None:
        check("上传 /upload 返回 2xx", False, body)
        return report()

    check("上传 /upload 返回 2xx", 200 <= status < 300, "HTTP %s" % status)
    if not (status and 200 <= status < 300):
        print("       响应: %s" % body[:400])
        return report()

    # 3) 解析返回，取出图片 URL
    image_url = ""
    try:
        import json

        data = json.loads(body)
        if data.get("success") and data.get("result"):
            image_url = data["result"][0]
    except Exception as exc:
        check("响应是合法 JSON", False, str(exc))

    check("返回结构为 {success:true, result:[url]}", bool(image_url), body[:200])
    check(
        "返回的是完整 http(s) URL（轻阅的硬性要求）",
        image_url.startswith("http://") or image_url.startswith("https://"),
        image_url or "(空)",
    )

    # 4) 回访图片，确认链接真的能打开
    if image_url:
        req = urllib.request.Request(image_url, method="GET")
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        try:
            with OPENER.open(req, timeout=30) as resp:
                head = resp.read(16)
                ctype = resp.headers.get("Content-Type", "")
                if head[:8] == b"\x89PNG\r\n\x1a\n":
                    magic = "PNG"
                elif head[:2] == b"\xff\xd8":
                    magic = "JPEG"
                else:
                    magic = "其他"
                # 注意：ImgBed 可能对图片做转码/压缩（实测 PNG 会被压成 JPEG），
                # 因此这里只要求 HTTP 200 且 Content-Type 是图片，不强制原始魔数。
                check(
                    "图片链接可访问且返回图片内容",
                    resp.status == 200 and ctype.startswith("image/"),
                    "HTTP %d, Content-Type=%s, 实际格式=%s" % (resp.status, ctype, magic),
                )
        except Exception as exc:
            check("图片链接可访问", False, "%s: %s" % (type(exc).__name__, exc))

    print("-" * 62)
    if image_url:
        print("本次上传的测试图：%s" % image_url)
        print("（1x1 像素，可在 ImgBed 后台删除）")

    return report()


def report():
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("结果：%d/%d 通过" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
