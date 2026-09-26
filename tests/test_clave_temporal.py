#!/usr/bin/env python3
"""
Contraseña temporal.

Cuando un administrador le pone la contraseña a otra persona, esa contraseña es
temporal: al entrar con ella solo se puede ir a la pantalla de cambiarla, ni
escribiendo la URL a mano. Cuando la cambia, entra normal y la temporal deja de
servir.

    python tests/test_clave_temporal.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_clave_"), "t.db")

import app as A

_fallos = []


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar(usuario, password):
    c = A.app.test_client()
    r = c.post('/login', data={'usuario': usuario, 'password': password})
    return c, r


def id_de(usuario):
    with A._conn() as c:
        return c.execute("SELECT id FROM usuarios WHERE usuario=?", (usuario,)).fetchone()["id"]


def debe_cambiar(usuario):
    with A._conn() as c:
        return c.execute("SELECT debe_cambiar FROM usuarios WHERE usuario=?",
                         (usuario,)).fetchone()["debe_cambiar"]


def test_admin_resetea_y_queda_temporal():
    print("\n  El administrador pone una contraseña: queda temporal")
    admin, _ = entrar("luis", "luis2026")
    admin.post('/admin/usuarios', data={'action': 'update', 'id': id_de('juandavid'),
                                        'rol': A.ROL_OPERADOR, 'password': 'Temporal-1'})
    check(debe_cambiar('juandavid') == 1, "juandavid queda marcado para cambiarla")


def test_con_temporal_solo_puede_cambiarla():
    print("\n  Con la temporal no se puede usar la app")
    c, r = entrar("juandavid", "Temporal-1")
    check(r.status_code == 302 and '/cambiar-clave' in r.location,
          "el login lleva a /cambiar-clave")
    for url in ['/dashboard', '/mesero/nuevo', '/cajero/cobrar']:
        r = c.get(url)
        check(r.status_code == 302 and '/cambiar-clave' in r.location,
              f"{url} redirige a cambiar la contraseña")
    check(c.get('/cambiar-clave').status_code == 200, "la pantalla de cambio abre")


def test_validaciones():
    print("\n  La pantalla de cambio valida lo que se escribe")
    c, _ = entrar("juandavid", "Temporal-1")
    casos = [({'actual': 'mala', 'nueva': 'Nueva-123', 'repite': 'Nueva-123'}, "actual incorrecta"),
             ({'actual': 'Temporal-1', 'nueva': 'abc', 'repite': 'abc'}, "muy corta"),
             ({'actual': 'Temporal-1', 'nueva': 'Nueva-123', 'repite': 'Otra-123'}, "no coinciden"),
             ({'actual': 'Temporal-1', 'nueva': 'Temporal-1', 'repite': 'Temporal-1'}, "igual a la actual")]
    for datos, caso in casos:
        r = c.post('/cambiar-clave', data=datos)
        check(r.status_code == 200 and debe_cambiar('juandavid') == 1, f"rechaza: {caso}")


def test_cambia_y_entra_normal():
    print("\n  Al cambiarla entra normal y la temporal deja de servir")
    c, _ = entrar("juandavid", "Temporal-1")
    r = c.post('/cambiar-clave', data={'actual': 'Temporal-1', 'nueva': 'Nueva-123',
                                        'repite': 'Nueva-123'})
    check(r.status_code == 302 and '/dashboard' in r.location, "tras cambiarla va al inicio")
    check(debe_cambiar('juandavid') == 0, "ya no esta marcado")
    check(c.get('/dashboard').status_code == 200, "puede usar la app en la misma sesion")
    _, r = entrar("juandavid", "Temporal-1")
    check(r.status_code == 200, "la temporal ya no entra")
    _, r = entrar("juandavid", "Nueva-123")
    check(r.status_code == 302 and '/dashboard' in r.location, "la nueva entra directo")


def test_admin_cambia_la_suya_no_es_temporal():
    print("\n  Si el administrador cambia SU contraseña, no es temporal")
    admin, _ = entrar("luis", "luis2026")
    admin.post('/admin/usuarios', data={'action': 'update', 'id': id_de('luis'),
                                        'rol': A.ROL_ADMIN, 'password': 'luis-nueva'})
    check(debe_cambiar('luis') == 0, "luis no queda marcado")


def test_persona_nueva_queda_temporal():
    print("\n  Una persona nueva entra con contraseña temporal")
    admin, _ = entrar("admin", "admin123")
    admin.post('/admin/usuarios', data={'action': 'create', 'new_username': 'nuevo1',
                                        'new_nombre': 'Persona Nueva',
                                        'new_password': 'Inicial-1', 'new_rol': A.ROL_OPERADOR})
    check(debe_cambiar('nuevo1') == 1, "nuevo1 queda marcado")


if __name__ == "__main__":
    print("=" * 62)
    print("  CONTRASEÑA TEMPORAL")
    print("=" * 62)
    for fn in [test_admin_resetea_y_queda_temporal, test_con_temporal_solo_puede_cambiarla,
               test_validaciones, test_cambia_y_entra_normal,
               test_admin_cambia_la_suya_no_es_temporal, test_persona_nueva_queda_temporal]:
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
