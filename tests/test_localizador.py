#!/usr/bin/env python3
"""
Localizadores de clientes (los aparatos del 1 al 16 que vibran).

Lo que se comprueba: que el numero quede guardado al registrar el pedido, que
salga en la pantalla de la estacion, que un localizador quede OCUPADO mientras
el pedido tenga algo sin entregar y LIBRE apenas se entrega todo, y que nunca
se acepte un numero fuera de 1-16.

    python tests/test_localizador.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_loc_"), "t.db")

import app as A

# Estas pruebas crean muchos pedidos con pizza a la misma hora; el tope por
# franja se prueba aparte, en test_cupo_franja.py.
A.MAX_PEDIDOS_POR_FRANJA = 10**6

_fallos = []
PIZZA   = {"nombre": "Hawaiana", "tipo": "Pizza",  "cantidad": 1, "precio_unit": 20000}
GASEOSA = {"nombre": "Gaseosa",  "tipo": "Bebida", "cantidad": 1, "precio_unit": 4000}


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar():
    c = A.app.test_client()
    c.post('/login', data={'usuario': 'cajero1', 'password': 'cajero123'},
           follow_redirects=True)
    return c


def crear(c, codigo, loc, items=None, **extra):
    datos = {"codigo": codigo, "localizador": loc,
             "items": items or [dict(PIZZA), dict(GASEOSA)]}
    datos.update(extra)
    r = c.post('/mesero/nuevo', json=datos)
    return r.status_code, r.get_json()


def test_leer_localizador():
    print("\n  Solo se aceptan números del 1 al 16")
    check(A.leer_localizador("7") == 7, "'7' -> 7")
    check(A.leer_localizador(16) == 16, "16 -> 16")
    check(A.leer_localizador("") == 0 and A.leer_localizador(None) == 0
          and A.leer_localizador("0") == 0, "vacío / None / 0 -> sin localizador")
    check(A.leer_localizador("17") is None, "17 no es válido")
    check(A.leer_localizador("-1") is None, "-1 no es válido")
    check(A.leer_localizador("abc") is None, "texto no es válido")


def test_se_guarda_y_sale_en_estacion():
    print("\n  Se guarda al registrar y sale en cocina y en bebidas")
    c = entrar()
    cod, d = crear(c, "LOC-A", 7)
    check(cod == 200 and d.get('ok'), "el pedido se registra")
    check(d.get('localizador') == 7, "la respuesta trae el localizador")
    check(A.get_pedido(d['id'])['localizador'] == 7, "queda guardado en el pedido")
    for tipo in ("Pizza", "Bebida"):
        k = [x for x in A.listar_estacion(tipo) if x["pedido_id"] == d['id']]
        check(k and k[0]["localizador"] == 7, f"la comanda de {tipo} muestra el 7")
    html = c.get('/estacion/pizzas').get_data(as_text=True)
    check('Localizador 7' in html, "la pantalla de cocina lo pinta")


def test_numero_invalido_se_rechaza():
    print("\n  Un 17 no se registra")
    c = entrar()
    cod, d = crear(c, "LOC-B", 17)
    check(cod == 400, f"responde 400 (dio {cod})")
    check(A.buscar_cuenta_por_codigo("LOC-B") is None, "no se creó ningún pedido")


def test_ocupado_hasta_entregar_todo():
    print("\n  Ocupado mientras falte algo; libre al entregar todo")
    c = entrar()
    _, d = crear(c, "LOC-C", 3)
    pid = d['id']
    check(A.localizadores_ocupados().get(3) == "LOC-C", "el 3 queda ocupado por LOC-C")
    with A._conn() as cx:
        A.entregar_estacion(cx, pid, "Bebida")
    check(3 in A.localizadores_ocupados(), "entregadas las bebidas: sigue ocupado (faltan pizzas)")
    with A._conn() as cx:
        A.entregar_estacion(cx, pid, "Pizza")
    check(3 not in A.localizadores_ocupados(), "entregado todo: el 3 queda libre")


def test_ocupado_pide_confirmacion():
    print("\n  Un localizador ocupado avisa, y se puede usar igual")
    c = entrar()
    crear(c, "LOC-D", 5)
    cod, d = crear(c, "LOC-E", 5)
    check(cod == 409 and d.get('loc_ocupado') == 5 and d.get('codigo') == "LOC-D",
          "el segundo pedido con el 5 recibe aviso de que lo tiene LOC-D")
    check(A.buscar_cuenta_por_codigo("LOC-E") is None, "y todavía no se crea")
    cod, d = crear(c, "LOC-E", 5, loc_forzar=True)
    check(cod == 200 and d.get('ok'), "confirmado, se registra")
    check(A.get_pedido(d['id'])['localizador'] == 5, "con el 5")


def test_sumar_conserva_localizador():
    print("\n  Sumar a una cuenta: conserva el suyo y no se avisa contra sí misma")
    c = entrar()
    _, d = crear(c, "LOC-F", 9)
    cod, d2 = crear(c, "LOC-F", 0, items=[dict(GASEOSA)], destino='sumar')
    check(cod == 200 and d2.get('sumado'), "se suma a la cuenta")
    check(A.get_pedido(d['id'])['localizador'] == 9, "sin elegir otro, conserva el 9")
    cod, d3 = crear(c, "LOC-F", 9, items=[dict(GASEOSA)], destino='sumar')
    check(cod == 200, "elegir el mismo 9 al sumar no avisa de 'ocupado'")


def test_anulado_libera():
    print("\n  Un pedido anulado no retiene el localizador")
    c = entrar()
    _, d = crear(c, "LOC-G", 12)
    with A._conn() as cx:
        cx.execute("UPDATE pedidos SET anulado=1 WHERE id=?", (d['id'],))
    check(12 not in A.localizadores_ocupados(), "el 12 queda libre")


def test_cambio_rapido():
    print("\n  Cambiar o quitar el localizador desde Pedidos del día")
    c = entrar()
    _, d = crear(c, "LOC-H", 2)
    pid = d['id']
    c.post(f'/pedido/{pid}/localizador', data={'localizador': '14', 'volver': 'mesero_pedidos'})
    check(A.get_pedido(pid)['localizador'] == 14, "cambia al 14")
    c.post(f'/pedido/{pid}/localizador', data={'localizador': '99', 'volver': 'mesero_pedidos'})
    check(A.get_pedido(pid)['localizador'] == 14, "un 99 se rechaza y no lo toca")
    c.post(f'/pedido/{pid}/localizador', data={'localizador': '0', 'volver': 'mesero_pedidos'})
    check(A.get_pedido(pid)['localizador'] == 0, "0 lo quita")
    r = c.post(f'/pedido/{pid}/localizador', data={'localizador': '1', 'volver': 'http://otro.sitio'})
    check('otro.sitio' not in (r.headers.get('Location') or ''), "no redirige a donde diga el formulario")


def test_entregue_dice_a_quien_llamar():
    print("\n  Al tocar ENTREGUÉ aparece a qué localizador llamar")
    c = entrar()
    _, d = crear(c, "LOC-I", 8, items=[dict(PIZZA)])
    k = [x for x in A.listar_estacion("Pizza") if x["pedido_id"] == d['id']][0]
    r = c.post(f'/estacion/pizzas/{d["id"]}/{k["ronda"]}/entregar', follow_redirects=True)
    check('Llama al localizador 8' in r.get_data(as_text=True), "el aviso dice 'Llama al localizador 8'")


def test_pantallas_cargan():
    print("\n  Las pantallas que lo muestran cargan sin error")
    c = entrar()
    for url in ('/mesero/nuevo', '/mesero/pedidos', '/cajero/cobrar',
                '/estacion/pizzas', '/estacion/bebidas', '/api/localizadores'):
        r = c.get(url)
        check(r.status_code == 200, f"{url} -> {r.status_code}")


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
