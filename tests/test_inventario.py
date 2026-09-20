#!/usr/bin/env python3
"""
Que el inventario no se pueda duplicar.

La causa era una carrera: al arrancar el dia los 2 workers de gunicorn ven el
inventario vacio a la vez y ambos copian el del dia anterior. Sin restriccion
en la base, quedaban dos filas por producto; a partir de ahi cualquier ajuste
manual las hacia divergir y el stock mostrado dejaba de ser fiable.

    python tests/test_inventario.py
"""
import os, sys, tempfile, threading, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_inv_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def sembrar_ayer(n=6):
    """Deja stock del dia anterior, como despues de una noche de trabajo."""
    ayer = (A.ahora() - A.timedelta(days=1)).strftime("%d/%m/%Y")
    with A._conn() as c:
        c.execute("DELETE FROM inventario")
        for nombre, (tipo, alerta) in list(A.INV_DEFAULT.items())[:n]:
            c.execute("INSERT INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) "
                      "VALUES (?,?,?,?,?,?)", (nombre, tipo, 10, 10, alerta, ayer))
    return n


def filas_hoy():
    hoy = A.ahora().strftime("%d/%m/%Y")
    with A._conn() as c:
        return c.execute("SELECT COUNT(*) FROM inventario WHERE fecha=?", (hoy,)).fetchone()[0]


def duplicados_hoy():
    hoy = A.ahora().strftime("%d/%m/%Y")
    with A._conn() as c:
        return c.execute("SELECT COUNT(*) FROM (SELECT nombre FROM inventario "
                         "WHERE fecha=? GROUP BY nombre HAVING COUNT(*)>1)", (hoy,)).fetchone()[0]


def test_carrera_entre_workers():
    print("\n  Dos procesos arrancando el dia a la vez")
    n = sembrar_ayer()
    barrera = threading.Barrier(4)

    def worker():
        barrera.wait()          # los 4 arrancan en el mismo instante
        try:
            A.get_inventario_hoy()
        except Exception as e:
            _fallos.append(f"worker reventó: {type(e).__name__}: {e}")

    hilos = [threading.Thread(target=worker) for _ in range(4)]
    for h in hilos: h.start()
    for h in hilos: h.join()

    check(filas_hoy() == n, f"quedan {filas_hoy()} filas, deben ser {n}")
    check(duplicados_hoy() == 0, "ningun producto duplicado")


def test_ajuste_manual_no_diverge():
    print("\n  Corregir el stock a mano debe verse reflejado")
    sembrar_ayer()
    A.get_inventario_hoy()
    nombre = "Agua 600ml"
    A.upsert_inventario(nombre, "bebida", 50)
    check(A.get_stock_dict().get(nombre) == 50, "tras corregir a 50, el sistema dice 50")
    A.ajustar_stock(nombre, -3)
    check(A.get_stock_dict().get(nombre) == 47, "tras vender 3, dice 47 (no un numero al azar)")
    check(duplicados_hoy() == 0, "y sigue sin duplicados")


def test_upsert_concurrente():
    print("\n  Varios ajustes del mismo producto a la vez")
    sembrar_ayer()
    A.get_inventario_hoy()
    barrera = threading.Barrier(4)

    def worker(v):
        barrera.wait()
        try:
            A.upsert_inventario("Gaseosa", "bebida", v)
        except Exception as e:
            _fallos.append(f"upsert reventó: {type(e).__name__}: {e}")

    hilos = [threading.Thread(target=worker, args=(v,)) for v in (20, 30, 40, 50)]
    for h in hilos: h.start()
    for h in hilos: h.join()
    check(duplicados_hoy() == 0, "sin duplicados aunque escriban a la vez")
    check(A.get_stock_dict().get("Gaseosa") in (20, 30, 40, 50),
          f"el stock es uno de los valores escritos ({A.get_stock_dict().get('Gaseosa')})")


def test_la_base_rechaza_duplicados():
    print("\n  La proteccion vive en la base, no solo en el codigo")
    sembrar_ayer()
    A.get_inventario_hoy()
    hoy = A.ahora().strftime("%d/%m/%Y")
    try:
        with A._conn() as c:
            c.execute("INSERT INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) "
                      "VALUES (?,?,?,?,?,?)", ("Gaseosa", "bebida", 99, 99, 5, hoy))
        check(False, "la base deberia haber rechazado el duplicado")
    except Exception as e:
        check("UNIQUE" in str(e).upper(), f"rechazado por la base ({type(e).__name__})")


if __name__ == "__main__":
    print("=" * 62)
    print("  INVENTARIO SIN DUPLICADOS")
    print("=" * 62)
    for fn in [test_carrera_entre_workers, test_ajuste_manual_no_diverge,
               test_upsert_concurrente, test_la_base_rechaza_duplicados]:
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
