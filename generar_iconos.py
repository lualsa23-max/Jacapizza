#!/usr/bin/env python3
"""
Genera los iconos de la app para la pantalla de inicio del celular.

Se corre a mano cuando cambie la marca; los PNG que produce se guardan en el
repositorio. Pillow hace falta SOLO para esto, no para que la app funcione:
por eso no está en requirements.txt y el servidor no lo instala.

    python generar_iconos.py
"""
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFont

FONDO = (41, 39, 35)        # --bg  #292723
LETRA = (224, 56, 154)      # --brand-text  #e0389a

# Verdana, la misma del logo de la app. Si no está (Linux), se cae a la de
# Pillow: el icono sale más pobre pero no se rompe la generación.
CANDIDATAS = [
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
    "/Library/Fonts/Verdana Bold.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Verdana_Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

DESTINO = pathlib.Path(__file__).parent / "static" / "iconos"
TAMANOS = [("icono-180.png", 180), ("icono-192.png", 192),
           ("icono-512.png", 512), ("favicon-32.png", 32)]


def buscar_fuente(px):
    for ruta in CANDIDATAS:
        if pathlib.Path(ruta).exists():
            return ImageFont.truetype(ruta, px)
    return ImageFont.load_default()


def dibujar(lado):
    """Cuadrado a sangre completa: iOS le pone las esquinas redondeadas él
    solo, así que dibujarlas aquí daría un doble borde."""
    img = Image.new("RGB", (lado, lado), FONDO)
    d = ImageDraw.Draw(img)

    if lado <= 48:
        # A este tamaño "JacaPizza" es una mancha: solo la inicial.
        f = buscar_fuente(int(lado * 0.62))
        _texto_centrado(d, img, "J", f, lado / 2)
        return img

    # Dos líneas: a 60px de pantalla, "JacaPizza" en una sola no se lee.
    f = buscar_fuente(int(lado * 0.26))
    alto = int(lado * 0.28)
    _texto_centrado(d, img, "Jaca", f, lado / 2 - alto / 2)
    _texto_centrado(d, img, "Pizza", f, lado / 2 + alto / 2)
    return img


def _texto_centrado(d, img, txt, fuente, cy):
    x0, y0, x1, y1 = d.textbbox((0, 0), txt, font=fuente)
    d.text(((img.width - (x1 - x0)) / 2 - x0, cy - (y1 - y0) / 2 - y0),
           txt, font=fuente, fill=LETRA)


def main():
    DESTINO.mkdir(parents=True, exist_ok=True)
    usando = next((r for r in CANDIDATAS if pathlib.Path(r).exists()), "la de Pillow")
    print(f"  fuente: {usando}")
    for nombre, lado in TAMANOS:
        ruta = DESTINO / nombre
        dibujar(lado).save(ruta, "PNG", optimize=True)
        print(f"  {nombre:16} {lado:>3}x{lado:<3}  {ruta.stat().st_size:>6} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
