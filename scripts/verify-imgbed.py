# -*- coding: utf-8 -*-
"""零副作用探测：验证 ImgBed 站点可达性与凭据是否有效。

刻意不复用转接服务，直接打你的图床，用来区分「bridge 有问题」还是
「图床/凭据有问题」。

安全性说明：
- 只发 GET 首页（读）与一个 **不带文件字段** 的 POST /upload（服务端会在
  参数校验阶段拒绝，不会写入任何数据）。
- 凭据从 config.json 读取，不出现在命令行里，也不会被打印。
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE, "config.json")
TIMEOUT = 20

# Cloudflare 的 Browser Integrity Check 会拦截非浏览器 UA（返回 error code: 1010），
# 所以探测时必须伪装成普通浏览器。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def mask(value):
    if not value:
        return "(未配置)"
    if len(value) <= 8:
        return "***"
    return value[:6] + "..." + value[-4:]


def request(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", BROWSER_UA)
    req.add_header("Accept", "*/*")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:  # 网络层失败
        return None, "%s: %s" % (type(exc).__name__, exc)


def main():
    if not os.path.exists(CONFIG):
        print("[FAIL] 找不到 config.json")
        return 1

    with open(CONFIG, encoding="utf-8-sig") as handle:
        cfg = json.load(handle)

    base = (cfg.get("baseUrl") or "").strip().rstrip("/")
    token = (cfg.get("apiToken") or "").strip()
    code = (cfg.get("authCode") or "").strip()

    if not base:
        print("[FAIL] baseUrl 未填写")
        return 1
    if not base.startswith("https://") and not base.startswith("http://"):
        print("[FAIL] baseUrl 必须以 http:// 或 https:// 开头，当前为 %r" % base)
        return 1

    print("目标图床 : %s" % base)
    print("apiToken : %s" % mask(token))
    print("authCode : %s" % mask(code))
    print("-" * 62)

    failures = 0

    # 1) 站点可达性
    status, body = request(base + "/")
    if status is None:
        print("[FAIL] 站点不可达 -> %s" % body)
        failures += 1
    elif status < 500:
        print("[PASS] 站点可达（首页 HTTP %d）" % status)
    else:
        print("[WARN] 站点返回 HTTP %d，可能是 Cloudflare 拦截" % status)
        failures += 1

    # 2) 凭据探测：不带文件字段的 POST，服务端会在参数校验阶段拒绝
    url = base + "/upload"
    if code:
        url += "?" + urllib.parse.urlencode({"authCode": code})
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    status, body = request(url, method="POST", headers=headers, data=b"")

    if status is None:
        print("[FAIL] 上传接口不可达 -> %s" % body)
        failures += 1
    elif status in (401, 403):
        print("[FAIL] 凭据被拒绝（HTTP %d）—— token 无效 / 缺少 upload 权限" % status)
        print("       响应: %s" % body[:300])
        failures += 1
    else:
        print("[PASS] 凭据未被拒绝（HTTP %d）—— 说明认证已通过，仅缺文件参数" % status)
        print("       响应: %s" % body[:300])

    print("-" * 62)
    print("结果：%s" % ("全部通过" if not failures else "%d 项需要处理" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
