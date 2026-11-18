package main

import (
	"archive/zip"
	"bufio"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

const baseURL = "http://14.225.198.188:8088"
const nodeZip = "https://nodejs.org/dist/v20.19.0/node-v20.19.0-win-x64.zip"

func main() {
	if runtime.GOOS != "windows" {
		fmt.Println("File cài này chạy trên Windows.")
		os.Exit(1)
	}
	reader := bufio.NewReader(os.Stdin)
	fmt.Println("Cài kết nối PC với VPS quét bình luận")
	fmt.Printf("VPS: %s\n", baseURL)
	fmt.Print("Token PC: ")
	token, _ := reader.ReadString('\n')
	token = strings.TrimSpace(token)
	fmt.Print("Slot 1-4: ")
	slotText, _ := reader.ReadString('\n')
	slot, err := strconv.Atoi(strings.TrimSpace(slotText))
	if err != nil || slot < 1 || slot > 4 || token == "" {
		fmt.Println("Cần token và slot từ 1 đến 4.")
		os.Exit(1)
	}
	fmt.Print("Tên máy (Enter để lấy tên Windows): ")
	host, _ := reader.ReadString('\n')
	host = strings.TrimSpace(host)
	if host == "" {
		host, _ = os.Hostname()
	}

	dir := filepath.Join(os.Getenv("LOCALAPPDATA"), "Tech666PC")
	if err := os.MkdirAll(dir, 0755); err != nil {
		fail(err)
	}
	link := fmt.Sprintf("{\n  \"base\": %q,\n  \"token\": %q,\n  \"host\": %q,\n  \"slot\": %d\n}\n", baseURL, token, host, slot)
	if err := os.WriteFile(filepath.Join(dir, "pc-link.json"), []byte(link), 0600); err != nil {
		fail(err)
	}
	fmt.Println("Đã ghi file kết nối.")
	for _, name := range []string{"huong-a.js", "pc-run.js", "renew-home-ip.ps1"} {
		fmt.Println("Tải", name)
		if err := download(baseURL+"/pc/"+name, filepath.Join(dir, name)); err != nil {
			fail(err)
		}
	}
	nodeExe := filepath.Join(dir, "node.exe")
	if _, err := os.Stat(nodeExe); err != nil {
		fmt.Println("Tải Node.js cho PC...")
		zipPath := filepath.Join(dir, "node.zip")
		if err := download(nodeZip, zipPath); err != nil {
			fail(err)
		}
		if err := unzipNode(zipPath, nodeExe); err != nil {
			fail(err)
		}
		os.Remove(zipPath)
	}
	cmdPath := filepath.Join(dir, "start-pc.cmd")
	cmd := fmt.Sprintf("@echo off\r\ncd /d %s\r\nstart \"\" /b %s pc-run.js\r\n", quote(dir), quote(nodeExe))
	if err := os.WriteFile(cmdPath, []byte(cmd), 0644); err != nil {
		fail(err)
	}
	startup := filepath.Join(os.Getenv("APPDATA"), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Tech666PC.cmd")
	if err := os.WriteFile(startup, []byte(cmd), 0644); err != nil {
		fail(err)
	}
	fmt.Println("Đã đặt tự chạy khi đăng nhập Windows.")
	proc := exec.Command(nodeExe, "pc-run.js")
	proc.Dir = dir
	if err := proc.Start(); err != nil {
		fail(err)
	}
	fmt.Printf("Đã cài và đang chạy slot %d, máy %s.\n", slot, host)
	fmt.Println("Bản mới trên VPS sẽ được tải và bật ngay, không cần cài lại.")
	time.Sleep(2 * time.Second)
}

func quote(s string) string {
	if strings.ContainsAny(s, " \t") {
		return `"` + s + `"`
	}
	return s
}

func download(url, dest string) error {
	resp, err := http.Get(url)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("%s trả %s", url, resp.Status)
	}
	tmp := dest + ".part"
	f, err := os.Create(tmp)
	if err != nil {
		return err
	}
	if _, err := io.Copy(f, resp.Body); err != nil {
		f.Close()
		return err
	}
	f.Close()
	return os.Rename(tmp, dest)
}

func unzipNode(zipPath, nodeExe string) error {
	r, err := zip.OpenReader(zipPath)
	if err != nil {
		return err
	}
	defer r.Close()
	for _, f := range r.File {
		if !strings.HasSuffix(strings.ToLower(f.Name), "/node.exe") && !strings.EqualFold(filepath.Base(f.Name), "node.exe") {
			continue
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.Create(nodeExe)
		if err != nil {
			rc.Close()
			return err
		}
		_, err = io.Copy(out, rc)
		out.Close()
		rc.Close()
		return err
	}
	return fmt.Errorf("không thấy node.exe trong gói Node")
}

func fail(err error) {
	fmt.Println("Lỗi:", err)
	os.Exit(1)
}
