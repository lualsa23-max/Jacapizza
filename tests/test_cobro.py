#!/usr/bin/env python3
"""
Que un pedido no se pueda quedar sin cobrar por un olvido.

En los datos reales había 12 pedidos preparados y entregados que nunca se
cobraron — $641.000 entre junio y septiembre. La causa: la pantalla de cobro
mostraba solo lo que COCINA hubiera marcado como listo, así que un olvido
sacaba el pedido de la caja para siempre.

    python tests/test_cobro.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_cob_"), "t.db")

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


def pedido(codigo="RES-X", pizzas=1, bebidas=1):
    items = []
    if pizzas:
        items.append({"nombre": "Hawaiana", "tipo": "Pizza", "cantidad": pizzas, "precio_unit": 20000})
    if bebidas:
        items.append({"nombre": "Gaseosa", "tipo": "Bebida", "cantidad": bebidas, "precio_unit": 4000})
    return A.nuevo_pedido(codigo, "prueba", items)


def en_cobro(pid):
    return any(p["id"] == pid for p in A.listar_pedidos(estado_cuenta="Abierta") if p["total"] > 0)


def test_cocina_no_puede_esconder_un_pedido():
    print("\n  EL CASO QUE COSTÓ $641.000")
    p = pedido("OLVIDADO")
    check(p["estado_cocina"] == "Pendiente", "cocina no lo ha marcado")
    check(en_cobro(p["id"]), "y AUN ASÍ aparece para cobrar")
    c = entrar()
    html = c.get('/cajero/cobrar').data.decode()
    check("OLVIDADO" in html, "se ve en la pantalla de cobro")


def test_cobrar_cierra_la_cuenta_sola():
    print("\n  La cuenta se cierra sola al cubrirse, sin botón extra")
    p = pedido("CIERRA")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Efectivo'}, follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "queda cerrada")
    check(q["saldo"] == 0, "sin saldo")
    check(not en_cobro(p["id"]), "y sale de la pantalla de cobro")
    check(q["estado_cocina"] == "Pendiente", "aunque cocina siga sin entregarla")


def test_pago_parcial():
    print("\n  Dividir la cuenta")
    p = pedido("PARCIAL", pizzas=2, bebidas=1)      # 44.000
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Nequi', 'monto': '20000'},
           follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["total_pagado"] == 20000, "registra los 20.000")
    check(q["saldo"] == 24000, f"queda debiendo 24.000 (${q['saldo']:.0f})")
    check(q["estado_cuenta"] == "Abierta", "y la cuenta sigue abierta")
    check(en_cobro(p["id"]), "sigue en la pantalla de cobro")
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Efectivo'}, follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "al saldar el resto, se cierra")
    check(len(q["pagos"]) == 2, "con los dos pagos registrados por separado")


def test_no_se_puede_cobrar_de_mas():
    print("\n  No se puede cobrar más de lo que se debe")
    p = pedido("TOPE", pizzas=1, bebidas=0)         # 20.000
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Efectivo', 'monto': '99000'},
           follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["total_pagado"] == 0, "el cobro excesivo se rechaza")
    check(q["estado_cuenta"] == "Abierta", "la cuenta sigue abierta")


def test_medio_de_pago_invalido():
    print("\n  Un medio de pago inventado no pasa")
    p = pedido("METODO")
    c = entrar()
    c.post(f'/cajero/cobrar/{p["id"]}', data={'metodo': 'Bitcoin'}, follow_redirects=True)
    check(A.get_pedido(p["id"])["total_pagado"] == 0, "no registra nada")


def test_quien_cobro_queda_registrado():
    print("\n  Queda constancia de quién cobró y cuánto debe entregar")
    # Las pruebas comparten base, asi que se mide la DIFERENCIA, no el total.
    antes_c = A.resumen_caja(persona="Caren Muñetón")
    antes_j = A.resumen_caja(persona="Juan David Mahecha")
    p1, p2 = pedido("C1"), pedido("C2")
    entrar("cajero1", "cajero123").post(f'/cajero/cobrar/{p1["id"]}',
                                        data={'metodo': 'Efectivo'}, follow_redirects=True)
    entrar("juandavid", "juan2026").post(f'/cajero/cobrar/{p2["id"]}',
                                         data={'metodo': 'Nequi'}, follow_redirects=True)
    caren = A.resumen_caja(persona="Caren Muñetón")
    juan  = A.resumen_caja(persona="Juan David Mahecha")
    d_caren_ef = caren["efectivo"] - antes_c["efectivo"]
    d_juan_ef  = juan["efectivo"] - antes_j["efectivo"]
    d_juan_tot = juan["total"] - antes_j["total"]
    check(d_caren_ef == 24000, f"lo de Caren en efectivo sube 24.000 (subio ${d_caren_ef:.0f})")
    check(d_juan_ef == 0, "lo de Juan David en efectivo NO sube: cobro por Nequi")
    check(d_juan_tot == 24000, "pero su total si sube 24.000")
    todo = A.resumen_caja()
    check(todo["total"] == caren["total"] + juan["total"],
          "la suma de las personas cuadra con la caja del día")


def test_cerrar_sin_cobrar_queda_registrado():
    print("\n  Cerrar sin cobrar deja constancia, no se falsea un pago")
    p = pedido("CORTESIA")
    c = entrar()
    c.post(f'/cajero/cuenta/{p["id"]}/cerrar_manual',
           data={'motivo': 'cortesía de la casa'}, follow_redirects=True)
    q = A.get_pedido(p["id"])
    check(q["estado_cuenta"] == "Cerrada", "la cuenta queda cerrada")
    check(q["cierre_manual"] is True, "marcada como cierre manual")
    check(len(q["pagos"]) == 0, "SIN inventar un pago que nadie hizo")
    check("cortesía" in q["notas"], "y con el motivo anotado")
    check(A.resumen_caja()["total"] == 0 or p["id"] not in [x["id"] for x in A.listar_pedidos(estado_cuenta="Abierta")],
          "no infla la caja del día")


def test_cortesia_exige_motivo():
    print("\n  Cerrar sin cobrar exige decir por qué")
    p = pedido("SIN-MOTIVO")
    c = entrar()
    c.post(f'/cajero/cuenta/{p["id"]}/cerrar_manual', data={'motivo': ''}, follow_redirects=True)
    check(A.get_pedido(p["id"])["estado_cuenta"] == "Abierta", "sin motivo no se cierra")


if __name__ == "__main__":
    print("=" * 62)
    print("  COBRO DESACOPLADO DE COCINA")
    print("=" * 62)
    for fn in [test_cocina_no_puede_esconder_un_pedido, test_cobrar_cierra_la_cuenta_sola,
               test_pago_parcial, test_no_se_puede_cobrar_de_mas, test_medio_de_pago_invalido,
               test_quien_cobro_queda_registrado, test_cerrar_sin_cobrar_queda_registrado,
               test_cortesia_exige_motivo]:
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
