// Quillite-ImgBed Bridge
//
// 在「轻阅 Markdown」与「CloudFlare-ImgBed」之间做协议转接。
//
// 为什么需要它：
//
//	轻阅的在线图床只认 PicGo 的本地 HTTP 协议，而且服务地址被硬性限制为
//	回环地址（http://localhost 或 http://127.0.0.1）。它的请求形态是：
//	    POST /heartbeat                -> {"success":true}
//	    POST /upload  (字段名 files)    -> {"success":true,"result":["https://..."]}
//
//	而 CloudFlare-ImgBed 的上传接口是：
//	    POST /upload?authCode=xxx  (字段名 file) -> [{"src":"/file/xxx.jpg"}]
//
//	两者在「接口路径、字段名、认证方式、返回结构」四处都不兼容，因此无法直连。
//
// 本服务监听回环地址，把轻阅的 PicGo 协议翻译成 ImgBed 的 API，于是轻阅那边
// 一行代码都不用改，只要把图床设为「本机 PicGo」并指向本服务即可。
package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"mime/multipart"
	"net/http"
	"net/textproto"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	appName        = "Quillite-ImgBed Bridge"
	appVersion     = "1.0.0"
	defaultListen  = "127.0.0.1:36678"
	defaultTimeout = 60

	// defaultUserAgent 伪装成普通浏览器。Cloudflare 的 Browser Integrity Check
	// 会把 Go-http-client 这类默认 UA 判定为机器人，直接返回 error code: 1010。
	defaultUserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

	// 与 ImgBed 单请求上限保持一致（Cloudflare Pages 请求体上限 100MB）。
	maxRequestSize = 100 << 20
	// multipart 解析时在内存里保留的字节数，超出部分落到临时文件。
	multipartMemory = 16 << 20
	// 读取 ImgBed 响应体的上限，防止异常服务端把内存打满。
	maxResponseSize = 1 << 20
)

type config struct {
	// Listen 本服务监听的地址。必须是回环地址，才能被轻阅接受。
	Listen string `json:"listen"`

	// BaseURL 你的 CloudFlare-ImgBed 站点根地址，例如 https://img.example.com
	BaseURL string `json:"baseUrl"`

	// AuthCode ImgBed 后台上传认证码。与 APIToken 二者填一个即可。
	AuthCode string `json:"authCode"`

	// APIToken ImgBed 的 API Token（需 upload 权限），通过 Authorization 头传递。
	APIToken string `json:"apiToken"`

	// 以下均为可选，留空则使用 ImgBed 服务端的默认值。
	UploadChannel  string `json:"uploadChannel"`  // telegram / cfr2 / s3 / discord / huggingface / webdav
	ChannelName    string `json:"channelName"`    // 多渠道场景下的具体渠道名
	UploadFolder   string `json:"uploadFolder"`   // 上传目录，如 img/2026
	UploadNameType string `json:"uploadNameType"` // default / index / origin / short

	// TimeoutSeconds 单次上传转发的超时时间。
	TimeoutSeconds int `json:"timeoutSeconds"`

	// LogFile 可选。填写后日志同时写入该文件，后台运行时可回看。
	LogFile string `json:"logFile"`

	// Verbose 打开后会把 ImgBed 的原始响应体打进日志，便于排查。
	Verbose bool `json:"verbose"`

	// UserAgent 可选。默认伪装成浏览器，用于规避 Cloudflare 的机器人拦截。
	UserAgent string `json:"userAgent"`
}

type uploadItem struct {
	Src       string `json:"src"`
	PublicURL string `json:"publicUrl"`
	URL       string `json:"url"`
}

type bridge struct {
	cfg    *config
	client *http.Client
}

func main() {
	configPath := flag.String("config", defaultConfigPath(), "配置文件路径")
	listenOverride := flag.String("listen", "", "覆盖配置里的监听地址")
	flag.Parse()

	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.SetPrefix("")

	cfg, err := loadConfig(*configPath)
	if err != nil {
		log.Fatalf("[FATAL] %v", err)
	}
	if *listenOverride != "" {
		cfg.Listen = *listenOverride
	}
	if err := cfg.validate(); err != nil {
		log.Fatalf("[FATAL] 配置无效: %v", err)
	}
	if err := setupLogFile(cfg.LogFile); err != nil {
		log.Printf("[WARN] 日志文件不可用，仅输出到控制台: %v", err)
	}

	b := &bridge{
		cfg: cfg,
		client: &http.Client{
			Timeout: time.Duration(cfg.TimeoutSeconds) * time.Second,
		},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/heartbeat", b.handleHeartbeat)
	mux.HandleFunc("/upload", b.handleUpload)
	mux.HandleFunc("/", b.handleRoot)

	server := &http.Server{
		Addr:              cfg.Listen,
		Handler:           logRequests(mux),
		ReadHeaderTimeout: 10 * time.Second,
	}

	log.Printf("================================================================")
	log.Printf(" %s v%s", appName, appVersion)
	log.Printf(" 监听地址 : http://%s", cfg.Listen)
	log.Printf(" 目标图床 : %s", cfg.BaseURL)
	log.Printf(" 上传渠道 : %s", orDefault(cfg.UploadChannel, "(跟随 ImgBed 服务端默认)"))
	log.Printf(" 认证方式 : %s", authDescription(cfg))
	log.Printf("----------------------------------------------------------------")
	log.Printf(" 在轻阅「图床设置 → 本机 PicGo → 高级设置」中填入：")
	log.Printf("     http://%s", cfg.Listen)
	log.Printf("================================================================")

	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("[FATAL] 服务启动失败: %v", err)
	}
}

func authDescription(cfg *config) string {
	switch {
	case cfg.AuthCode != "" && cfg.APIToken != "":
		return "authCode + API Token"
	case cfg.AuthCode != "":
		return "authCode（?authCode=）"
	case cfg.APIToken != "":
		return "API Token（Authorization 头）"
	default:
		return "未配置（图床需未开启认证才可上传）"
	}
}

func orDefault(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

// appDir 返回可执行文件所在目录。相对路径都以它为基准，
// 这样双击运行、开机自启、从任意目录调用得到的结果都一致。
func appDir() string {
	if executable, err := os.Executable(); err == nil {
		return filepath.Dir(executable)
	}
	return "."
}

func defaultConfigPath() string {
	return filepath.Join(appDir(), "config.json")
}

// resolveRelative 把相对路径按可执行文件所在目录展开。
func resolveRelative(path string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(appDir(), path)
}

// ---------------------------------------------------------------- 配置

func loadConfig(path string) (*config, error) {
	abs := resolveRelative(path)
	data, err := os.ReadFile(abs)
	if errors.Is(err, os.ErrNotExist) {
		if writeErr := os.WriteFile(abs, []byte(configTemplate), 0o600); writeErr != nil {
			return nil, fmt.Errorf("未找到配置文件 %s，且生成模板失败: %w", abs, writeErr)
		}
		return nil, fmt.Errorf("未找到配置文件，已生成模板：\n    %s\n\n请填写 baseUrl 与 authCode（或 apiToken）后重新启动", abs)
	}
	if err != nil {
		return nil, fmt.Errorf("读取配置文件失败: %w", err)
	}

	cfg := &config{}
	if err := json.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("配置文件不是合法 JSON: %w", err)
	}
	if cfg.Listen == "" {
		cfg.Listen = defaultListen
	}
	if cfg.TimeoutSeconds <= 0 {
		cfg.TimeoutSeconds = defaultTimeout
	}
	return cfg, nil
}

func (c *config) validate() error {
	raw := strings.TrimSpace(c.BaseURL)
	if raw == "" {
		return errors.New("baseUrl 未填写，例如 https://img.example.com")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("baseUrl 不是合法地址: %q", raw)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return fmt.Errorf("baseUrl 必须以 http:// 或 https:// 开头: %q", raw)
	}
	if parsed.Host == "" {
		return fmt.Errorf("baseUrl 缺少主机名: %q", raw)
	}
	if parsed.Scheme == "http" {
		log.Printf("[WARN] baseUrl 使用明文 http，图片链接与认证码将以明文传输")
	}
	c.BaseURL = strings.TrimRight(parsed.String(), "/")

	if c.AuthCode == "" && c.APIToken == "" {
		log.Printf("[WARN] 未配置 authCode / apiToken；若你的图床开启了上传认证，请求会被拒绝")
	}
	return nil
}

func setupLogFile(path string) error {
	path = strings.TrimSpace(path)
	if path == "" {
		return nil
	}
	path = resolveRelative(path)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		return err
	}
	log.SetOutput(io.MultiWriter(os.Stdout, file))
	return nil
}

// ---------------------------------------------------------------- 路由

func (b *bridge) handleHeartbeat(w http.ResponseWriter, r *http.Request) {
	// 轻阅用 POST 探活，只检查 HTTP 2xx 且 success 为 true。
	writeJSON(w, http.StatusOK, map[string]any{"success": true})
}

func (b *bridge) handleRoot(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"service":       appName,
		"version":       appVersion,
		"status":        "running",
		"target":        b.cfg.BaseURL,
		"auth":          authDescription(b.cfg),
		"endpoints":     []string{"POST /heartbeat", "POST /upload"},
		"quilliteSetup": "图床设置 → 本机 PicGo → 高级设置 → PicGo 服务地址填 http://" + b.cfg.Listen,
	})
}

func (b *bridge) handleUpload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writePicGoError(w, http.StatusMethodNotAllowed, "本接口只接受 POST")
		return
	}

	r.Body = http.MaxBytesReader(w, r.Body, maxRequestSize)
	if err := r.ParseMultipartForm(multipartMemory); err != nil {
		log.Printf("[ERROR] 解析上传数据失败: %v", err)
		writePicGoError(w, http.StatusBadRequest, "解析上传数据失败: "+err.Error())
		return
	}
	defer func() {
		if r.MultipartForm != nil {
			_ = r.MultipartForm.RemoveAll()
		}
	}()

	// 轻阅用的字段名是 files（复数）；这里同时兼容 file（单数），
	// 方便用 curl 之类的手工测试。
	files := r.MultipartForm.File["files"]
	if len(files) == 0 {
		files = r.MultipartForm.File["file"]
	}
	if len(files) == 0 {
		writePicGoError(w, http.StatusBadRequest, "请求里没有 files 字段")
		return
	}

	links := make([]string, 0, len(files))
	for _, header := range files {
		link, err := b.forwardToImgBed(header)
		if err != nil {
			log.Printf("[ERROR] 上传失败 %s: %v", header.Filename, err)
			writePicGoError(w, http.StatusBadGateway, "转发到 ImgBed 失败: "+err.Error())
			return
		}
		log.Printf("[OK] %s -> %s", header.Filename, link)
		links = append(links, link)
	}

	writeJSON(w, http.StatusOK, map[string]any{"success": true, "result": links})
}

// ---------------------------------------------------------------- 转发

func (b *bridge) forwardToImgBed(header *multipart.FileHeader) (string, error) {
	source, err := header.Open()
	if err != nil {
		return "", fmt.Errorf("打开待上传文件失败: %w", err)
	}
	defer source.Close()

	var payload bytes.Buffer
	writer := multipart.NewWriter(&payload)
	part, err := createFilePart(writer, "file", filepath.Base(header.Filename), header.Header.Get("Content-Type"))
	if err != nil {
		return "", err
	}
	if _, err := io.Copy(part, source); err != nil {
		return "", fmt.Errorf("读取待上传文件失败: %w", err)
	}
	if err := writer.Close(); err != nil {
		return "", err
	}

	endpoint := b.uploadEndpoint()
	request, err := http.NewRequest(http.MethodPost, endpoint, &payload)
	if err != nil {
		return "", err
	}
	request.Header.Set("Content-Type", writer.FormDataContentType())
	request.Header.Set("User-Agent", orDefault(strings.TrimSpace(b.cfg.UserAgent), defaultUserAgent))
	request.Header.Set("Accept", "*/*")
	if b.cfg.APIToken != "" {
		request.Header.Set("Authorization", "Bearer "+b.cfg.APIToken)
	}

	response, err := b.client.Do(request)
	if err != nil {
		return "", fmt.Errorf("连接图床失败: %w", err)
	}
	defer response.Body.Close()

	raw, err := io.ReadAll(io.LimitReader(response.Body, maxResponseSize))
	if err != nil {
		return "", fmt.Errorf("读取图床响应失败: %w", err)
	}
	if b.cfg.Verbose {
		log.Printf("[DEBUG] ImgBed 原始响应 HTTP %d: %s", response.StatusCode, string(raw))
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return "", fmt.Errorf("图床返回 HTTP %d：%s", response.StatusCode, truncate(strings.TrimSpace(string(raw)), 300))
	}

	return b.resolveImageURL(raw)
}

func createFilePart(writer *multipart.Writer, field, filename, contentType string) (io.Writer, error) {
	if contentType == "" {
		contentType = "application/octet-stream"
	}
	safeName := strings.NewReplacer(`"`, "", "\r", "", "\n", "").Replace(filename)
	safeType := strings.NewReplacer("\r", "", "\n", "").Replace(contentType)

	header := textproto.MIMEHeader{}
	header.Set("Content-Disposition", fmt.Sprintf(`form-data; name="%s"; filename="%s"`, field, safeName))
	header.Set("Content-Type", safeType)
	return writer.CreatePart(header)
}

func (b *bridge) uploadEndpoint() string {
	query := url.Values{}
	if b.cfg.AuthCode != "" {
		query.Set("authCode", b.cfg.AuthCode)
	}
	if b.cfg.UploadChannel != "" {
		query.Set("uploadChannel", b.cfg.UploadChannel)
	}
	if b.cfg.ChannelName != "" {
		query.Set("channelName", b.cfg.ChannelName)
	}
	if b.cfg.UploadFolder != "" {
		query.Set("uploadFolder", b.cfg.UploadFolder)
	}
	if b.cfg.UploadNameType != "" {
		query.Set("uploadNameType", b.cfg.UploadNameType)
	}
	// 让图床尽量直接返回完整链接；即使服务端不支持该参数，
	// 下面的 resolveImageURL 也会把相对路径补全。
	query.Set("returnFormat", "full")

	return b.cfg.BaseURL + "/upload?" + query.Encode()
}

// resolveImageURL 从 ImgBed 的响应里取出图片地址并补全为绝对 URL。
//
// 这一步是必需的：轻阅会拒绝任何不是完整 http/https 地址的返回值
// （见 image_upload.go 里的 "PicGo returned an unsafe image URL"），
// 而 ImgBed 默认返回的 src 是 /file/xxx.jpg 这样的相对路径。
func (b *bridge) resolveImageURL(raw []byte) (string, error) {
	items, err := parseUploadItems(raw)
	if err != nil {
		return "", fmt.Errorf("%w：%s", err, truncate(strings.TrimSpace(string(raw)), 300))
	}
	for _, item := range items {
		for _, candidate := range []string{item.Src, item.PublicURL, item.URL} {
			if link, err := b.absolutize(candidate); err == nil {
				return link, nil
			}
		}
	}
	return "", fmt.Errorf("图床未返回可用地址：%s", truncate(strings.TrimSpace(string(raw)), 300))
}

func parseUploadItems(raw []byte) ([]uploadItem, error) {
	var items []uploadItem
	if err := json.Unmarshal(raw, &items); err == nil && len(items) > 0 {
		return items, nil
	}

	var wrapper struct {
		Data    []uploadItem `json:"data"`
		Message string       `json:"message"`
	}
	if err := json.Unmarshal(raw, &wrapper); err == nil && len(wrapper.Data) > 0 {
		return wrapper.Data, nil
	}

	return nil, errors.New("无法识别图床返回的数据结构")
}

func (b *bridge) absolutize(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", errors.New("地址为空")
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return "", err
	}
	if !parsed.IsAbs() {
		base, err := url.Parse(b.cfg.BaseURL + "/")
		if err != nil {
			return "", err
		}
		parsed = base.ResolveReference(parsed)
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return "", fmt.Errorf("协议不安全: %s", parsed.Scheme)
	}
	if parsed.Host == "" {
		return "", errors.New("地址缺少主机名")
	}
	return parsed.String(), nil
}

// ---------------------------------------------------------------- 工具

func writeJSON(w http.ResponseWriter, status int, payload any) {
	body, err := json.Marshal(payload)
	if err != nil {
		http.Error(w, `{"success":false,"message":"encode failed"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

func writePicGoError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{"success": false, "message": message})
}

func truncate(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit]) + "…"
}

func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		next.ServeHTTP(w, r)
		log.Printf("[HTTP] %s %s %s (%s)", r.RemoteAddr, r.Method, r.URL.Path, time.Since(start).Round(time.Millisecond))
	})
}

const configTemplate = `{
  "_说明": "轻阅 Markdown 与 CloudFlare-ImgBed 之间的本地转接服务配置",

  "listen": "127.0.0.1:36678",
  "_listen": "本服务监听地址。必须是回环地址，否则轻阅会拒绝连接。端口别和 PicGo 的 36677 冲突。",

  "baseUrl": "https://你的图床域名",
  "_baseUrl": "你的 CloudFlare-ImgBed 站点根地址，结尾不要带斜杠。",

  "authCode": "",
  "_authCode": "ImgBed 后台上传认证码。在 图床后台 → 系统设置 → 安全设置 里设置。",

  "apiToken": "",
  "_apiToken": "也可以改用 API Token（需 upload 权限），在 系统设置 → 安全设置 → API Token 管理 里生成。与 authCode 填一个即可。",

  "uploadChannel": "",
  "_uploadChannel": "可选。留空表示跟随 ImgBed 服务端默认渠道。可选值：telegram / cfr2 / s3 / discord / huggingface / webdav",

  "channelName": "",
  "_channelName": "可选。多渠道场景下指定具体渠道名，留空表示不指定。",

  "uploadFolder": "",
  "_uploadFolder": "可选。上传到子目录，例如 img/2026（留空则上传到根目录）。",

  "uploadNameType": "",
  "_uploadNameType": "可选。命名方式：default（前缀_原名）/ index（仅前缀）/ origin（仅原名）/ short（短链）。留空用服务端默认。",

  "timeoutSeconds": 60,
  "logFile": "bridge.log",
  "_logFile": "日志文件路径，相对路径以本程序所在目录为基准。留空则不写文件。",

  "verbose": false,
  "_verbose": "设为 true 会把图床的原始响应体打进日志，排查问题时很有用。",

  "userAgent": "",
  "_userAgent": "可选。默认伪装成 Chrome 浏览器。若你的 Cloudflare 开启了 Browser Integrity Check，留空即可（默认值已能通过）；一般无需填写。"
}
`
