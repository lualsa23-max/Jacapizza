#!/usr/bin/env python3
"""
Tope de pedidos por hora de entrega (MAX_PEDIDOS_POR_FRANJA = 6).

Lo que se comprueba: que el septimo pedido con pizza a la misma hora se
rechace sin dejar nada a medias (ni pedido ni inventario descontado), que los
de solo bebidas y los anulados no ocupen cupo, y que tampoco se pueda llenar
una hora moviendo pedidos desde Editar o desde el cambio rapido de hora.

    python tests/test_cupo_franja.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_cupo_"), "t.db")

import app as A

_fallos = []
PIZZA   = {"nombre": "Hawaiana", "tipo": "Pizza",  "cantidad": 1, "precio_unit": 20000}
GASEOSA = {"nombre": "Gaseosa",  "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000}
MAX = A.MAX_PEDIDOS_POR_FRANJA


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar():
    c = A.app.test_client()
    c.post('/login', data={'usuario': 'cajero1', 'password': 'cajero123'},
           follow_redirects=True)
    return c


def crear(c, codigo, franja, items=None, **extra):
    datos = {"codigo": codigo, "franja": franja, "items": items or [dict(PIZZA)]}
    datos.update(extra)
    r = c.post('/mesero/nuevo', json=datos)
    return r.status_code, r.get_json()


def masas():
    return A.get_stock_dict().get("Pizza (masa)")


def test_septimo_se_rechaza():
    print(f"\n  A las 7:30 PM caben {MAX} pedidos con pizza; el siguiente no")
    c = entrar()
    for n in range(MAX):
        cod, d = crear(c, f"F730-{n}", "7:30 PM")
        check(cod == 200 and d.get('ok'), f"pedido {n + 1} de {MAX} entra")
    check(A.cupos_franjas()["7:30 PM"] == MAX, f"la franja cuenta {MAX}")
    antes = masas()
    cod, d = crear(c, "F730-X", "7:30 PM")
    check(cod == 409 and d.get('franja_llena') == "7:30 PM", f"el {MAX + 1}º responde 409 'franja llena'")
    check('7:30 PM' in (d.get('error') or ''), "el mensaje dice qué hora está llena")
    check(A.buscar_cuenta_por_codigo("F730-X") is None, "no se creó el pedido")
    check(masas() == antes, "no se descontó inventario")
    cod, d = crear(c, "F745-1", "7:45 PM")
    check(cod == 200, "la hora siguiente sí recibe")


def test_bebidas_y_anulados_no_cuentan():
    print("\n  Solo bebidas y anulados no ocupan cupo")
    c = entrar()
    for n in range(MAX):
        crear(c, f"F800-{n}", "8:00 PM")
    cod, d = crear(c, "F800-BEB", "8:00 PM", items=[dict(GASEOSA)])
    check(cod == 200, "un pedido de solo bebidas entra aunque la hora esté llena")
    check(A.cupos_franjas()["8:00 PM"] == MAX, "y no suma al conteo")
    pid = A.buscar_cuenta_por_codigo("F800-0")["id"]
    with A._conn() as cx:
        cx.execute("UPDATE pedidos SET anulado=1 WHERE id=?", (pid,))
    check(A.cupos_franjas()["8:00 PM"] == MAX - 1, "anular uno libera su cupo")
    cod, d = crear(c, "F800-NUEVO", "8:00 PM")
    check(cod == 200, "y entra otro en su lugar")


def test_sumar_no_cuenta_doble():
    print("\n  Sumar a una cuenta que ya tiene pizza no gasta otro cupo")
    c = entrar()
    for n in range(MAX):
        crear(c, f"F815-{n}", "8:15 PM")
    cod, d = crear(c, "F815-0", "8:15 PM", destino='sumar')
    check(cod == 200 and d.get('sumado'), "la pizza adicional se suma sin problema")


def test_sumar_pizza_a_cuenta_de_bebidas():
    print("\n  Sumar pizza a una cuenta de solo bebidas sí ocupa cupo")
    c = entrar()
    crear(c, "F830-BEB", "8:30 PM", items=[dict(GASEOSA)])
    for n in range(MAX):
        crear(c, f"F830-{n}", "8:30 PM")
    cod, d = crear(c, "F830-BEB", "8:30 PM", destino='sumar')
    check(cod == 409, "con la hora llena, no deja meterle pizza")


def test_mover_a_hora_llena():
    print("\n  No se llena una hora moviendo pedidos")
    c = entrar()
    for n in range(MAX):
        crear(c, f"F845-{n}", "8:45 PM")
    _, d = crear(c, "F900-A", "9:00 PM")
    pid = d['id']
    c.post(f'/pedido/{pid}/hora', data={'franja': '8:45 PM', 'volver': 'mesero_pedidos'})
    check(A.get_pedido(pid)['franja_hora'] == "9:00 PM", "el cambio rápido de hora no lo mueve")
    r = c.post(f'/mesero/pedido/{pid}/editar',
               json={"items": [dict(PIZZA)], "franja": "8:45 PM", "notas": ""})
    check(r.status_code == 409, "Editar tampoco lo deja pasar a la hora llena")
    check(A.get_pedido(pid)['franja_hora'] == "9:00 PM", "y queda donde estaba")
    pid_lleno = A.buscar_cuenta_por_codigo("F845-0")["id"]
    r = c.post(f'/mesero/pedido/{pid_lleno}/editar',
               json={"items": [dict(PIZZA), dict(GASEOSA)], "franja": "8:45 PM", "notas": "x"})
    check(r.status_code == 200, "un pedido que YA está en la hora llena se puede editar")


def test_pantallas_marcan_lleno():
    print("\n  Las pantallas muestran la hora llena")
    c = entrar()
    html = c.get('/mesero/nuevo').get_data(as_text=True)
    check('7:30 PM · LLENO' in html, "Nuevo pedido marca 7:30 PM como LLENO")
    check('7:45 PM · 1/6' in html, "y muestra cuántos lleva la que tiene cupo")
    check(c.get('/api/cupos').get_json().get("7:30 PM") == MAX, "/api/cupos responde")
    check(c.get('/mesero/pedidos').status_code == 200, "Pedidos del día carga")
    check(c.get('/estacion/pizzas').status_code == 200, "Cocina carga")


if __name__ == "__main__":
    for nombre, f in list(globals().items()):
        if nombre.startswith("test_") and callable(f):
            try:
                f()
            except Exception:
                traceback.print_exc()
                _fallos.append(nombre)
    print(f"\n{'TODO BIEN' if not _fallos else f'{len(_fallos)} FALLA(S)'}")
    sys.exit(1 if _fallos else 0)
