#!/usr/bin/env python3
"""
Que marcar una estacion NUNCA toque la otra.

Esta es la garantia que sostiene la pantalla de bebidas separada: la persona
de barra marca lo suyo y las pizzas siguen pendientes en cocina, pase lo que
pase. Si alguna de estas pruebas falla, esa separacion no es segura.

    python tests/test_estaciones.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_test_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def pedido_mixto():
    """2 pizzas + 2 bebidas, nada despachado."""
    return A.nuevo_pedido("RES-TEST", "prueba", [
        {"nombre": "Hawaiana",      "tipo": "Pizza",  "cantidad": 2, "precio_unit": 20000},
        {"nombre": "Mexicana",      "tipo": "Pizza",  "cantidad": 1, "precio_unit": 20000},
        {"nombre": "Gaseosa",       "tipo": "Bebida", "cantidad": 2, "precio_unit": 4000},
        {"nombre": "Cerveza Poker", "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000},
    ])


def sin_despachar(p, tipo):
    return [i for i in p["productos"] if i["tipo"] == tipo and not i["despachado"]]


def test_barra_no_toca_cocina():
    print("\n  Marcar BEBIDAS no debe tocar las pizzas")
    p = pedido_mixto()
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Bebida")
    q = A.get_pedido(p["id"])
    check(len(sin_despachar(q, "Pizza")) == 2,  "las 2 pizzas siguen SIN despachar")
    check(len(sin_despachar(q, "Bebida")) == 0, "las bebidas quedaron despachadas")
    check(q["estado_cocina"] == "Pendiente",    "estado_cocina sigue 'Pendiente'")
    check(q["estado_barra"] == "Entregado",     "estado_barra pasa a 'Entregado'")


def test_cocina_no_toca_barra():
    print("\n  Marcar PIZZAS no debe tocar las bebidas")
    p = pedido_mixto()
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Pizza")
    q = A.get_pedido(p["id"])
    check(len(sin_despachar(q, "Bebida")) == 2, "las 2 bebidas siguen SIN despachar")
    check(len(sin_despachar(q, "Pizza")) == 0,  "las pizzas quedaron despachadas")
    check(q["estado_barra"] == "Pendiente",     "estado_barra sigue 'Pendiente'")
    check(q["estado_cocina"] == "Entregado",    "estado_cocina pasa a 'Entregado'")


def test_orden_inverso():
    print("\n  El orden en que se marcan no debe importar")
    p = pedido_mixto()
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Bebida")
        A.entregar_estacion(c, p["id"], "Pizza")
    q = A.get_pedido(p["id"])
    check(q["estado_cocina"] == "Entregado" and q["estado_barra"] == "Entregado",
          "marcando barra y luego cocina, las dos quedan entregadas")

    p2 = pedido_mixto()
    with A._conn() as c:
        A.entregar_estacion(c, p2["id"], "Pizza")
        A.entregar_estacion(c, p2["id"], "Bebida")
    q2 = A.get_pedido(p2["id"])
    check(q2["estado_cocina"] == "Entregado" and q2["estado_barra"] == "Entregado",
          "marcando cocina y luego barra, las dos quedan entregadas")


def test_estacion_invalida():
    print("\n  Una estacion inventada debe ser rechazada")
    p = pedido_mixto()
    try:
        with A._conn() as c:
            A.entregar_estacion(c, p["id"], "Postre")
        check(False, "deberia haber lanzado ValueError")
    except ValueError:
        check(True, "ValueError con una estacion desconocida")
    q = A.get_pedido(p["id"])
    check(len([i for i in q["productos"] if i["despachado"]]) == 0,
          "y no despacho nada por error")


def test_ronda_nueva_solo_despierta_su_estacion():
    print("\n  Una ronda de solo bebidas no debe devolver el pedido a cocina")
    p = pedido_mixto()
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Pizza")
        A.entregar_estacion(c, p["id"], "Bebida")
    # El cliente pide otra cerveza
    A.actualizar_pedido(p["id"], [{"nombre": "Cerveza Poker", "tipo": "Bebida",
                                   "cantidad": 1, "precio_unit": 4000}])
    q = A.get_pedido(p["id"])
    check(q["estado_cocina"] == "Entregado", "cocina NO se entera de la ronda de bebidas")
    check(q["estado_barra"] == "Pendiente",  "barra vuelve a 'Pendiente'")
    nuevas = [i for i in q["productos"] if not i["despachado"]]
    check(len(nuevas) == 1 and nuevas[0]["ronda"] == 2,
          f"el item nuevo va en la ronda 2 (ronda={nuevas[0]['ronda'] if nuevas else '?'})")
    check(len([i for i in q["productos"] if i["despachado"]]) == 4,
          "los 4 items de la ronda 1 siguen despachados, no se recocinan")


def test_cuenta_se_cierra_sola():
    print("\n  La cuenta se cierra sola cuando el pago cubre el total")
    p = pedido_mixto()                      # 3 pizzas*20000 + 3 bebidas*4000 = 72000
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Abierta", f"nace 'Abierta' (total={q['total']:.0f})")
    A.registrar_pago(p["id"], 30000, "Efectivo", "prueba", marcar_pagado=False)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Abierta", "con pago parcial sigue 'Abierta'")
    A.registrar_pago(p["id"], q["saldo"], "Nequi", "prueba", marcar_pagado=False)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada",
          "al cubrirse el total se cierra SOLA, sin que nadie pulse nada")


def test_cocina_no_bloquea_el_cobro():
    print("\n  Cocina no debe poder bloquear el cobro")
    p = pedido_mixto()
    A.registrar_pago(p["id"], p["total"], "Efectivo", "prueba", marcar_pagado=False)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada",
          "la cuenta se cierra aunque cocina no haya marcado nada")
    check(q["estado_cocina"] == "Pendiente",
          "y cocina sigue viendolo pendiente, que es lo correcto")


if __name__ == "__main__":
    print("=" * 62)
    print("  SEPARACION DE ESTACIONES")
    print("=" * 62)
    for fn in [test_barra_no_toca_cocina, test_cocina_no_toca_barra, test_orden_inverso,
               test_estacion_invalida, test_ronda_nueva_solo_despierta_su_estacion,
               test_cuenta_se_cierra_sola, test_cocina_no_bloquea_el_cobro]:
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
