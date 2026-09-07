"""Gera resources/icon.ico (rode: python resources/make_icon.py)."""
import os
from PIL import Image, ImageDraw, ImageFont

SIZE = 256


def build():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Fundo com cantos arredondados, gradiente simples roxo -> azul
    for y in range(SIZE):
        t = y / SIZE
        r = int(124 + (60 - 124) * t)
        g = int(92 + (110 - 92) * t)
        b = int(255 + (240 - 255) * t)
        d.line([(0, y), (SIZE, y)], fill=(r, g, b, 255))

    mask = Image.new("L", (SIZE, SIZE), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=52, fill=255)
    img.putalpha(mask)

    # "Página" estilizada
    page_w, page_h = 110, 150
    px, py = (SIZE - page_w) // 2 - 10, (SIZE - page_h) // 2
    d.rounded_rectangle(
        [px, py, px + page_w, py + page_h], radius=10,
        fill=(255, 255, 255, 235)
    )
    d.rounded_rectangle(
        [px + 18, py + 22, px + page_w + 18, py + page_h + 22], radius=10,
        fill=(255, 255, 255, 130), outline=None
    )
    d.rounded_rectangle(
        [px, py, px + page_w, py + page_h], radius=10,
        fill=(255, 255, 255, 235)
    )
    for i, ly in enumerate(range(py + 30, py + page_h - 20, 22)):
        d.rounded_rectangle(
            [px + 16, ly, px + page_w - 16, ly + 8], radius=4,
            fill=(124, 92, 255, 180)
        )

    out_dir = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(out_dir, "icon.ico")
    img.save(out_path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Ícone salvo em: {out_path}")


if __name__ == "__main__":
    build()
