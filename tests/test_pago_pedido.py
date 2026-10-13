#!/usr/bin/env python3
"""
Que el cobro al tomar el pedido no se pierda.

Al crear un pedido, cobrar era una casilla escondida al final del scroll; al
editarlo, un aviso "ya fue pagado" salia con cualquier pago parcial; y si el
pago fallaba al guardarse, el pedido quedaba 'Pagado' sin plata en la caja.

    python tests/test_pago_pedido.py
"""
import io, os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_pped_"), "t.db")

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


PIZZA  = {"nombre": "Hawaiana", "tipo": "Pizza",  "cantidad": 1, "precio_unit": 20000}
BEBIDA = {"nombre": "Gaseosa",  "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000}


def crear(c, codigo, items, **extra):
    datos = {"codigo": codigo, "items": items, "franja": A.FRANJAS_HORA[0]}
    datos.update(extra)
    return c.post('/mesero/nuevo', json=datos)


def pagos_de(pid):
    with A._conn() as c:
        return c.execute("SELECT * FROM pagos WHERE pedido_id=?", (pid,)).fetchall()


def test_sin_cobrar_queda_debiendo():
    print("\n  Enviar sin cobrar: queda en Cobrar con todo el saldo")
    r = crear(entrar(), "SIN-1", [PIZZA, BEBIDA])
    d = r.get_json()
    check(r.status_code == 200 and d["ok"], "se crea")
    check(not d["cobrado"] and d["saldo"] == 24000, "no cobra y debe $24.000")
    check(not pagos_de(d["id"]), "no hay pagos")


def test_cobrar_todo_al_crear():
    print("\n  Cobrar y enviar: el pago queda en la tabla de pagos")
    d = crear(entrar(), "COB-1", [PIZZA], cobrar_ya=True, metodo_pago="Nequi").get_json()
    check(d["cobrado"] and d["monto"] == 20000 and d["saldo"] == 0, "cobra los $20.000")
    pg = pagos_de(d["id"])
    check(len(pg) == 1 and pg[0]["metodo"] == "Nequi", "un pago en Nequi")
    check(A.get_pedido(d["id"])["estado_cuenta"] == "Cerrada", "cuenta cerrada")


def test_pago_parcial_al_crear():
    print("\n  Pago parcial al crear: lo que falta sigue en Cobrar")
    d = crear(entrar(), "PAR-1", [PIZZA, BEBIDA], cobrar_ya=True,
              metodo_pago="Efectivo", monto="10.000").get_json()
    check(d["monto"] == 10000 and d["saldo"] == 14000, "paga $10.000, debe $14.000")
    check(A.get_pedido(d["id"])["estado_cuenta"] == "Abierta", "la cuenta sigue abierta")


def test_cobro_invalido_no_crea_pedido():
    print("\n  Un cobro mal armado no deja un pedido a medias")
    c = entrar()
    antes = len(A.listar_pedidos())
    r1 = crear(c, "MAL-1", [PIZZA], cobrar_ya=True, metodo_pago="Bitcoin")
    r2 = crear(c, "MAL-2", [PIZZA], cobrar_ya=True, metodo_pago="Nequi", monto=50000)
    r3 = crear(c, "MAL-3", [PIZZA], cobrar_ya=True, metodo_pago="Nequi", monto="abc")
    check(r1.status_code == 400, "medio desconocido: rechazado")
    check(r2.status_code == 400, "más de lo que vale: rechazado")
    check(r3.status_code == 400, "monto ilegible: rechazado")
    check(len(A.listar_pedidos()) == antes, "no se creó ningún pedido")


def test_comprobante_al_crear():
    print("\n  Foto del comprobante al cobrar desde el pedido")
    c = entrar()
    datos = ('{"codigo":"FOTO-1","items":[{"nombre":"Hawaiana","tipo":"Pizza",'
             '"cantidad":1,"precio_unit":20000}],"cobrar_ya":true,"metodo_pago":"Nequi"}')
    r = c.post('/mesero/nuevo', data={"datos": datos,
               "comprobante": (io.BytesIO(b"\xff\xd8\xff" + b"0" * 100), "c.jpg")},
               content_type="multipart/form-data")
    d = r.get_json()
    check(r.status_code == 200 and d["cobrado"], "se crea y se cobra")
    pg = pagos_de(d["id"])
    check(pg and pg[0]["comprobante"].startswith("pago_"), "el pago tiene su foto")


def test_pago_que_falla_no_queda_como_pagado():
    print("\n  Si el pago falla, NO se marca 'Pagado' en silencio")
    c = entrar()
    original = A.registrar_pago
    def roto(*a, **k):
        raise RuntimeError("disco lleno")
    A.registrar_pago = roto
    try:
        d = crear(c, "FALLA-1", [BEBIDA], cobrar_ya=True, metodo_pago="Efectivo").get_json()
    finally:
        A.registrar_pago = original
    check(bool(d.get("pago_error")), "el mesero recibe el aviso")
    p = A.get_pedido(d["id"])
    check(p["estado"] != "Pagado" and p["saldo"] == 4000, "el pedido sigue debiendo")
    check(not pagos_de(d["id"]), "no hay un pago inventado")


def test_editar_no_dice_pagado_si_debe():
    print("\n  Editar: muestra lo que debe, no 'ya fue pagado'")
    c = entrar()
    d = crear(c, "EDI-1", [PIZZA, BEBIDA], cobrar_ya=True,
              metodo_pago="Efectivo", monto=20000).get_json()
    html = c.get(f'/mesero/pedido/{d["id"]}/editar').data.decode()
    check("ya fue pagado" not in html, "ya no dice 'ya fue pagado'")
    check("debe $4.000" in html.replace("\xa0", " "), "dice cuánto debe")
    check("Llave Bre-B" in html, "los medios salen de METODOS_PAGO")


def test_editar_cobra_lo_nuevo():
    print("\n  Editar y cobrar: cobra el saldo con lo agregado")
    c = entrar()
    d = crear(c, "EDI-2", [BEBIDA], cobrar_ya=True, metodo_pago="Efectivo").get_json()
    pid = d["id"]
    check(A.get_pedido(pid)["saldo"] == 0, "arranca pagado")
    # Se agrega una pizza y se cobra lo que falta
    r = c.post(f'/mesero/pedido/{pid}/editar',
               json={"items": [BEBIDA, PIZZA], "cobrar_ya": True, "metodo_pago": "Tarjeta"})
    e = r.get_json()
    check(e["ok"] and e["monto"] == 20000 and e["saldo"] == 0, "cobra los $20.000 nuevos")
    check(sum(p["monto"] for p in pagos_de(pid)) == 24000, "pagos suman el total")


def test_editar_sin_nada_que_cobrar():
    print("\n  Editar: no deja cobrar si no debe nada")
    c = entrar()
    pid = crear(c, "EDI-3", [BEBIDA], cobrar_ya=True, metodo_pago="Efectivo").get_json()["id"]
    with A._conn() as cx:
        A.entregar_estacion(cx, pid, "Bebida")
    r = c.post(f'/mesero/pedido/{pid}/editar',
               json={"items": [], "cobrar_ya": True, "metodo_pago": "Efectivo"})
    check(r.status_code == 400, "rechazado")
    check(len(pagos_de(pid)) == 1, "no se duplica el pago")


def test_codigo_repetido_pregunta():
    print("\n  D. Código repetido: pregunta en vez de crear otra cuenta")
    c = entrar()
    primero = crear(c, "DUP-1", [BEBIDA]).get_json()
    antes = len(A.listar_pedidos())
    r = crear(c, "dup-1 ", [PIZZA])
    d = r.get_json()
    check(r.status_code == 409 and d["existe"] and d["id"] == primero["id"], "avisa cuál cuenta existe")
    check(len(A.listar_pedidos()) == antes, "no crea nada mientras no se decida")


def test_sumar_a_la_cuenta():
    print("\n  D. Sumar a la cuenta que ya existe")
    c = entrar()
    pid = crear(c, "DUP-2", [BEBIDA]).get_json()["id"]
    d = crear(c, "DUP-2", [PIZZA], destino="sumar", notas="sin cebolla").get_json()
    check(d["ok"] and d["sumado"] and d["id"] == pid, "va a la misma cuenta")
    p = A.get_pedido(pid)
    check(p["total"] == 24000 and p["saldo"] == 24000, "el total acumula $24.000")
    check(p["estado_cocina"] == "Pendiente", "la pizza nueva le llega a cocina")
    check("sin cebolla" in (p["notas"] or ""), "las notas se suman")


def test_sumar_y_cobrar_todo():
    print("\n  D. Sumar y cobrar: se puede cobrar lo anterior + lo nuevo")
    c = entrar()
    pid = crear(c, "DUP-3", [BEBIDA]).get_json()["id"]
    d = crear(c, "DUP-3", [PIZZA], destino="sumar", cobrar_ya=True,
              metodo_pago="Efectivo", monto=24000).get_json()
    check(d["ok"] and d["saldo"] == 0, "cobra los $24.000 de una")
    check(A.get_pedido(pid)["estado_cuenta"] == "Cerrada", "cuenta cerrada")


def test_crear_cuenta_aparte():
    print("\n  D. Crear otra cuenta aparte, si de verdad es otra")
    c = entrar()
    pid = crear(c, "DUP-4", [BEBIDA]).get_json()["id"]
    d = crear(c, "DUP-4", [PIZZA], destino="nueva").get_json()
    check(d["ok"] and not d["sumado"] and d["id"] != pid, "es un pedido nuevo")


def _fecha_jornada():
    from datetime import datetime
    return datetime.strptime(A.jornada_actual(), "%Y-%m-%d").strftime("%d/%m/%Y")


def test_cierre_con_cuentas_abiertas():
    print("\n  E. El cierre no pasa en silencio con cuentas sin cobrar")
    c = entrar()
    crear(c, "CIE-1", [PIZZA])
    f = _fecha_jornada()
    r = c.post('/admin/cierre', json={"fecha": f, "items": []})
    check(r.status_code == 409 and r.get_json()["abiertas"] >= 1, "pide confirmar")
    r = c.post('/admin/cierre', json={"fecha": f, "items": [], "confirmar_abiertas": True})
    check(r.status_code == 200 and r.get_json()["ok"], "confirmado, se guarda")
    with A._conn() as cx:
        m = cx.execute("SELECT valor FROM meta WHERE clave=?",
                       (f"cierre_abiertas:{A.jornada_actual()}",)).fetchone()
    check(m is not None and '"por"' in m["valor"], "queda quién lo confirmó")
    html = c.get('/admin/cierre?fecha=' + f).data.decode()
    check("sin cobrar del" in html and "CIE-1" in html, "la pantalla lista las cuentas")


def test_pagos_sin_foto():
    print("\n  E. Los pagos electrónicos sin foto se cuentan")
    c = entrar()
    crear(c, "FOT-2", [BEBIDA], cobrar_ya=True, metodo_pago="Daviplata")
    r = A.resumen_caja()
    check(any(x["metodo"] == "Daviplata" for x in r["sin_foto"]), "aparece en sin_foto")
    check(not any(x["metodo"] == "Efectivo" for x in r["sin_foto"]), "el efectivo no cuenta")
    html = c.get('/cajero/caja?vista=mia').data.decode()
    check("sin foto — tócalos abajo" in html, "Caja lo muestra arriba")
    check("sin comprobante" in c.get('/dashboard').data.decode(), "y sale en la campana")


if __name__ == "__main__":
    print("=" * 62)
    print("  COBRO AL TOMAR EL PEDIDO")
    print("=" * 62)
    for fn in [test_sin_cobrar_queda_debiendo, test_cobrar_todo_al_crear,
               test_pago_parcial_al_crear, test_cobro_invalido_no_crea_pedido,
               test_comprobante_al_crear, test_pago_que_falla_no_queda_como_pagado,
               test_editar_no_dice_pagado_si_debe, test_editar_cobra_lo_nuevo,
               test_editar_sin_nada_que_cobrar, test_codigo_repetido_pregunta,
               test_sumar_a_la_cuenta, test_sumar_y_cobrar_todo, test_crear_cuenta_aparte,
               test_cierre_con_cuentas_abiertas, test_pagos_sin_foto]:
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
