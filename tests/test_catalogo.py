#!/usr/bin/env python3
"""
Revision del catalogo de nov-2026.

1. El ajuste de arranque da de baja los duplicados (Soda Italiana generica,
   Jugo Natural (agua)), pasa Cola y pola a Cervezas, y corre UNA sola vez.
2. Lo vendido con variante ("— en agua", "— Michelada") descuenta el
   inventario del producto. Antes los jugos casi nunca lo hacian.
3. Una pizza de dos sabores tiene un solo nombre, se toquen en el orden que sea.

    python tests/test_catalogo.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_cat_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar():
    c = A.app.test_client()
    c.post('/login', data={'usuario': 'cajero1', 'password': 'cajero123'},
           follow_redirects=True)
    return c


def fila(nombre):
    with A._conn() as c:
        return c.execute("SELECT activo, categoria FROM catalogo WHERE nombre=?", (nombre,)).fetchone()


def test_base_nueva_sin_duplicados():
    print("\n  Una base nueva no crea la Soda Italiana genérica")
    check(fila("Soda Italiana") is None, "no existe 'Soda Italiana' a secas")
    check(fila("Soda Italiana - Frutos Rojos") is not None, "sí la de Frutos Rojos")
    check(fila("Soda Italiana - Frutos Amarillos") is not None, "y la de Frutos Amarillos")


def test_ajuste_de_arranque():
    print("\n  El ajuste da de baja duplicados y recategoriza, una sola vez")
    with A._conn() as c:
        # Como esta en produccion
        for nombre, tipo, cat in [("Soda Italiana", "bebida_especial", "Gaseosas y aguas"),
                                  ("Jugo Natural (agua)", "bebida_especial", "Jugos y frescos"),
                                  ("Cola y pola", "bebida", "Gaseosas y aguas")]:
            c.execute("INSERT INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo,categoria) "
                      "VALUES (?,?,5000,0,0,1,?)", (nombre, tipo, cat))
        c.execute("DELETE FROM meta WHERE clave='ajuste_catalogo_2026_11'")
    A._ajustar_catalogo_2026_11()
    check(fila("Soda Italiana")["activo"] == 0, "Soda Italiana genérica queda inactiva")
    check(fila("Jugo Natural (agua)")["activo"] == 0, "Jugo Natural (agua) queda inactivo")
    check(fila("Cola y pola")["categoria"] == "Cervezas", "Cola y pola pasa a Cervezas")
    check(fila("Cola y pola")["activo"] == 1, "Cola y pola sigue activa")
    nombres = [n for cat in A.get_bebidas_por_categoria().values() for n in cat]
    check("Soda Italiana" not in nombres and "Jugo Natural (agua)" not in nombres,
          "ya no salen en la pantalla de pedido")
    check("Cola y pola" in A.get_bebidas_por_categoria().get("Cervezas", {}),
          "Cola y pola sale en Cervezas")

    # Si alguien la reactiva desde el Menu, el siguiente arranque no la pisa
    with A._conn() as c:
        c.execute("UPDATE catalogo SET activo=1 WHERE nombre='Soda Italiana'")
    A._ajustar_catalogo_2026_11()
    check(fila("Soda Italiana")["activo"] == 1, "no se vuelve a aplicar en el siguiente arranque")


def test_variantes_descuentan_inventario():
    print("\n  Lo vendido con variante descuenta del producto")
    with A._conn() as c:
        c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo) "
                  "VALUES ('Cerveza budweiser','bebida',5000,1,5,1)")
    casos = [
        ("Jugo Natural — Mora — en agua", "Jugo Natural — Mora"),
        ("Jugo Natural — Mora — en leche", "Jugo Natural — Mora"),
        ("Jugo Natural — Mora", "Jugo Natural — Mora"),
        ("Cerveza budweiser — Michelada", "Cerveza budweiser"),
        ("Cerveza Águila Light — Michelada", "Cerveza Águila Light"),
        ("Soda Italiana — Frutos Rojos", "Soda Italiana - Frutos Rojos"),
        ("Gaseosa — Uva", "Gaseosa"),
    ]
    for vendido, esperado in casos:
        k = A._item_a_stock_key(vendido, "Bebida")
        check(k == esperado, f"{vendido!r} -> {esperado!r} (dio {k!r})")
    check(A._item_a_stock_key("Mojito", "Bebida") is None, "un cóctel sin inventario no descuenta nada")

    A.upsert_inventario("Jugo Natural — Mora", "bebida", 10)
    A.descontar_inventario([{"nombre": "Jugo Natural — Mora — en agua", "tipo": "Bebida", "cantidad": 2},
                            {"nombre": "Jugo Natural — Mora — en leche", "tipo": "Bebida", "cantidad": 1}])
    check(A.get_stock_dict().get("Jugo Natural — Mora") == 7, "3 jugos de mora vendidos: el conteo baja de 10 a 7")


def test_pizza_dos_sabores_un_nombre():
    print("\n  Una pizza de dos sabores tiene un solo nombre")
    check(A.normalizar_pizza("Mexicana / Criolla") == "Criolla / Mexicana", "Mexicana / Criolla -> Criolla / Mexicana")
    check(A.normalizar_pizza("Hawaiana/ Criolla") == "Criolla / Hawaiana", "Hawaiana/ Criolla (de Editar) -> Criolla / Hawaiana")
    check(A.normalizar_pizza("Hawaiana") == "Hawaiana", "una de un sabor no cambia")
    c = entrar()
    r = c.post('/mesero/nuevo', json={"codigo": "CAT-1", "franja": "",
                                      "items": [{"nombre": "Pepperoni / Hawaiana", "tipo": "Pizza",
                                                 "cantidad": 1, "precio_unit": 20000}]})
    d = r.get_json()
    check(r.status_code == 200, "el pedido se registra")
    nombres = [i["nombre"] for i in A.get_pedido(d["id"])["productos"]]
    check(nombres == ["Hawaiana / Pepperoni"], f"queda como 'Hawaiana / Pepperoni' (quedó {nombres})")
    r = c.post(f'/mesero/pedido/{d["id"]}/editar',
               json={"items": [{"nombre": "Pepperoni/ Mexicana", "tipo": "Pizza",
                                "cantidad": 1, "precio_unit": 20000}], "franja": "", "notas": ""})
    nombres = [i["nombre"] for i in A.get_pedido(d["id"])["productos"]]
    check(r.status_code == 200 and nombres == ["Mexicana / Pepperoni"],
          f"desde Editar también (quedó {nombres})")


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
