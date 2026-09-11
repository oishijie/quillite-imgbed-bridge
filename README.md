# Quillite-ImgBed Bridge

让「轻阅 Markdown」用上你自建的 [CloudFlare-ImgBed](https://github.com/MarSeventh/CloudFlare-ImgBed) 图床。

## 为什么需要它

轻阅的在线图床只认 **PicGo 的本地 HTTP 协议**，而且服务地址被硬性限制为回环地址。它发出的请求是：

```
POST /heartbeat                        ->  {"success":true}
POST /upload  (multipart 字段名 files)  ->  {"success":true,"result":["https://..."]}
```

而 CloudFlare-ImgBed 的上传接口是：

```
POST /upload?authCode=xxx  (multipart 字段名 file)  ->  [{"src":"/file/xxx.jpg"}]
```

四处不兼容：**接口认证、字段名、返回结构、URL 形态**。所以轻阅无法直连 ImgBed——这不是配置问题，是协议层面的差异（详见 `image_upload.go` 中的 `normalisePicGoServerURL`）。

本服务在两者之间做翻译：监听回环地址，说 PicGo 的话，干 ImgBed 的事。**轻阅那边不需要改任何代码。**

## 快速开始

### 1. 配置

首次运行会自动生成 `config.json` 模板。填两个必填项即可：

```json
{
  "listen": "127.0.0.1:36678",
  "baseUrl": "https://你的图床域名",
  "authCode": "你的上传认证码",
  "logFile": "bridge.log"
}
```

- **baseUrl**：你的 ImgBed 站点根地址，结尾不带斜杠
- **authCode**：在 ImgBed 后台「系统设置 → 安全设置」里设置的上传认证码
  （也可以改用 `apiToken`，在「安全设置 → API Token 管理」生成，需 `upload` 权限）

### 2. 启动

双击 `启动.bat`，或直接运行 `QuilliteImgBedBridge.exe`。

看到这样的输出就是就绪了：

```
 Quillite-ImgBed Bridge v1.0.0
 监听地址 : http://127.0.0.1:36678
 目标图床 : https://你的图床域名
 认证方式 : authCode（?authCode=）
```

用浏览器打开 <http://127.0.0.1:36678> 可以看到运行状态。

### 3. 在轻阅里启用

1. 打开轻阅 →「更多」→「图床设置」
2. 选择 **「本机 PicGo」**（不需要真的装 PicGo）
3. 展开 **「高级设置」**，把 **PicGo 服务地址** 改成 `http://127.0.0.1:36678`
4. 点 **「测试连接」**，显示成功即可 **保存设置**

之后粘贴、拖拽、选择的图片都会自动上传到你的 ImgBed。上传失败时轻阅会自动回退到本地 assets，不会丢图。

## 可选配置

| 字段 | 说明 |
|---|---|
| `uploadChannel` | 上传渠道。留空跟随 ImgBed 服务端默认。可选 `telegram` / `cfr2` / `s3` / `discord` / `huggingface` / `webdav` |
| `channelName` | 多渠道场景下指定具体渠道名 |
| `uploadFolder` | 上传到子目录，例如 `img/2026` |
| `uploadNameType` | 命名方式：`default`（前缀_原名）/ `index` / `origin`（仅原名）/ `short` |
| `timeoutSeconds` | 单次上传超时，默认 60 秒 |
| `logFile` | 日志文件，相对路径以 exe 所在目录为基准 |
| `verbose` | 打印图床原始响应体，排查问题时打开 |

## 开机自启

以管理员身份运行 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\安装开机自启.ps1
```

会在「启动」文件夹创建快捷方式。取消自启用 `卸载开机自启.ps1`。

## 常见问题

**双击 `启动.bat` 一闪而过**
脚本编码被改坏了。`.bat` 必须是 **UTF-8 + CRLF**、`.vbs` 必须是 **GBK + CRLF**。一旦变成 LF（Unix）换行，cmd 会把命令行切碎，脚本第一行就报错并瞬间退出，连 `pause` 都来不及执行。修复：

```
python scripts/fix-scripts-encoding.py
```

> 用记事本或 VS Code 改过这两个脚本后，务必跑一次上面的命令。

**轻阅报「没有检测到 PicGo」**
先确认本服务在运行：浏览器打开 <http://127.0.0.1:36678>。如果打不开，说明服务没启动或端口被占用。

**图床返回 `error code: 1010`**
这是 **Cloudflare 的机器人拦截**（Browser Integrity Check），不是 ImgBed 的报错——请求根本没到达应用层。程序已内置浏览器 User-Agent 规避；若你手动改过 `userAgent` 配置项，清空它即可恢复默认。

**域名解析失败（`getaddrinfo failed` / 日志里的 `no such host`）**
先自查域名本身：

```bash
nslookup 你的图床域名
```

返回 `Non-existent domain` 说明 DNS 里根本没有这条记录（子域名拼错或尚未配置），与轻阅和本服务无关。

**轻阅报「在线图床上传失败，已自动使用本地图片」**
看 `bridge.log` 里的 `[ERROR]` 行。最常见的原因：
- `authCode` / `apiToken` 填错 → 图床返回 401/403
- 上传渠道没配置 → 图床返回 5xx，日志里会带原始响应
- `baseUrl` 写错

打开 `verbose` 可以看到图床的完整回应，这是排查最快的办法。

**端口冲突**
如果 36678 被占用，改 `config.json` 里的 `listen` 即可（例如 `127.0.0.1:36679`），然后同步更新轻阅里填的地址。注意别用 PicGo 默认的 36677。

**为什么不能直接填我的图床地址？**
轻阅的 `normalisePicGoServerURL()` 只接受 `http` + `localhost`/回环 IP，这是作者有意设置的安全沙箱（防止应用被利用把图片传到任意服务器）。本服务正是为了在不破坏这个设计的前提下接上自建图床。

## 目录结构

```
quillite-imgbed-bridge/
├── main.go                        服务源码
├── QuilliteImgBedBridge.exe       编译产物（已 gitignore，见「自行编译」）
├── config.example.json            配置模板（复制为 config.json 后填写）
├── config.json                    你的配置（首次运行自动生成，已 gitignore 不入库）
├── bridge.log                     运行日志（已 gitignore）
├── .gitignore                     忽略真实配置与二进制产物
├── 启动.bat                       前台启动（带日志窗口）
├── 静默启动.vbs                   后台无窗口启动
├── 安装开机自启.ps1 / 卸载开机自启.ps1
└── scripts/
    ├── e2e-test.py                协议转换自测（mock 图床，15 项断言）
    ├── real-upload-test.py        真实上传自测（走你的图床，5 项断言）
    ├── verify-imgbed.py           图床可达性 + 凭据有效性探测（零副作用）
    └── fix-scripts-encoding.py    修复启动脚本的编码与换行
```

## 自行编译与测试

```bash
# 编译
GOOS=windows GOARCH=amd64 go build -ldflags "-s -w" -o QuilliteImgBedBridge.exe .

# 1) 协议转换自测：mock 图床，不发真实请求，15 项断言
python scripts/e2e-test.py

# 2) 图床可达性 + 凭据有效性：零副作用，不写入任何文件
python scripts/verify-imgbed.py

# 3) 真实上传自测：往你的图床传一张 1x1 测试图（需服务已在运行）
python scripts/real-upload-test.py
```

自测覆盖 15 项断言：心跳响应、字段名改写（`files`→`file`）、认证码拼接、相对路径补全、错误传播、方法校验等。
