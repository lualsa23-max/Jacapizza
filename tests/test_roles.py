#!/usr/bin/env python3
"""
Dos roles: Operador y Administrador.

Lo que se comprueba: que nadie del equipo se quede fuera al migrar los usuarios
del diccionario en memoria a la base, que un Operador pueda hacer todo lo
operativo, y que NO pueda llegar a las cifras del negocio ni escribiendo la URL
a mano.

    python tests/test_roles.py
"""
import os, sys, tempfile, traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(prefix="jaca_rol_"), "t.db")

import app as A

_fallos = []

# Lo operativo: lo hace cualquiera del equipo
OPERATIVAS = ['/mesero/nuevo', '/mesero/pedidos', '/cocina/pedidos', '/estacion/pizzas', '/estacion/bebidas',
              '/cajero/cobrar', '/cajero/caja', '/admin/inventario',
              '/admin/menu/pizzas', '/admin/menu/bebidas',
              '/admin/cierre', '/admin/cierre/historial']
# Cifras del negocio: solo administradores
DE_NEGOCIO = ['/admin/reportes', '/admin/resumen', '/admin/usuarios', '/admin/pedidos']


def check(cond, msg):
    print(f"    {'ok  ' if cond else 'FALLA'}  {msg}")
    if not cond:
        _fallos.append(msg)


def entrar(usuario, password):
    c = A.app.test_client()
    r = c.post('/login', data={'usuario': usuario, 'password': password},
               follow_redirects=True)
    with c.session_transaction() as s:
        return c, s.get('rol'), s.get('nombre')


def test_todos_pueden_entrar():
    print("\n  Nadie se queda fuera al migrar a la base")
    esperados = [("admin", "admin123", A.ROL_ADMIN, "Natalia de Sarmiento"),
                 ("luis", "luis2026", A.ROL_ADMIN, "Luis Sarmiento"),
                 ("daniela", "daniela2026", A.ROL_OPERADOR, "Daniela Suárez"),
                 ("cajero1", "cajero123", A.ROL_OPERADOR, "Caren Muñetón"),
                 ("cocina1", "cocina123", A.ROL_OPERADOR, "Chef y Chefa"),
                 ("juandavid", "juan2026", A.ROL_OPERADOR, "Juan David Mahecha")]
    for u, pw, rol, nombre in esperados:
        _, r, n = entrar(u, pw)
        check(r == rol and n == nombre, f"@{u} entra como {rol} ({nombre})")


def test_contrasenas_cifradas():
    print("\n  Las contraseñas ya no estan en texto plano")
    with A._conn() as c:
        filas = c.execute("SELECT usuario,password_hash FROM usuarios").fetchall()
    check(all(len(r["password_hash"]) > 40 for r in filas), "todas guardadas como hash")
    check(not any(r["password_hash"] in ("admin123", "luis2026") for r in filas),
          "ninguna contraseña legible en la base")
    check(entrar("admin", "clave-mala")[1] is None, "una contraseña incorrecta no entra")


def test_daniela_fusionada():
    print("\n  Las dos cuentas de Daniela quedan en una")
    with A._conn() as c:
        n = c.execute("SELECT COUNT(*) FROM usuarios WHERE usuario IN ('daniela','mesero1')").fetchone()[0]
        d = c.execute("SELECT nombre,rol FROM usuarios WHERE usuario='daniela'").fetchone()
    check(n == 1, "queda una sola cuenta, no dos")
    check(d["nombre"] == "Daniela Suárez", "con su nombre real")
    check(d["rol"] == A.ROL_OPERADOR, "y como Operadora, sin acceso al negocio")
    check(entrar("mesero1", "mesero123")[1] is None, "la cuenta vieja ya no entra")


def test_operador_hace_lo_operativo():
    print("\n  Un Operador puede hacer todo lo operativo")
    c, rol, _ = entrar("juandavid", "juan2026")
    malas = [r for r in OPERATIVAS if c.get(r, follow_redirects=True).status_code != 200]
    check(not malas, f"las {len(OPERATIVAS)} pantallas operativas abren" +
          (f" — FALLAN {malas}" if malas else ""))


def test_operador_no_ve_el_negocio():
    print("\n  Un Operador NO llega al negocio, ni escribiendo la URL")
    c, _, _ = entrar("juandavid", "juan2026")
    for ruta in DE_NEGOCIO:
        r = c.get(ruta, follow_redirects=False)
        check(r.status_code in (301, 302), f"{ruta} lo rebota (codigo {r.status_code})")


def test_admin_ve_todo():
    print("\n  Un Administrador si ve el negocio")
    c, _, _ = entrar("admin", "admin123")
    malas = [r for r in OPERATIVAS + DE_NEGOCIO if c.get(r, follow_redirects=True).status_code != 200]
    check(not malas, "todas las pantallas abren" + (f" — FALLAN {malas}" if malas else ""))


def test_cambios_sobreviven_al_reinicio():
    print("\n  Los cambios de usuario sobreviven a un reinicio")
    c, _, _ = entrar("admin", "admin123")
    c.post('/admin/usuarios', data={'action': 'create', 'new_username': 'prueba',
                                    'new_nombre': 'Persona de Prueba',
                                    'new_password': 'clave123',
                                    'new_rol': A.ROL_OPERADOR}, follow_redirects=True)
    with A._conn() as cn:
        existe = cn.execute("SELECT nombre FROM usuarios WHERE usuario='prueba'").fetchone()
    check(existe is not None, "el usuario nuevo esta EN LA BASE, no en memoria")
    check(entrar("prueba", "clave123")[1] == A.ROL_OPERADOR, "y puede entrar")


def test_no_se_puede_quedar_sin_admin():
    print("\n  No se puede dejar el negocio sin administradores")
    c, _, _ = entrar("admin", "admin123")
    with A._conn() as cn:
        cn.execute("UPDATE usuarios SET rol=? WHERE rol=? AND usuario<>'admin'",
                   (A.ROL_OPERADOR, A.ROL_ADMIN))
        uid = cn.execute("SELECT id FROM usuarios WHERE usuario='admin'").fetchone()["id"]
    c.post('/admin/usuarios', data={'action': 'update', 'id': uid, 'nombre': 'Natalia de Sarmiento',
                                    'rol': A.ROL_OPERADOR}, follow_redirects=True)
    with A._conn() as cn:
        rol = cn.execute("SELECT rol FROM usuarios WHERE usuario='admin'").fetchone()["rol"]
    check(rol == A.ROL_ADMIN, "el ultimo administrador no se puede degradar")


if __name__ == "__main__":
    print("=" * 62)
    print("  ROLES: OPERADOR Y ADMINISTRADOR")
    print("=" * 62)
    for fn in [test_todos_pueden_entrar, test_contrasenas_cifradas, test_daniela_fusionada,
               test_operador_hace_lo_operativo, test_operador_no_ve_el_negocio,
               test_admin_ve_todo, test_cambios_sobreviven_al_reinicio,
               test_no_se_puede_quedar_sin_admin]:
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
