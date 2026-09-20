#!/usr/bin/env python3
"""
Venta rápida: una bebida suelta en pocos toques.

Lo que más se comprueba: que el sistema NUNCA invente un código. Los códigos
son de reserva de estadía y los da el alojamiento; una venta sin reserva debe
quedar con el campo vacío, no con algo fabricado.

    python tests/test_venta_rapida.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_vr_"), "t.db")

import app as A

_fallos = []
GASEOSA = {"nombre": "Gaseosa", "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000}


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar():
    c = A.app.test_client()
    c.post('/login', data={'usuario': 'juandavid', 'password': 'juan2026'},
           follow_redirects=True)
    return c


def vender(cliente, **datos):
    base = {"items": [dict(GASEOSA)], "codigo": "", "metodo": "Efectivo", "a_cuenta": False}
    base.update(datos)
    r = cliente.post('/venta-rapida', json=base)
    return r.status_code, r.get_json()


def test_sin_codigo_no_se_inventa_nada():
    print("\n  SIN CÓDIGO — el sistema no debe inventar uno")
    c = entrar()
    cod, d = vender(c)
    check(cod == 200 and d.get('ok'), "la venta se registra")
    p = A.get_pedido(d['id'])
    check(p["mesa"] == "", f"el campo código queda VACÍO (quedó {p['mesa']!r})")
    check(p["tipo_venta"] == "rapida", "marcada como venta rápida")
    check(p["estado_cuenta"] == "Cerrada", "cobrada al instante")
    check(p["estado_barra"] == "Entregado", "y entregada: no va a la pantalla de barra")
    check(not any(k["pedido_id"] == p["id"] for k in A.listar_estacion("Bebida")),
          "confirmado: no aparece en barra")


def test_codigo_con_cuenta_abierta_se_suma():
    print("\n  CÓDIGO CON CUENTA ABIERTA — se suma a esa estadía")
    base = A.nuevo_pedido("R41-10", "prueba", [
        {"nombre": "Hawaiana", "tipo": "Pizza", "cantidad": 1, "precio_unit": 20000}])
    c = entrar()
    cod, d = vender(c, codigo="R41-10", a_cuenta=True, metodo="")
    check(cod == 200 and d.get('ok'), "se acepta")
    check(d['id'] == base['id'], "va al MISMO pedido, no crea otro")
    q = A.get_pedido(base['id'])
    check(q["total"] == 24000, f"el total acumulado sube a 24.000 (${q['total']:.0f})")
    check(q["saldo"] == 24000, "y queda debiendo el total")
    check(q["estado_cocina"] == "Pendiente", "la pizza sigue pendiente en cocina")


def test_codigo_mayusculas_y_espacios():
    print("\n  El código se encuentra aunque se escriba distinto")
    A.nuevo_pedido("R41-20", "prueba", [dict(GASEOSA)])
    c = entrar()
    for variante in ["r41-20", "  R41-20  ", "R41-20"]:
        d = c.get(f'/api/reserva?codigo={variante.strip()}').get_json()
        check(d.get('existe'), f"encuentra la cuenta con {variante!r}")


def test_codigo_de_cuenta_ya_pagada_la_reabre():
    print("\n  CÓDIGO DE CUENTA YA PAGADA — se reabre con la bebida")
    base = A.nuevo_pedido("R41-30", "prueba", [dict(GASEOSA)])
    A.registrar_pago(base['id'], 4000, "Efectivo", "prueba", marcar_pagado=False)
    check(A.get_pedido(base['id'])["estado_cuenta"] == "Cerrada", "parte de una cuenta cerrada")
    c = entrar()
    cod, d = vender(c, codigo="R41-30", a_cuenta=True, metodo="")
    q = A.get_pedido(base['id'])
    check(d.get('id') == base['id'], "se suma a la misma cuenta")
    check(q["estado_cuenta"] == "Abierta", "la cuenta se REABRE sola")
    check(q["saldo"] == 4000, f"debiendo solo la bebida nueva (${q['saldo']:.0f})")
    check(q["total_pagado"] == 4000, "el pago anterior sigue registrado")


def test_codigo_sin_cuenta_pide_confirmacion():
    print("\n  CÓDIGO SIN CUENTA — avisa antes de crear una fantasma")
    c = entrar()
    cod, d = vender(c, codigo="NO-EXISTE-99")
    check(cod == 409 and d.get('confirmar'), "pide confirmación en vez de crearla")
    check("NO-EXISTE-99" in d.get('mensaje', ''), "y dice de qué código habla")
    antes = len(A.listar_pedidos(anulado=None))
    vender(c, codigo="NO-EXISTE-99")
    check(len(A.listar_pedidos(anulado=None)) == antes, "sin confirmar, NO crea nada")
    cod, d = vender(c, codigo="NO-EXISTE-99", confirmado=True)
    check(cod == 200 and d.get('ok'), "al confirmar sí la crea")
    check(A.get_pedido(d['id'])["mesa"] == "NO-EXISTE-99", "con el código que se escribió")


def test_exige_medio_de_pago():
    print("\n  Sin decir cómo paga, no se registra")
    c = entrar()
    cod, d = vender(c, metodo="")
    check(cod == 400 and 'error' in d, "se rechaza")
    cod, d = vender(c, metodo="Bitcoin")
    check(cod == 400, "y un medio inventado también")


def test_respeta_el_stock():
    print("\n  No se puede vender lo que no hay")
    A.upsert_inventario("Gaseosa", "bebida", 0)
    c = entrar()
    cod, d = vender(c)
    check(cod == 400 and 'error' in d, f"rechazado: {d.get('error', '')[:44]}")
    A.upsert_inventario("Gaseosa", "bebida", 10)


def test_descuenta_inventario_una_sola_vez():
    print("\n  El inventario se descuenta exactamente una vez")
    A.upsert_inventario("Gaseosa", "bebida", 10)
    c = entrar()
    vender(c, items=[{"nombre": "Gaseosa", "tipo": "Bebida", "cantidad": 3, "precio_unit": 4000}])
    check(A.get_stock_dict().get("Gaseosa") == 7,
          f"de 10 a 7 (quedó {A.get_stock_dict().get('Gaseosa')})")


if __name__ == "__main__":
    print("=" * 62)
    print("  VENTA RÁPIDA")
    print("=" * 62)
    for fn in [test_sin_codigo_no_se_inventa_nada, test_codigo_con_cuenta_abierta_se_suma,
               test_codigo_mayusculas_y_espacios, test_codigo_de_cuenta_ya_pagada_la_reabre,
               test_codigo_sin_cuenta_pide_confirmacion, test_exige_medio_de_pago,
               test_respeta_el_stock, test_descuenta_inventario_una_sola_vez]:
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
