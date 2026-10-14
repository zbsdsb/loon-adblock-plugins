package main

import (
	"bytes"
	"image"
	"image/color"
	"image/jpeg"
	"image/png"
	"strings"
	"testing"
)

func pngBytes(t *testing.T, img image.Image) []byte {
	t.Helper()
	var b bytes.Buffer
	if err := png.Encode(&b, img); err != nil {
		t.Fatal(err)
	}
	return b.Bytes()
}

func TestBlackFrameIsNotSuccess(t *testing.T) {
	_, _, _, err := convertFrame(pngBytes(t, image.NewRGBA(image.Rect(0, 0, 750, 1334))))
	if err == nil || !strings.Contains(err.Error(), "screen_unavailable") {
		t.Fatalf("black frame accepted: %v", err)
	}
}

func TestMalformedFrameFails(t *testing.T) {
	if _, _, _, err := convertFrame([]byte("not an image")); err == nil {
		t.Fatal("accepted invalid image")
	}
}

func TestSmallFrameFails(t *testing.T) {
	if _, _, _, err := convertFrame(pngBytes(t, image.NewRGBA(image.Rect(0, 0, 10, 10)))); err == nil {
		t.Fatal("accepted thumbnail")
	}
}

func TestVisibleFrameEncodesJPEG(t *testing.T) {
	img := image.NewRGBA(image.Rect(0, 0, 750, 1334))
	for y := 0; y < 100; y++ {
		for x := 0; x < 100; x++ {
			img.Set(x, y, color.White)
		}
	}
	data, w, h, err := convertFrame(pngBytes(t, img))
	if err != nil {
		t.Fatal(err)
	}
	if w != 750 || h != 1334 {
		t.Fatal("dimensions changed")
	}
	if _, err := jpeg.Decode(bytes.NewReader(data)); err != nil {
		t.Fatal(err)
	}
}
