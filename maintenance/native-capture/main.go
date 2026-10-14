// 原生截图观察器：只读取 Instruments 屏幕，不注入、不监听截图 HTTP 端口。
package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image/jpeg"
	"image/png"
	"os"
	"path/filepath"
	"time"

	"github.com/danielpaulus/go-ios/ios"
	"github.com/danielpaulus/go-ios/ios/instruments"
	"github.com/danielpaulus/go-ios/ios/tunnel"
)

type config struct {
	UDID       string  `json:"udid"`
	Directory  string  `json:"directory"`
	TunnelPort int     `json:"tunnel_port"`
	Duration   float64 `json:"duration"`
	Interval   float64 `json:"interval"`
	ReviewGate bool    `json:"review_gate"`
}

// 熄屏时 Instruments 可能成功返回全黑 PNG；必须拒绝，不能把 exit 0 当成有效画面。
func convertFrame(raw []byte) ([]byte, int, int, error) {
	img, err := png.Decode(bytes.NewReader(raw))
	if err != nil {
		return nil, 0, 0, fmt.Errorf("decode native PNG: %w", err)
	}
	bounds := img.Bounds()
	if bounds.Dx() < 100 || bounds.Dy() < 100 {
		return nil, 0, 0, errors.New("invalid screenshot dimensions")
	}
	visible := false
	for y := bounds.Min.Y; y < bounds.Max.Y && !visible; y += 8 {
		for x := bounds.Min.X; x < bounds.Max.X; x += 8 {
			r, g, b, _ := img.At(x, y).RGBA()
			if r > 1285 || g > 1285 || b > 1285 {
				visible = true
				break
			}
		}
	}
	if !visible {
		return nil, 0, 0, errors.New("screen_unavailable_black_frame")
	}
	var data bytes.Buffer
	if err := jpeg.Encode(&data, img, &jpeg.Options{Quality: 85}); err != nil {
		return nil, 0, 0, err
	}
	return data.Bytes(), bounds.Dx(), bounds.Dy(), nil
}

func emit(v any) error { return json.NewEncoder(os.Stdout).Encode(v) }

func resolve(c config) (ios.DeviceEntry, error) {
	d, err := ios.GetDevice(c.UDID)
	if err != nil {
		return d, fmt.Errorf("USB device: %w", err)
	}
	if d.Properties.SerialNumber != c.UDID {
		return d, errors.New("device identity mismatch")
	}
	t, err := tunnel.TunnelInfoForDevice(c.UDID, "127.0.0.1", c.TunnelPort)
	if err != nil {
		return d, fmt.Errorf("local tunnel: %w", err)
	}
	d.UserspaceTUN, d.UserspaceTUNHost, d.UserspaceTUNPort = t.UserspaceTUN, "127.0.0.1", t.UserspaceTUNPort
	s, err := ios.NewWithAddrPortDevice(t.Address, t.RsdPort, d)
	if err != nil {
		return d, err
	}
	defer s.Close()
	provider, err := s.Handshake()
	if err != nil {
		return d, err
	}
	if provider.Udid != c.UDID {
		return d, errors.New("RSD identity mismatch")
	}
	d.Address, d.Rsd = t.Address, provider
	return d, nil
}

func run() error {
	var c config
	if err := json.NewDecoder(os.Stdin).Decode(&c); err != nil {
		return err
	}
	if c.UDID == "" || !filepath.IsAbs(c.Directory) || c.Duration < 1 || c.Duration > 30 || c.Interval < .1 || c.Interval > .5 || c.TunnelPort < 1 || c.TunnelPort > 65535 {
		return errors.New("invalid bounded capture configuration")
	}
	if err := os.MkdirAll(c.Directory, 0700); err != nil {
		return err
	}
	d, err := resolve(c)
	if err != nil {
		return err
	}
	s, err := instruments.NewScreenshotService(d)
	if err != nil {
		return err
	}
	defer s.Close()
	if c.ReviewGate {
		data, err := s.TakeScreenshot()
		if err != nil {
			return err
		}
		data, _, _, err = convertFrame(data)
		if err != nil {
			return err
		}
		name := filepath.Join(c.Directory, "preflight.jpg")
		if err := os.WriteFile(name, data, 0600); err != nil {
			return err
		}
		digest := sha256.Sum256(data)
		if err := emit(map[string]any{"event": "preflight", "file": "preflight.jpg", "sha256": hex.EncodeToString(digest[:]), "timestamp": float64(time.Now().UnixNano()) / 1e9}); err != nil {
			return err
		}
		var request struct {
			Action string `json:"action"`
		}
		if err := json.NewDecoder(os.Stdin).Decode(&request); err != nil {
			return err
		}
		if request.Action != "capture" {
			return errors.New("capture not approved")
		}
	}
	if err := emit(map[string]any{"event": "ready", "backend": "native-instruments", "network_listener": false}); err != nil {
		return err
	}
	started := time.Now()
	for i := 0; time.Since(started).Seconds() < c.Duration; i++ {
		tick := time.Now()
		data, err := s.TakeScreenshot()
		if err != nil {
			return fmt.Errorf("take screenshot: %w", err)
		}
		captured := time.Now()
		jpegData, width, height, err := convertFrame(data)
		if err != nil {
			return err
		}
		name := fmt.Sprintf("frame-%03d.jpg", i)
		path := filepath.Join(c.Directory, name)
		file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			return err
		}
		_, writeErr := file.Write(jpegData)
		closeErr := file.Close()
		if writeErr != nil {
			return writeErr
		}
		if closeErr != nil {
			return closeErr
		}
		digest := sha256.Sum256(jpegData)
		frame := map[string]any{"event": "frame", "file": name, "sha256": hex.EncodeToString(digest[:]), "timestamp": float64(captured.UnixNano()) / 1e9, "acquisition_started": float64(tick.UnixNano()) / 1e9, "width": width, "height": height, "foreground_state": "unreviewed"}
		if err := emit(frame); err != nil {
			return err
		}
		if remaining := time.Duration(c.Interval*float64(time.Second)) - time.Since(tick); remaining > 0 {
			time.Sleep(remaining)
		}
	}
	return emit(map[string]any{"event": "finished"})
}

func main() {
	if err := run(); err != nil {
		// 完整错误仅进入调用方保存的 private stderr，不冒充有效画面。
		fmt.Fprintln(os.Stderr, err)
		_ = emit(map[string]any{"event": "error", "reason": "native_capture_failed"})
		os.Exit(2)
	}
}
