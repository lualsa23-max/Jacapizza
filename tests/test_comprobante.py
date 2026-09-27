#!/usr/bin/env python3
"""
Foto del comprobante en los pagos electrónicos.

Un pago por Nequi, Daviplata, tarjeta o llave tiene que poder quedar con la
foto del comprobante pegada, en el momento del cobro o después desde Caja.

    python tests/test_comprobante.py
"""
import io, os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_comp_"), "t.db")

import app as A

_fallos = []
JPG = b"\xff\xd8\xff\xe0" + b"x" * 2000     # basta con que sea un archivo .jpg


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar(usuario="cajero1", password="cajero123"):
    c = A.app.test_client()
    c.post('/login', data={'usuario': usuario, 'password': password}, follow_redirects=True)
    return c


def pedido(codigo):
    return A.nuevo_pedido(codigo, "prueba", [
        {"nombre": "Hawaiana", "tipo": "Pizza", "cantidad": 1, "precio_unit": 20000}])


def foto():
    return (io.BytesIO(JPG), "comprobante.jpg")


def test_cobro_con_foto():
    print("\n  Cobrar por Nequi con la foto")
    p = pedido("NEQUI-FOTO")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Nequi', 'comprobante': foto()},
           content_type='multipart/form-data', follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "la cuenta se cierra igual que siempre")
    pg = q["pagos"][0]
    check(pg["comprobante"].endswith(".jpg"), "el pago queda con su comprobante")
    ruta = os.path.join(A.COMPROBANTES_FOLDER, pg["comprobante"])
    check(os.path.exists(ruta) and open(ruta, "rb").read() == JPG, "el archivo está en el volumen")
    r = c.get(f'/comprobante/{pg["comprobante"]}')
    check(r.status_code == 200 and r.data == JPG, "y se puede ver")
    anon = A.app.test_client().get(f'/comprobante/{pg["comprobante"]}')
    check(anon.status_code in (301, 302), "pero no sin iniciar sesión")


def test_cobro_parcial_con_foto():
    print("\n  Un abono por Daviplata también lleva foto")
    p = pedido("PARCIAL-FOTO")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}',
           data={'metodo': 'Daviplata', 'monto': '5000', 'comprobante': foto()},
           content_type='multipart/form-data', follow_redirects=True)
    pg = A.get_pedido(p["id"])["pagos"][0]
    check(pg["monto"] == 5000 and pg["comprobante"], "abono de 5.000 con comprobante")


def test_sin_foto_tambien_cobra():
    print("\n  Sin foto el cobro no se bloquea")
    p = pedido("SIN-FOTO")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Nequi'}, follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "se cobra")
    check(q["pagos"][0]["comprobante"] == "", "y queda marcado sin comprobante")
    html = c.get('/cajero/caja').data.decode()
    check("sin foto" in html, "en Caja aparece para completarlo")


def test_efectivo_ignora_archivos():
    print("\n  El efectivo no guarda comprobante")
    p = pedido("EFECTIVO")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Efectivo', 'comprobante': foto()},
           content_type='multipart/form-data', follow_redirects=True)
    check(A.get_pedido(p["id"])["pagos"][0]["comprobante"] == "", "no se guarda nada")


def test_agregar_despues():
    print("\n  Agregar la foto después, desde Caja")
    p = pedido("DESPUES")
    A.registrar_pago(p["id"], 20000, "Llave Bre-B", "Cajero", marcar_pagado=False)
    pago_id = A.get_pedido(p["id"])["pagos"][0]["id"]
    otro = entrar("mesero1", "mesero123")
    otro.post(f'/cajero/pago/{pago_id}/comprobante', data={'comprobante': foto()},
              content_type='multipart/form-data')
    check(A.get_pedido(p["id"])["pagos"][0]["comprobante"] == "",
          "otra persona no puede ponérselo")
    admin = entrar("admin", "admin123")
    admin.post(f'/cajero/pago/{pago_id}/comprobante', data={'comprobante': foto()},
               content_type='multipart/form-data')
    check(A.get_pedido(p["id"])["pagos"][0]["comprobante"] != "", "un administrador sí")


def test_archivo_invalido():
    print("\n  Un archivo que no es foto ni PDF no se guarda")
    p = pedido("EXE")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}',
           data={'metodo': 'Nequi', 'comprobante': (io.BytesIO(b"MZ"), "virus.exe")},
           content_type='multipart/form-data', follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "el cobro sí queda")
    check(q["pagos"][0]["comprobante"] == "", "el archivo no")


if __name__ == "__main__":
    print("=" * 62)
    print("  COMPROBANTES DE PAGO")
    print("=" * 62)
    for fn in [test_cobro_con_foto, test_cobro_parcial_con_foto, test_sin_foto_tambien_cobra,
               test_efectivo_ignora_archivos, test_agregar_despues, test_archivo_invalido]:
        try:
            fn()
        except Exception:
            traceback.print_exc()
            _fallos.append(fn.__name__)
    print("\n" + "=" * 62)
    print(f"  {'TODO BIEN' if not _fallos else f'{len(_fallos)} FALLOS'}")
    for f in _fallos:
        print(f"    - {f}")
    print("=" * 62)
    sys.exit(1 if _fallos else 0)
