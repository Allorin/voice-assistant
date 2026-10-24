"""Собрать иконки сайта из одной картинки: python scripts/make_icons.py [design/icon-source.jpg]

Исходник — квадрат со светлой скруглённой подложкой (сгенерирован по промпту, см. README).
- favicon 16/32 — крупный план головы: целиком персонаж на вкладке превращается в пятно;
- favicon 48, иконки 180/192/512 — вся картинка;
- maskable 512 — для Android: персонаж вписан в безопасный круг (80 % диаметра), фон до краёв;
- avatar — голова для шапки страницы.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "icons"

TILE = (229, 236, 244)        # цвет подложки на исходнике
TILE_RADIUS = 0.13            # скругление подложки (доля стороны)
HEAD = (0.205, 0.146, 0.811, 0.752)   # кадр «голова» в долях стороны исходника
CHARACTER = (0.195, 0.150, 0.805, 0.850)  # границы персонажа целиком


def rounded_mask(size: int, ratio: float = TILE_RADIUS) -> Image.Image:
    ss = 4  # рисуем крупнее и уменьшаем — сглаженные края
    m = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size * ss - 1, size * ss - 1], radius=int(size * ss * ratio), fill=255)
    return m.resize((size, size), Image.LANCZOS)


def flat_tile(src: Image.Image) -> Image.Image:
    """Белые углы исходника закрашиваются цветом подложки — получается квадрат без прозрачности."""
    full = Image.new("RGB", src.size, TILE)
    full.paste(src, (0, 0), rounded_mask(src.size[0]))
    return full


def crop(src: Image.Image, frac: tuple[float, float, float, float]) -> Image.Image:
    w = src.size[0]
    return src.crop(tuple(round(v * w) for v in frac))


def rounded(img: Image.Image, size: int) -> Image.Image:
    out = img.resize((size, size), Image.LANCZOS).convert("RGBA")
    out.putalpha(rounded_mask(size))
    return out


def save_png(img: Image.Image, path: Path) -> None:
    """Плоская картинка без потерь на глаз укладывается в 256 цветов — файл в несколько раз меньше."""
    if img.mode == "RGBA":
        img = img.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    else:
        img = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    img.save(path, optimize=True)


def maskable(src: Image.Image, size: int = 512) -> Image.Image:
    """Безопасная зона maskable-иконки — круг радиусом 40 % стороны; углы персонажа должны в него попасть."""
    x0, y0, x1, y1 = CHARACTER
    half_diag = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5 / 2  # в долях стороны исходника
    scale = 0.40 / half_diag
    inner = round(size * scale)
    canvas = Image.new("RGB", (size, size), TILE)
    canvas.paste(flat_tile(src).resize((inner, inner), Image.LANCZOS), ((size - inner) // 2, (size - inner) // 2))
    return canvas


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "design" / "icon-source.jpg"
    src = Image.open(source).convert("RGB")
    if src.size[0] != src.size[1]:
        sys.exit("Нужна квадратная картинка")
    OUT.mkdir(parents=True, exist_ok=True)
    full, head = flat_tile(src), crop(src, HEAD)

    small = {16: rounded(head, 16), 32: rounded(head, 32), 48: rounded(full, 48)}
    small[16].save(OUT / "favicon-16.png")
    small[32].save(OUT / "favicon-32.png")
    small[48].save(OUT / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                   append_images=[small[16], small[32]])
    save_png(rounded(full, 192), OUT / "icon-192.png")
    save_png(rounded(full, 512), OUT / "icon-512.png")
    save_png(full.resize((180, 180), Image.LANCZOS), OUT / "apple-touch-icon.png")  # iOS скругляет углы сам
    save_png(maskable(src), OUT / "icon-maskable-512.png")
    save_png(head.resize((128, 128), Image.LANCZOS), OUT / "avatar.png")

    manifest = {
        "name": "Голосовой ежедневник",
        "short_name": "Ежедневник",
        "lang": "ru",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#edf1f6",
        "theme_color": "#0e7c66",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    (ROOT / "app" / "static" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                                          encoding="utf-8")
    print("Готово:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
