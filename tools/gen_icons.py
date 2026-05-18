#!/usr/bin/env python3
"""arquant_icon.png → 안드로이드 런처 아이콘 전 밀도 재생성.

- 적응형 포그라운드(ic_launcher_foreground): 투명 캔버스 중앙에 로고를 안전영역(~66%)
  에 맞춰 배치 (런처 원형/스퀘어클 마스크에 잘리지 않도록).
- 레거시(ic_launcher / ic_launcher_round): #0A0E1A 배경 위에 로고를 합성(평탄화).
"""
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "arquant_mobile" / "arquant_icon.png"
RES = ROOT / "arquant_mobile" / "app" / "src" / "main" / "res"
BG = (0x0A, 0x0E, 0x1A, 0xFF)  # @color/ic_launcher_background

# density: (legacy square px, adaptive foreground px)
DENS = {
    "mdpi":    (48, 108),
    "hdpi":    (72, 162),
    "xhdpi":   (96, 216),
    "xxhdpi":  (144, 324),
    "xxxhdpi": (192, 432),
}
FG_SAFE = 0.66   # 적응형 포그라운드 안전영역 비율
LEGACY_FILL = 0.90  # 레거시 아이콘에서 로고가 차지하는 비율

src = Image.open(SRC).convert("RGBA")
# 투명 여백 자동 크롭 (불투명 이미지면 bbox = 전체 → 그대로)
bbox = src.split()[3].getbbox()
logo = src.crop(bbox) if bbox else src


def scaled(target_long: int) -> Image.Image:
    w, h = logo.size
    s = target_long / max(w, h)
    return logo.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def centered(canvas_px: int, fill: float, background) -> Image.Image:
    canvas = Image.new("RGBA", (canvas_px, canvas_px), background)
    lg = scaled(round(canvas_px * fill))
    canvas.alpha_composite(lg, ((canvas_px - lg.width) // 2,
                                (canvas_px - lg.height) // 2))
    return canvas


written = []
for dens, (legacy_px, fg_px) in DENS.items():
    d = RES / f"mipmap-{dens}"
    d.mkdir(parents=True, exist_ok=True)

    fg = centered(fg_px, FG_SAFE, (0, 0, 0, 0))
    fg.save(d / "ic_launcher_foreground.png")

    legacy = centered(legacy_px, LEGACY_FILL, BG).convert("RGB")
    legacy.save(d / "ic_launcher.png")
    legacy.save(d / "ic_launcher_round.png")

    written.append(f"{dens}: fg {fg_px}px, legacy {legacy_px}px")

print("source logo bbox:", bbox, "→ cropped", logo.size)
for w in written:
    print("  ", w)
print("DONE — regenerated", len(DENS) * 3, "icon PNGs")
