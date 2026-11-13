#!/usr/bin/env python3
"""
Genera, a partir de los logos oficiales de Logos-JacaPizza/, todo lo que la
app usa de la marca:

  static/marca/   el logo horizontal en tamaño web (barra, login, etc.)
  static/iconos/  los iconos cuadrados de la pantalla de inicio y el favicon

Se corre a mano cuando cambie la marca; los PNG que produce se guardan en el
repositorio. Pillow hace falta SOLO para esto, no para que la app funcione:
por eso no está en requirements.txt y el servidor no lo instala.

    python generar_iconos.py
"""
import pathlib
import sys

from PIL import Image

RAIZ = pathlib.Path(__file__).parent
ORIGEN = RAIZ / "Logos-JacaPizza"
MARCA = RAIZ / "static" / "marca"
ICONOS = RAIZ / "static" / "iconos"

FONDO = (41, 39, 35)        # --bg  #292723

# Logo oficial -> nombre en static/marca/
LOGOS = {
    "JacaPizza-principal-fondo-oscuro.png": "jacapizza-oscuro.png",
    "JacaPizza-fondo-claro.png":            "jacapizza-claro.png",
    "JacaPizza-monocromo-crema.png":        "jacapizza-mono-crema.png",
    "JacaPizza-monocromo-oscuro.png":       "jacapizza-mono-oscuro.png",
}
# 1200 px de ancho alcanza para el login a 3x y pesa poco.
ANCHO_WEB = 1200

# Cortes dentro del PNG original (3090x738), medidos sobre el canal alfa.
# Si llega un logo con otro encuadre, hay que volver a medirlos.
CORTE_JACA = (93, 1394)
CORTE_PIZZA = (1446, 2998)
CORTE_J = (93, 292)

TAMANOS = [("icono-180.png", 180), ("icono-192.png", 192),
           ("icono-512.png", 512), ("favicon-32.png", 32)]


def recortar(img, x0, x1):
    """Columnas x0..x1, ajustado al alto real de la tinta."""
    trozo = img.crop((x0, 0, x1, img.height))
    return trozo.crop(trozo.getchannel("A").getbbox())


def escalar_ancho(img, ancho):
    alto = round(img.height * ancho / img.width)
    return img.resize((ancho, alto), Image.LANCZOS)


def escalar_alto(img, alto):
    ancho = round(img.width * alto / img.height)
    return img.resize((ancho, alto), Image.LANCZOS)


def logos_web():
    MARCA.mkdir(parents=True, exist_ok=True)
    for origen, destino in LOGOS.items():
        img = Image.open(ORIGEN / origen).convert("RGBA")
        img = img.crop(img.getchannel("A").getbbox())   # sin el aire de los bordes
        ruta = MARCA / destino
        escalar_ancho(img, ANCHO_WEB).save(ruta, "PNG", optimize=True)
        print(f"  marca/{destino:24} {ruta.stat().st_size:>7} bytes")


def dibujar(lado, logo):
    """Cuadrado a sangre completa: iOS le pone las esquinas redondeadas él
    solo, así que dibujarlas aquí daría un doble borde."""
    img = Image.new("RGBA", (lado, lado), FONDO + (255,))

    if lado <= 48:
        # A este tamaño "JacaPizza" es una mancha: solo la inicial.
        j = escalar_alto(recortar(logo, *CORTE_J), round(lado * 0.66))
        img.alpha_composite(j, ((lado - j.width) // 2, (lado - j.height) // 2))
        return img.convert("RGB")

    # Dos líneas: a 60px de pantalla, "JacaPizza" en una sola no se lee.
    # El conjunto cabe en el 70% central para que Android pueda recortarlo
    # en círculo (icono "maskable") sin comerse letras.
    jaca = recortar(logo, *CORTE_JACA)
    pizza = recortar(logo, *CORTE_PIZZA)
    ancho = round(lado * 0.70)
    escala = ancho / max(jaca.width, pizza.width)
    jaca = jaca.resize((round(jaca.width * escala), round(jaca.height * escala)), Image.LANCZOS)
    pizza = pizza.resize((round(pizza.width * escala), round(pizza.height * escala)), Image.LANCZOS)
    hueco = round(lado * 0.04)
    total = jaca.height + hueco + pizza.height
    y = (lado - total) // 2
    img.alpha_composite(jaca, ((lado - jaca.width) // 2, y))
    img.alpha_composite(pizza, ((lado - pizza.width) // 2, y + jaca.height + hueco))
    return img.convert("RGB")


def main():
    logos_web()
    ICONOS.mkdir(parents=True, exist_ok=True)
    logo = Image.open(ORIGEN / "JacaPizza-principal-fondo-oscuro.png").convert("RGBA")
    for nombre, lado in TAMANOS:
        ruta = ICONOS / nombre
        dibujar(lado, logo).save(ruta, "PNG", optimize=True)
        print(f"  iconos/{nombre:16} {lado:>3}x{lado:<3}  {ruta.stat().st_size:>6} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
