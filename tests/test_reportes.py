#!/usr/bin/env python3
"""
Que el reporte cuente lo que de verdad se vendió.

El 26/09/2026 el reporte de "Ayer" dio $14.000: un solo pedido de bebidas y
cero pizzas. El reporte solo contaba `estado='Pagado'`, pero la pantalla de
Cobrar cierra la cuenta sin escribir esa columna, así que ninguna pizza
cobrada ahí llegaba al reporte.

    python tests/test_reportes.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_rep_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar(usuario="cajero1", password="cajero123"):
    c = A.app.test_client()
    c.post('/login', data={'usuario': usuario, 'password': password}, follow_redirects=True)
    return c


def pedido(codigo, pizzas=1, bebidas=1):
    items = []
    if pizzas:
        items.append({"nombre": "Hawaiana", "tipo": "Pizza", "cantidad": pizzas, "precio_unit": 20000})
    if bebidas:
        items.append({"nombre": "Gaseosa", "tipo": "Bebida", "cantidad": bebidas, "precio_unit": 4000})
    return A.nuevo_pedido(codigo, "prueba", items)


def hoy():
    return A.iso_a_fecha(A.jornada_actual())


def test_pizza_cobrada_en_caja_cuenta_como_venta():
    print("\n  EL CASO DEL 26/09: pizza cobrada en la pantalla de Cobrar")
    antes = A.get_reporte(hoy(), hoy())
    p = pedido("PIZZA-CAJA", pizzas=2, bebidas=1)            # 44.000
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Efectivo'}, follow_redirects=True)
    with A._conn() as con:
        A.entregar_estacion(con, p["id"], "Pizza")
        A.entregar_estacion(con, p["id"], "Bebida")
    q = A.get_pedido(p["id"])
    check(q["estado"] != "Pagado", f"la columna vieja sigue en '{q['estado']}' (así pasó)")
    d = A.get_reporte(hoy(), hoy())
    check(d["n_pedidos"] == antes["n_pedidos"] + 1, "el pedido entra al reporte")
    check(d["total_ventas"] == antes["total_ventas"] + 44000, "con sus 44.000")
    check(d["n_pizzas"] == antes["n_pizzas"] + 2, "y sus 2 pizzas")


def test_cuenta_abierta_no_es_venta():
    print("\n  Lo que no se ha cobrado todavía no es ingreso")
    antes = A.get_reporte(hoy(), hoy())
    pedido("SIN-COBRAR")
    p = pedido("A-MEDIAS")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Nequi', 'monto': '10000'},
           follow_redirects=True)
    d = A.get_reporte(hoy(), hoy())
    check(d["n_pedidos"] == antes["n_pedidos"], "ni el sin cobrar ni el de pago parcial cuentan")
    check(d["total_cobrado"] == antes["total_cobrado"] + 10000,
          "pero el abono sí aparece en lo cobrado por canal")


def test_cortesia_y_anulado_no_son_venta():
    print("\n  Cortesías y anulados quedan fuera")
    antes = A.get_reporte(hoy(), hoy())
    p = pedido("CORTESIA")
    c = entrar()
    c.post(f'/cajero/cuenta/{p["id"]}/cerrar_manual', data={'motivo': 'cumpleaños'},
           follow_redirects=True)
    check(A.get_pedido(p["id"])["estado_cuenta"] == "Cerrada", "la cortesía cierra la cuenta")
    q = pedido("ANULADO")
    c.post(f'/cajero/cobrar/{q["id"]}', data={'metodo': 'Efectivo'}, follow_redirects=True)
    with A._conn() as con:
        con.execute("UPDATE pedidos SET anulado=1 WHERE id=?", (q["id"],))
    d = A.get_reporte(hoy(), hoy())
    check(d["n_pedidos"] == antes["n_pedidos"], "ninguno de los dos suma al reporte")


def test_flujo_viejo_sigue_contando():
    print("\n  Lo que el flujo viejo dejó en 'Pagado' sigue contando")
    antes = A.get_reporte(hoy(), hoy())
    p = pedido("VIEJO", pizzas=0, bebidas=2)                # 8.000
    A.registrar_pago(p["id"], 8000, "Efectivo", "prueba", marcar_pagado=True)
    d = A.get_reporte(hoy(), hoy())
    check(d["n_pedidos"] == antes["n_pedidos"] + 1, "entra al reporte")
    check(d["total_ventas"] == antes["total_ventas"] + 8000, "con sus 8.000")


def test_excel_trae_lo_mismo():
    print("\n  El Excel del reporte trae los mismos pedidos")
    c = entrar("admin", "admin123")
    csv = c.get(f'/admin/reportes/csv?fi={hoy()}&ff={hoy()}').data.decode()
    check("PIZZA-CAJA" in csv, "la pizza cobrada en caja está en el Excel")
    check("CORTESIA" not in csv and "ANULADO" not in csv, "la cortesía y el anulado no")


if __name__ == "__main__":
    print("=" * 62)
    print("  REPORTE DE VENTAS")
    print("=" * 62)
    for fn in [test_pizza_cobrada_en_caja_cuenta_como_venta, test_cuenta_abierta_no_es_venta,
               test_cortesia_y_anulado_no_son_venta, test_flujo_viejo_sigue_contando,
               test_excel_trae_lo_mismo]:
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
