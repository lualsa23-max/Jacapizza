#!/usr/bin/env python3
"""
La jornada limpia las pantallas sin perder plata.

El problema original: un pedido que cocina nunca marcó se quedaba en la
pantalla para siempre, mezclado con los de hoy y sin fecha visible. De ahí los
pedidos repetidos y la comida desperdiciada.

Lo delicado es que limpiar la pantalla NO puede hacer desaparecer una cuenta
que todavía debe plata. Eso es lo que más se comprueba aquí.

    python tests/test_jornada.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_jor_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def pedido(codigo, dias_atras=0, items=None):
    """Crea un pedido y lo mueve a la jornada que haga falta."""
    p = A.nuevo_pedido(codigo, "prueba", items or [
        {"nombre": "Hawaiana", "tipo": "Pizza",  "cantidad": 1, "precio_unit": 20000},
        {"nombre": "Gaseosa",  "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000}])
    if dias_atras:
        d = A.ahora() - A.timedelta(days=dias_atras)
        with A._conn() as c:
            c.execute("UPDATE pedidos SET fecha=?, jornada=? WHERE id=?",
                      (d.strftime("%d/%m/%Y"), d.strftime("%Y-%m-%d"), p["id"]))
    return A.get_pedido(p["id"])


def barrer():
    """Fuerza un barrido, como si acabara de cambiar la jornada.

    Hay que borrar la marca de `meta` además del cache del proceso: el barrido
    corre UNA vez por jornada a propósito, así que sin esto el segundo no hace
    nada (que es justo lo que se comprueba en otra prueba más abajo).
    """
    A._ULTIMA_JORNADA_BARRIDA = None
    with A._conn() as c:
        c.execute("DELETE FROM meta WHERE clave='ultima_jornada_barrida'")
    return A.barrer_jornada()


def test_lo_viejo_sale_de_las_pantallas():
    print("\n  Lo de días anteriores sale de cocina y barra")
    viejo = pedido("AYER-1", dias_atras=1)
    hoy   = pedido("HOY-1")
    # Doble red a proposito: la consulta ya filtra por jornada, asi que el de
    # ayer es invisible AUNQUE el barrido no llegue a correr nunca.
    check(not any(k["pedido_id"] == viejo["id"] for k in A.listar_estacion("Pizza")),
          "invisible ya solo por el filtro de jornada, sin depender del barrido")
    barrer()
    pizzas = [k["pedido_id"] for k in A.listar_estacion("Pizza")]
    bebidas = [k["pedido_id"] for k in A.listar_estacion("Bebida")]
    check(viejo["id"] not in pizzas,  "tras el barrido ya NO está en pizzas")
    check(viejo["id"] not in bebidas, "ni en bebidas")
    check(hoy["id"] in pizzas,        "y el de hoy sigue ahí")


def test_lo_viejo_sigue_cobrable():
    print("\n  Pero una cuenta vieja que debe plata NO desaparece")
    viejo = pedido("AYER-DEBE", dias_atras=2)
    barrer()
    q = A.get_pedido(viejo["id"])
    check(q["archivado"] is True, "queda archivado")
    check(q["estado_cuenta"] == "Abierta", "y su cuenta sigue ABIERTA")
    check(q["saldo"] == 24000, f"con el saldo intacto (${q['saldo']:.0f})")
    abiertas = [p["id"] for p in A.listar_pedidos(estado_cuenta="Abierta")]
    check(viejo["id"] in abiertas, "sigue saliendo en la lista de cuentas por cobrar")


def test_lo_no_entregado_queda_marcado():
    print("\n  Lo que nunca salió queda marcado como tal, no como entregado")
    viejo = pedido("AYER-2", dias_atras=1)
    barrer()
    with A._conn() as c:
        vals = [r[0] for r in c.execute("SELECT DISTINCT COALESCE(despachado,0) FROM items "
                                        "WHERE pedido_id=?", (viejo["id"],))]
    check(vals == [2], f"despachado=2 ('cerrado sin entregar'), no 1 (entregado) — {vals}")


def test_barrido_repetido_no_hace_daño():
    print("\n  Barrer dos veces no cambia nada")
    pedido("AYER-3", dias_atras=1)
    barrer()
    with A._conn() as c:
        antes = c.execute("SELECT COUNT(*) FROM pedidos WHERE archivado=1").fetchone()[0]
    n2 = barrer()
    with A._conn() as c:
        despues = c.execute("SELECT COUNT(*) FROM pedidos WHERE archivado=1").fetchone()[0]
    check(antes == despues, f"mismo resultado ({antes})")
    check(n2 == 0, "el segundo barrido no toca nada")


def test_una_comanda_por_ronda():
    print("\n  Cada ronda es su propia comanda")
    p = pedido("RONDAS", items=[{"nombre": "Hawaiana", "tipo": "Pizza",
                                 "cantidad": 2, "precio_unit": 20000}])
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Pizza")
    A.actualizar_pedido(p["id"], [{"nombre": "Mexicana", "tipo": "Pizza",
                                   "cantidad": 1, "precio_unit": 20000}])
    ks = [k for k in A.listar_estacion("Pizza") if k["pedido_id"] == p["id"]]
    check(len(ks) == 1, f"solo la ronda pendiente aparece ({len(ks)} comanda)")
    if ks:
        check(ks[0]["ronda"] == 2, f"marcada como ronda {ks[0]['ronda']}")
        check(len(ks[0]["productos"]) == 1 and ks[0]["productos"][0]["nombre"] == "Mexicana",
              "y solo trae lo nuevo, no lo ya preparado")


def test_entregar_una_ronda_no_toca_la_otra():
    print("\n  Entregar una ronda no arrastra otra ronda pendiente")
    p = pedido("DOS-RONDAS", items=[{"nombre": "Hawaiana", "tipo": "Pizza",
                                     "cantidad": 1, "precio_unit": 20000}])
    A.actualizar_pedido(p["id"], [{"nombre": "Hawaiana", "tipo": "Pizza", "cantidad": 1, "precio_unit": 20000},
                                  {"nombre": "Mexicana", "tipo": "Pizza", "cantidad": 1, "precio_unit": 20000}])
    ks = [k for k in A.listar_estacion("Pizza") if k["pedido_id"] == p["id"]]
    check(len(ks) == 1, "una comanda pendiente")
    with A._conn() as c:
        A.entregar_estacion(c, p["id"], "Pizza", ks[0]["ronda"])
    check(not [k for k in A.listar_estacion("Pizza") if k["pedido_id"] == p["id"]],
          "tras entregarla, el pedido sale de la pantalla")


def test_la_pantalla_de_bebidas_nunca_dibuja_pizzas():
    print("\n  La pantalla de bebidas no puede mostrar una pizza")
    pedido("MIXTO")
    for k in A.listar_estacion("Bebida"):
        tipos = {i["tipo"] for i in k["productos"]}
        check(tipos == {"Bebida"}, f"comanda #{k['pedido_id']} solo trae bebidas ({tipos})")
    for k in A.listar_estacion("Pizza"):
        tipos = {i["tipo"] for i in k["productos"]}
        check(tipos == {"Pizza"}, f"comanda #{k['pedido_id']} solo trae pizzas ({tipos})")


if __name__ == "__main__":
    print("=" * 62)
    print("  JORNADA Y COMANDAS")
    print("=" * 62)
    for fn in [test_lo_viejo_sale_de_las_pantallas, test_lo_viejo_sigue_cobrable,
               test_lo_no_entregado_queda_marcado, test_barrido_repetido_no_hace_daño,
               test_una_comanda_por_ronda, test_entregar_una_ronda_no_toca_la_otra,
               test_la_pantalla_de_bebidas_nunca_dibuja_pizzas]:
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
