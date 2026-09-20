from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response, send_from_directory
import sqlite3, os, csv, io, json, sys, traceback, uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

print(f"[BOOT] Python {sys.version}", flush=True)
print(f"[BOOT] CWD: {os.getcwd()}", flush=True)
print(f"[BOOT] DB_PATH env: {os.environ.get('DB_PATH','not set')}", flush=True)

# ── ZONA HORARIA COLOMBIA (UTC-5) ────────────────────
TZ_COL = timezone(timedelta(hours=-5))

def ahora():
    """Retorna datetime actual en hora Colombia."""
    return datetime.now(TZ_COL)

# La jornada corta a las 5 AM, no a medianoche: el servicio nocturno tiene que
# ser una sola unidad de negocio. Sin esto, una cuenta abierta a las 23:55 se
# archivaria a las 00:01.
JORNADA_CORTE_H = 5

_DIAS  = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
_MESES = ["enero","febrero","marzo","abril","mayo","junio","julio",
          "agosto","septiembre","octubre","noviembre","diciembre"]


def _fecha_larga():
    """'viernes 20 de septiembre'— se lee mejor que 20/09/2026 al entrar."""
    d = ahora()
    return f"{_DIAS[d.weekday()]} {d.day} de {_MESES[d.month - 1]}"


def jornada_actual():
    """Fecha de NEGOCIO en formato ISO ordenable ('YYYY-MM-DD').
    Convive con `fecha` ('%d/%m/%Y'), que se conserva para no mover reportes."""
    return (ahora() - timedelta(hours=JORNADA_CORTE_H)).strftime("%Y-%m-%d")


# ── F2: COMPARACION DE FECHAS ────────────────────────
# El formato 'dd/mm/yyyy'NO se puede ordenar como texto, y el codigo lo venia
# comparando asi. Efecto real medido sobre la base de produccion: el reporte de
# cualquier mes mostraba casi el historico completo (marzo decia $15.383.500
# cuando fueron $1.970.500), porque '21/03/2026'esta entre '01/09/2026'y
# '30/09/2026'letra por letra. Y un rango que cruzaba de mes daba $0.

def fecha_a_iso(fecha_str, por_defecto=None):
    """'dd/mm/yyyy'-> 'yyyy-mm-dd'. None si no se puede interpretar."""
    try:
        d, m, y = str(fecha_str).strip().split("/")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except Exception:
        return por_defecto


def iso_a_fecha(iso):
    """'yyyy-mm-dd' -> 'dd/mm/yyyy'. Los campos de fecha del navegador mandan
    ISO; el resto de la app trabaja en el formato de siempre."""
    try:
        y, m, d = str(iso).strip().split("-")
        return f"{int(d):02d}/{int(m):02d}/{int(y):04d}"
    except Exception:
        return ""


def rango_iso(fecha_ini, fecha_fin):
    """Convierte un rango de la interfaz a ISO.

    Si un extremo viene mal formado se abre ESE extremo, en vez de devolver un
    rango imposible que vaciaria el reporte sin decir por que.
    """
    return (fecha_a_iso(fecha_ini, "0000-01-01"),
            fecha_a_iso(fecha_fin, "9999-12-31"))


def sql_iso(alias=""):
    """Expresion SQL que da la fecha ISO ordenable de una fila.

    Usa `jornada` cuando esta rellena (es la columna indexada) y si no la
    calcula desde `fecha`. Asi el arreglo funciona igual en una base ya migrada
    y en una que todavia no lo esta: no depende del orden del despliegue.
    """
    a = f"{alias}." if alias else ""
    return (f"COALESCE(NULLIF({a}jornada,''), "
            f"substr({a}fecha,7,4)||'-'||substr({a}fecha,4,2)||'-'||substr({a}fecha,1,2))")

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'jacapizza-secret-2024-xK9!')

# ── ANTI-BLOQUEO: headers y robots.txt ──────────────
@app.after_request
def add_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return response

@app.route('/robots.txt')
def robots():
    return Response("User-agent: *\nAllow: /\n", mimetype='text/plain')

DB_PATH = os.environ.get('DB_PATH', '/data/pizza_data.db')
_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try: os.makedirs(_db_dir, exist_ok=True)
    except: DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pizza_data.db')

USUARIOS = {
    "admin":   {"password": "admin123",  "rol": "Administrador", "nombre": "Natalia de Sarmiento"},
    "luis":    {"password": "luis2026",   "rol": "Administrador", "nombre": "Luis Sarmiento"},
    "daniela":    {"password": "daniela2026",   "rol": "Administrador", "nombre": "daniela Admin"},
    "mesero1": {"password": "mesero123", "rol": "Mesero",        "nombre": "Daniela Suárez"},
    "cajero1": {"password": "cajero123", "rol": "Mesero",        "nombre": "Caren Muñetón",
                "roles": ["Mesero", "Cajero"]},
    "cocina1": {"password": "cocina123", "rol": "Cocina",        "nombre": "Chef y Chefa"},
}
FRANJAS_HORA = [
    "7:00 PM","7:15 PM","7:30 PM","7:45 PM",
    "8:00 PM","8:15 PM","8:30 PM","8:45 PM","9:00 PM",
]
BEBIDAS_DEFAULT = {
    "Gaseosa":4000,"Agua 600ml":4000,"Soda Italiana":5000,
    "Cerveza Águila":4000,"Cerveza Águila Light":4000,"Cerveza Coronita":5000,
    "Cerveza Poker":4000,"Limonada de Coco":7000,"Cerezada":7000,
    "Jugo Natural — Guanábana":7000,"Jugo Natural — Mora":7000,
    "Jugo Natural — Lulo":7000,"Jugo Natural — Fresa":7000,
    "Jugo Natural — Tamarindo":7000,"Jugo Natural — Maracumango":7000,
    "Jugo Natural — Maracuyá":7000,"Jugo Natural — Piña":7000,
    "Jugo Natural — Piña-Hierbabuena":7000,
}
PIZZAS_DEFAULT = {
    "Hawaiana":20000,"Pollo con Champiñones":20000,"Mexicana":20000,
    "Pepperoni":20000,"Criolla":20000,"Vegetariana":20000,
}
PRECIO_PIZZA = 20000
TOPPINGS = {
    "Queso extra": 3000,
    "Champiñones": 3000,
    "Pepperoni": 3000,
    "Tocineta": 4000,
    "Maíz": 2000,
    "Jalapeño": 2000,
}
JUGOS_SABORES = [
    "Guanábana","Mora","Lulo","Fresa","Tamarindo",
    "Maracumango","Maracuyá","Piña","Piña-Hierbabuena",
]
INV_DEFAULT = {
    "Pizza (masa)":("pizza",3),"Agua 600ml":("bebida",5),"Gaseosa":("bebida",5),
    "Cerveza Águila":("bebida",5),"Cerveza Águila Light":("bebida",5),
    "Cerveza Coronita":("bebida",5),"Cerveza Poker":("bebida",5),
    "Soda Italiana - Frutos Rojos":("bebida",5),"Soda Italiana - Frutos Amarillos":("bebida",5),
    "Limonada de Coco":("bebida",5),"Cerezada":("bebida",5),
    "Jugo Natural — Guanábana":("bebida",5),"Jugo Natural — Mora":("bebida",5),
    "Jugo Natural — Lulo":("bebida",5),"Jugo Natural — Fresa":("bebida",5),
    "Jugo Natural — Tamarindo":("bebida",5),"Jugo Natural — Maracumango":("bebida",5),
    "Jugo Natural — Maracuyá":("bebida",5),"Jugo Natural — Piña":("bebida",5),
    "Jugo Natural — Piña-Hierbabuena":("bebida",5),
}
# Los 5 medios que ya usan. Estaban repetidos a mano en cada plantilla;
# ahora se definen una vez y se validan en el servidor.
# Categorias de bebida. El orden es el que sale en pantalla: primero lo que
# mas se vende. Se guardan en `catalogo.categoria`, asi que se pueden cambiar
# desde el Menu sin tocar el codigo.
CATEGORIAS_BEBIDA = ["Cervezas", "Jugos y frescos", "Gaseosas y aguas",
                     "Cócteles", "Otros"]


def categoria_sugerida(nombre):
    """Adivina la categoria por el nombre, para no tener que clasificar 27
    productos a mano. Solo se usa la PRIMERA vez; despues manda lo guardado."""
    n = (nombre or "").lower()
    if n.startswith("cerveza"):
        return "Cervezas"
    if n.startswith("jugo") or n.startswith("limonada") or "cerezada" in n:
        return "Jugos y frescos"
    if n.startswith(("gaseosa", "soda", "coca", "agua")):
        return "Gaseosas y aguas"
    if n.startswith("cóctel") or n.startswith("coctel") or "cuba libre" in n \
            or "gin tonic" in n or "cola y pola" in n:
        return "Cócteles"
    return "Otros"


# El valor es el NOMBRE del icono de trazo, no un emoji: los emojis se ven
# distintos en cada aparato y no se pueden teñir del color del contexto.
METODOS_PAGO = {
    "Efectivo":    "cobrar",
    "Tarjeta":     "tarjeta",
    "Nequi":       "movil",
    "Daviplata":   "movil",
    "Llave Bre-B": "llave",
}

CATEGORIAS_GASTO = [
    "Insumos cocina", "Bebidas", "Empaques", "Servicios",
    "Mantenimiento y locativos", "Transporte/mandados", "Jornales", "Otros",
]

@app.template_filter('fromjson')
def fromjson_filter(v):
    try: return json.loads(v)
    except: return {}

@app.context_processor
def _icono():
    """Dibuja un icono del set. Uso: {{ icono('cocina') }} o {{ icono('cobrar', 32) }}

    Los iconos son de un solo trazo y heredan el color del texto (currentColor),
    asi que no hay que declarar una variante por cada contexto.
    """
    from markupsafe import Markup
    def icono(nombre, tam=24, clase=""):
        return Markup(
            f'<svg class="ico {clase}" width="{tam}" height="{tam}" viewBox="0 0 24 24" '
            f'fill="none" stroke="currentColor" stroke-width="1.75" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            f'<use href="#i-{nombre}"/></svg>')
    return {"icono": icono}


@app.template_filter('cop')
def fmt_cop(v):
    try: return f"${float(v):,.0f}".replace(",",".")
    except: return "$0"

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    # Con 2 workers de gunicorn y tablets recargando, 5s se queda corto.
    # busy_timeout hace que SQLite espere en vez de lanzar "database is locked".
    try: c.execute("PRAGMA busy_timeout=15000")
    except: pass
    return c

# ── F0: INFRAESTRUCTURA DE BASE DE DATOS ─────────────
# Nada de esto cambia el comportamiento de la app: solo evita bloqueos
# entre los 2 workers de gunicorn y acelera las consultas existentes.

def _aplicar_pragmas(c):
    """WAL permite leer mientras otro worker escribe. Es persistente en el
    archivo, asi que basta aplicarlo una vez, pero repetirlo es inofensivo.
    Si el sistema de archivos no lo soporta, se queda en el modo por defecto."""
    try:
        modo = c.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        print(f"[BOOT] journal_mode={modo}", flush=True)
    except Exception as e:
        print(f"[BOOT] no se pudo activar WAL ({e}); se sigue en modo por defecto", flush=True)
    try: c.execute("PRAGMA synchronous=NORMAL")
    except: pass


# Indices sobre las columnas por las que ya se filtra hoy.
# Crearlos no altera ningun resultado, solo evita recorrer la tabla entera.
_INDICES = [
    "CREATE INDEX IF NOT EXISTS ix_items_pedido      ON items(pedido_id)",
    "CREATE INDEX IF NOT EXISTS ix_pagos_pedido      ON pagos(pedido_id)",
    "CREATE INDEX IF NOT EXISTS ix_pagos_fecha       ON pagos(fecha)",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_fecha     ON pedidos(fecha)",
    "CREATE INDEX IF NOT EXISTS ix_pedidos_estado    ON pedidos(estado)",
    "CREATE INDEX IF NOT EXISTS ix_inventario_fecha  ON inventario(fecha, nombre)",
    "CREATE INDEX IF NOT EXISTS ix_catalogo_tipo     ON catalogo(tipo, activo)",
    "CREATE INDEX IF NOT EXISTS ix_gastos_fecha      ON gastos(fecha)",
    "CREATE INDEX IF NOT EXISTS ix_cierres_fecha     ON cierres_inventario(fecha)",
]

def _crear_indices(c):
    for sql in _INDICES:
        try: c.execute(sql)
        except Exception as e:
            print(f"[BOOT] indice omitido: {e}", flush=True)
    _indice_unico_inventario(c)


def _indice_unico_inventario(c):
    """Impide fisicamente que un producto aparezca dos veces el mismo dia.

    La causa de la duplicacion es una carrera: al arrancar el dia, los 2 workers
    de gunicorn ven el inventario vacio a la vez y ambos copian el de ayer.
    Con esta restriccion el segundo simplemente no entra (los INSERT usan
    OR IGNORE), en vez de crear una copia.

    Si la base YA tuviera duplicados el indice no se puede crear; en ese caso se
    avisa por consola y se sigue sin el, para no impedir que la app arranque.
    """
    dups = c.execute(
        "SELECT COUNT(*) FROM (SELECT nombre, fecha FROM inventario "
        "GROUP BY nombre, fecha HAVING COUNT(*) > 1)").fetchone()[0]
    if dups:
        print(f"[BOOT] AVISO: {dups} producto(s) duplicados en inventario. "
              f"No se puede activar la proteccion hasta fusionarlos.", flush=True)
        return
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_inventario_dia "
                  "ON inventario(nombre, fecha)")
    except Exception as e:
        print(f"[BOOT] no se pudo crear ux_inventario_dia: {e}", flush=True)


def init_db():
    try:
        print(f"[BOOT] init_db starting, DB_PATH={DB_PATH}", flush=True)
        with _conn() as c:
            c.executescript("""CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL, mesero TEXT NOT NULL,
                estado TEXT DEFAULT 'Pendiente', total REAL DEFAULT 0,
                hora TEXT, fecha TEXT, pago TEXT, modificado INTEGER DEFAULT 0,
                notas TEXT DEFAULT '', franja_hora TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pedido_id INTEGER,
                nombre TEXT, tipo TEXT, cantidad INTEGER, precio_unit REAL
            );
            CREATE TABLE IF NOT EXISTS notificaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER,
                codigo TEXT, detalle TEXT, total REAL, vista INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL,
                tipo TEXT NOT NULL, stock INTEGER DEFAULT 0,
                alerta_min INTEGER DEFAULT 5, fecha TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cierres_inventario (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL, nombre TEXT NOT NULL, tipo TEXT NOT NULL,
                stock_inicial INTEGER DEFAULT 0, vendido INTEGER DEFAULT 0,
                teorico INTEGER DEFAULT 0, real_contado INTEGER DEFAULT 0,
                diferencia INTEGER DEFAULT 0, nota TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS catalogo (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL UNIQUE, tipo TEXT NOT NULL,
                precio REAL DEFAULT 0, en_inventario INTEGER DEFAULT 1,
                alerta_min INTEGER DEFAULT 5, activo INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS pagos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL,
                monto REAL NOT NULL,
                metodo TEXT NOT NULL,
                cobrado_por TEXT NOT NULL,
                fecha TEXT NOT NULL,
                hora TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS entregas_caja (
                jornada TEXT NOT NULL,
                persona TEXT NOT NULL,
                esperado REAL DEFAULT 0,
                entregado REAL DEFAULT 0,
                diferencia REAL DEFAULT 0,
                recibido_por TEXT DEFAULT '',
                fecha TEXT DEFAULT '', hora TEXT DEFAULT '',
                nota TEXT DEFAULT '',
                PRIMARY KEY (jornada, persona)
            );
            CREATE TABLE IF NOT EXISTS meta (
                clave TEXT PRIMARY KEY, valor TEXT
            );
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario TEXT NOT NULL UNIQUE,
                nombre TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'Operador',
                activo INTEGER DEFAULT 1,
                creado TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha TEXT NOT NULL, hora TEXT NOT NULL,
                categoria TEXT NOT NULL, proveedor TEXT DEFAULT '',
                descripcion TEXT DEFAULT '', monto REAL NOT NULL,
                metodo_pago TEXT DEFAULT 'Efectivo',
                factura_path TEXT DEFAULT '',
                registrado_por TEXT NOT NULL
            );
            """)
            for col in ["ALTER TABLE pedidos ADD COLUMN notas TEXT DEFAULT ''",
                        "ALTER TABLE pedidos ADD COLUMN franja_hora TEXT DEFAULT ''",
                        "ALTER TABLE inventario ADD COLUMN stock_inicial INTEGER DEFAULT 0",
                        "ALTER TABLE pedidos ADD COLUMN cobrado_por TEXT DEFAULT ''",
                        "ALTER TABLE items ADD COLUMN despachado INTEGER DEFAULT 0",
                        # ── v2: columnas nuevas ───────────────────────────
                        # Van aqui, y no solo en migrar_v2.py, para que el codigo
                        # se baste solo: una base nueva o una sin migrar todavia
                        # las obtiene al arrancar. migrar_v2.py se encarga ademas
                        # de RELLENARLAS en las filas que ya existen.
                        "ALTER TABLE pedidos ADD COLUMN jornada TEXT DEFAULT ''",
                        "ALTER TABLE pedidos ADD COLUMN estado_cocina TEXT DEFAULT 'Pendiente'",
                        "ALTER TABLE pedidos ADD COLUMN estado_barra TEXT DEFAULT 'Pendiente'",
                        "ALTER TABLE pedidos ADD COLUMN estado_cuenta TEXT DEFAULT 'Abierta'",
                        "ALTER TABLE pedidos ADD COLUMN cierre_manual INTEGER DEFAULT 0",
                        "ALTER TABLE pedidos ADD COLUMN mesa_num TEXT DEFAULT ''",
                        "ALTER TABLE pedidos ADD COLUMN tipo_venta TEXT DEFAULT 'reserva'",
                        "ALTER TABLE pedidos ADD COLUMN archivado INTEGER DEFAULT 0",
                        "ALTER TABLE pedidos ADD COLUMN archivado_por TEXT DEFAULT ''",
                        "ALTER TABLE pedidos ADD COLUMN ultima_ronda INTEGER DEFAULT 1",
                        "ALTER TABLE pedidos ADD COLUMN anulado INTEGER DEFAULT 0",
                        "ALTER TABLE pedidos ADD COLUMN anulado_por TEXT DEFAULT ''",
                        "ALTER TABLE pedidos ADD COLUMN anulado_motivo TEXT DEFAULT ''",
                        "ALTER TABLE items ADD COLUMN ronda INTEGER DEFAULT 1",
                        "ALTER TABLE items ADD COLUMN creado_hora TEXT DEFAULT ''",
                        "ALTER TABLE items ADD COLUMN nota TEXT DEFAULT ''",
                        "ALTER TABLE pagos ADD COLUMN jornada TEXT DEFAULT ''",
                        "ALTER TABLE gastos ADD COLUMN jornada TEXT DEFAULT ''",
                        "ALTER TABLE cierres_inventario ADD COLUMN jornada TEXT DEFAULT ''",
                        "ALTER TABLE catalogo ADD COLUMN categoria TEXT DEFAULT ''"]:
                try: c.execute(col)
                except: pass
            _aplicar_pragmas(c)
            _crear_indices(c)
    except Exception as e:
        print(f"[BOOT] init_db warning: {e}", flush=True)
        traceback.print_exc()

try:
    init_db()
    print("[BOOT] init_db OK", flush=True)
except Exception as e:
    print(f"[BOOT] init_db failed (non-fatal): {e}", flush=True)
    traceback.print_exc()

def _seed_catalogo():
    try:
        with _conn() as c:
            count = c.execute("SELECT COUNT(*) FROM catalogo").fetchone()[0]
            if count == 0:
                for nombre, precio in PIZZAS_DEFAULT.items():
                    c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,?,0,0)",
                             (nombre,"pizza",precio))
                beb_inv = [
                    ("Gaseosa",4000,5),("Agua 600ml",4000,5),
                    ("Cerveza Águila",4000,5),("Cerveza Águila Light",4000,5),
                    ("Cerveza Coronita",5000,5),("Cerveza Poker",4000,5),
                    ("Soda Italiana - Frutos Rojos",5000,5),
                    ("Soda Italiana - Frutos Amarillos",5000,5),
                    ("Limonada de Coco",7000,5),("Cerezada",7000,5),
                ]
                for nombre, precio, alerta in beb_inv:
                    c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,?,1,?)",
                             (nombre,"bebida",precio,alerta))
                for nombre, precio in [("Soda Italiana",5000)]:
                    c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,?,0,0)",
                             (nombre,"bebida_especial",precio))
                c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,0,1,3)",
                         ("Pizza (masa)","pizza_inv"))
            # SIEMPRE sincronizar jugos y bebidas del BEBIDAS_DEFAULT que falten
            existentes = {r["nombre"] for r in c.execute("SELECT nombre FROM catalogo").fetchall()}
            for nombre, precio in BEBIDAS_DEFAULT.items():
                if nombre not in existentes:
                    en_inv = 1 if nombre.startswith("Jugo Natural") or nombre in INV_DEFAULT else 0
                    alerta = INV_DEFAULT.get(nombre, ("bebida", 5))[1] if nombre in INV_DEFAULT else 5
                    c.execute("INSERT INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo) VALUES (?,?,?,?,?,1)",
                             (nombre, "bebida", precio, en_inv, alerta))
                    print(f"[BOOT] Nuevo producto en catálogo: {nombre} (${precio})")
    except Exception as e:
        print("Seed error:", e)

try: _seed_catalogo()
except: pass


def _clasificar_bebidas():
    """Pone categoria a las bebidas que aun no la tienen. Idempotente: solo
    toca las vacias, asi que un cambio hecho a mano no se pisa."""
    try:
        with _conn() as c:
            sin = c.execute("SELECT id,nombre FROM catalogo WHERE tipo LIKE 'bebida%' "
                            "AND COALESCE(categoria,'')=''").fetchall()
            for r in sin:
                c.execute("UPDATE catalogo SET categoria=? WHERE id=?",
                          (categoria_sugerida(r["nombre"]), r["id"]))
            if sin:
                print(f"[BOOT] {len(sin)} bebida(s) clasificadas por categoria", flush=True)
    except Exception as e:
        print(f"[BOOT] _clasificar_bebidas fallo: {e}", flush=True)


try: _clasificar_bebidas()
except: pass


# ── F5: USUARIOS Y ROLES ─────────────────────────────
# Dos roles, no cuatro. Ya no hay "solo mesero" ni "solo caja" ni "solo cocina":
# todo el equipo hace de todo. Lo unico reservado son las cifras del negocio.
ROL_OPERADOR = "Operador"
ROL_ADMIN    = "Administrador"
ROLES = [ROL_OPERADOR, ROL_ADMIN]

# Como quedan los roles viejos al migrar
_ROL_VIEJO_A_NUEVO = {
    "Administrador": ROL_ADMIN,
    "Mesero": ROL_OPERADOR, "Cajero": ROL_OPERADOR, "Cocina": ROL_OPERADOR,
}

# Altas y fusiones pedidas por el dueño. Se aplican UNA VEZ, en la siembra.
_USUARIOS_NUEVOS = [
    # usuario, nombre, password inicial, rol
    ("juandavid", "Juan David Mahecha", "juan2026", ROL_OPERADOR),
]
# La cuenta de la izquierda se descarta; su persona ya esta en la de la derecha.
_FUSIONES = {"mesero1": "daniela"}
# Datos definitivos de las cuentas fusionadas
_AJUSTES = {"daniela": {"nombre": "Daniela Suárez", "rol": ROL_OPERADOR}}


def _seed_usuarios():
    """Pasa los usuarios del diccionario en memoria a la base.

    Hasta ahora `USUARIOS` era un dict de Python que `admin_usuarios` modificaba
    en RAM: los cambios se perdian al reiniciar y no se propagaban entre los 2
    workers de gunicorn. Ademas las contraseñas estaban en texto plano.

    El diccionario se conserva como semilla y respaldo de solo lectura: si algo
    sale mal con la tabla, nadie se queda fuera.
    """
    try:
        with _conn() as c:
            if c.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0]:
                return
            ahora_txt = ahora().strftime("%d/%m/%Y %H:%M")
            for u, d in USUARIOS.items():
                if u in _FUSIONES:
                    continue                      # su persona entra por la otra cuenta
                nombre = d["nombre"]
                rol = _ROL_VIEJO_A_NUEVO.get(d["rol"], ROL_OPERADOR)
                if u in _AJUSTES:
                    nombre = _AJUSTES[u].get("nombre", nombre)
                    rol    = _AJUSTES[u].get("rol", rol)
                c.execute("INSERT OR IGNORE INTO usuarios (usuario,nombre,password_hash,rol,activo,creado) "
                          "VALUES (?,?,?,?,1,?)",
                          (u, nombre, generate_password_hash(d["password"]), rol, ahora_txt))
            for u, nombre, pwd, rol in _USUARIOS_NUEVOS:
                c.execute("INSERT OR IGNORE INTO usuarios (usuario,nombre,password_hash,rol,activo,creado) "
                          "VALUES (?,?,?,?,1,?)",
                          (u, nombre, generate_password_hash(pwd), rol, ahora_txt))
            filas = c.execute("SELECT usuario,rol FROM usuarios ORDER BY rol,usuario").fetchall()
            print(f"[BOOT] usuarios sembrados: " +
                  ", ".join(f"{r['usuario']}({r['rol'][:5]})" for r in filas), flush=True)
    except Exception as e:
        print(f"[BOOT] _seed_usuarios fallo: {e}", flush=True)


try: _seed_usuarios()
except: pass


def get_usuarios():
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,usuario,nombre,rol,activo,creado FROM usuarios "
            "ORDER BY rol DESC, nombre COLLATE NOCASE")]


def verificar_credenciales(usuario, password):
    """Devuelve el usuario si la contraseña es correcta, None si no.

    Si la tabla fallara, se cae al diccionario en memoria para que nadie se
    quede sin poder entrar en mitad de un servicio.
    """
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM usuarios WHERE usuario=? AND activo=1",
                          (usuario,)).fetchone()
        # La consulta funciono: la tabla MANDA. Si aqui no esta, no entra —
        # aunque siga en el diccionario viejo (cuentas fusionadas o dadas de baja).
        if r and check_password_hash(r["password_hash"], password):
            return {"usuario": r["usuario"], "nombre": r["nombre"], "rol": r["rol"]}
        return None
    except Exception as e:
        print(f"[AUTH] tabla usuarios no disponible ({e}); usando respaldo", flush=True)
    d = USUARIOS.get(usuario)
    if d and d["password"] == password:
        return {"usuario": usuario, "nombre": d["nombre"],
                "rol": _ROL_VIEJO_A_NUEVO.get(d["rol"], ROL_OPERADOR)}
    return None

# ── CATALOG HELPERS ──────────────────────────────────
def get_catalogo_bebidas():
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT nombre, precio FROM catalogo WHERE tipo IN ('bebida','bebida_especial') AND activo=1 ORDER BY id"
            ).fetchall()
        r = {row["nombre"]: row["precio"] for row in rows}
        if r: return r
    except: pass
    return dict(BEBIDAS_DEFAULT)

def get_catalogo_pizzas():
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT nombre, precio FROM catalogo WHERE tipo='pizza' AND activo=1 ORDER BY id"
            ).fetchall()
        r = {row["nombre"]: row["precio"] for row in rows}
        if r: return r
    except: pass
    return dict(PIZZAS_DEFAULT)

def get_bebidas_por_categoria():
    """Bebidas agrupadas, en el orden de CATEGORIAS_BEBIDA. Solo devuelve las
    categorias que tienen algo: una seccion vacia es ruido."""
    try:
        with _conn() as c:
            filas = c.execute(
                "SELECT nombre,precio,COALESCE(categoria,'') cat FROM catalogo "
                "WHERE tipo IN ('bebida','bebida_especial') AND activo=1 "
                "ORDER BY nombre COLLATE NOCASE").fetchall()
    except Exception:
        return {"Otros": dict(BEBIDAS_DEFAULT)}
    grupos = {}
    for r in filas:
        cat = r["cat"] or categoria_sugerida(r["nombre"])
        grupos.setdefault(cat, {})[r["nombre"]] = r["precio"]
    orden = [c for c in CATEGORIAS_BEBIDA if c in grupos]
    orden += [c for c in grupos if c not in CATEGORIAS_BEBIDA]
    return {c: grupos[c] for c in orden}


def get_catalogo_admin(familia):
    """Filas completas del catalogo para la pantalla de menu.

    A diferencia de get_catalogo_pizzas/bebidas —que devuelven {nombre: precio}
    para el carrito— aqui hace falta el `id`, porque toda edicion se hace por id.
    'bebidas'incluye el tipo historico 'bebida_especial', que antes se mostraba
    pero no se podia editar.
    """
    tipos = ("pizza",) if familia == "pizzas" else ("bebida", "bebida_especial")
    ph = ",".join("?" * len(tipos))
    with _conn() as c:
        rows = c.execute(
            f"SELECT id,nombre,tipo,precio,en_inventario,alerta_min,"
            f"COALESCE(categoria,'') categoria FROM catalogo "
            f"WHERE tipo IN ({ph}) AND activo=1 ORDER BY nombre COLLATE NOCASE", tipos).fetchall()
    return [dict(r) for r in rows]


def get_inv_estandar():
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT nombre, tipo, alerta_min FROM catalogo WHERE en_inventario=1 AND activo=1"
            ).fetchall()
        result = {}
        for r in rows:
            t = "pizza" if r["tipo"] == "pizza_inv" else "bebida"
            result[r["nombre"]] = (t, r["alerta_min"])
        if result: return result
    except: pass
    return dict(INV_DEFAULT)

# ── PEDIDOS ───────────────────────────────────────────
def _pagos_de_filas(rows):
    return [{"id": r["id"], "monto": r["monto"], "metodo": r["metodo"],
             "cobrado_por": r["cobrado_por"], "fecha": r["fecha"], "hora": r["hora"]} for r in rows]


def _get_pagos(c, pid):
    try:
        return _pagos_de_filas(
            c.execute("SELECT * FROM pagos WHERE pedido_id=? ORDER BY id", (pid,)).fetchall())
    except:
        return []

# ── F1: ESTADOS DERIVADOS ────────────────────────────
# Tres pistas independientes, ninguna escrita a mano por una persona:
#   estado_cocina  <- items tipo Pizza sin despachar
#   estado_barra   <- items tipo Bebida sin despachar
#   estado_cuenta  <- si los pagos cubren el total (o hubo cierre manual)

ESTACIONES = {"Pizza": "estado_cocina", "Bebida": "estado_barra"}


def _sync_estado(c, pid):
    """Recalcula las columnas derivadas de un pedido. Idempotente.

    NO escribe la columna `estado` de siempre. En esta fase las pantallas
    siguen leyendola, y derivarla ya cambiaria el comportamiento: un pedido
    con pizzas pagado por adelantado pasaria a 'Pagado'y la cocina dejaria
    de verlo. El cambio de lectura llega con las pantallas nuevas.
    """
    row = c.execute("SELECT total, cierre_manual FROM pedidos WHERE id=?", (pid,)).fetchone()
    if not row:
        return None

    total  = c.execute("SELECT COALESCE(SUM(cantidad*precio_unit),0) FROM items "
                       "WHERE pedido_id=?", (pid,)).fetchone()[0]
    pagado = c.execute("SELECT COALESCE(SUM(monto),0) FROM pagos "
                       "WHERE pedido_id=?", (pid,)).fetchone()[0]

    # Que estacion tiene todavia algo por entregar
    pendiente = {r[0] for r in c.execute(
        "SELECT DISTINCT tipo FROM items WHERE pedido_id=? "
        "AND (despachado=0 OR despachado IS NULL)", (pid,))}
    cocina = "Pendiente" if "Pizza"  in pendiente else "Entregado"
    barra  = "Pendiente" if "Bebida" in pendiente else "Entregado"

    try: manual = row["cierre_manual"]
    except (KeyError, IndexError): manual = 0
    # Tolerancia de 1 peso: `total` es REAL y el peso colombiano no tiene
    # centavos, asi que un saldo de 0.0000001 no puede dejar una cuenta
    # abierta para siempre.
    cuenta = "Cerrada" if (manual or (total > 0 and pagado >= total - 1)) else "Abierta"

    c.execute("UPDATE pedidos SET total=?, estado_cocina=?, estado_barra=?, estado_cuenta=? "
              "WHERE id=?", (total, cocina, barra, cuenta, pid))
    return {"total": total, "pagado": pagado, "estado_cocina": cocina,
            "estado_barra": barra, "estado_cuenta": cuenta}


# ── F7: JORNADA Y ESTACIONES ─────────────────────────

_ULTIMA_JORNADA_BARRIDA = None   # por proceso; evita tocar la base en cada peticion


def barrer_jornada():
    """Archiva lo que quedo sin entregar de jornadas anteriores.

    Sin esto, un pedido que cocina nunca marco se quedaba en la pantalla para
    siempre, mezclado con los de hoy y sin fecha visible — de ahi los pedidos
    repetidos y la comida desperdiciada.

    Archivar es SOLO respecto a las pantallas de estacion: no se toca nunca
    `estado_cuenta`, asi que una cuenta vieja que deba plata sigue apareciendo
    en cobro. Nada se borra.

    No hay cron: esto se dispara solo, perezosamente, la primera vez que alguien
    abre una pantalla despues de cambiar la jornada.
    """
    global _ULTIMA_JORNADA_BARRIDA
    j = jornada_actual()
    if _ULTIMA_JORNADA_BARRIDA == j:
        return 0                      # camino rapido: una comparacion de textos

    tocados = 0
    with _conn() as c:
        # BEGIN IMMEDIATE serializa a los 2 workers de gunicorn: el que pierde
        # encuentra la marca y no hace nada.
        c.execute("BEGIN IMMEDIATE")
        try:
            marca = c.execute("SELECT valor FROM meta WHERE clave='ultima_jornada_barrida'").fetchone()
            if marca and marca[0] == j:
                c.execute("ROLLBACK")
                _ULTIMA_JORNADA_BARRIDA = j
                return 0
            viejos = f"(SELECT id FROM pedidos WHERE COALESCE(archivado,0)=0 AND {sql_iso()} < ?)"
            # despachado=2 = "cerrado por jornada sin entregar". No es lo mismo
            # que entregado (1), y asi queda constancia de lo que nunca salio.
            c.execute(f"UPDATE items SET despachado=2 WHERE despachado=0 AND pedido_id IN {viejos}", (j,))
            cur = c.execute(f"UPDATE pedidos SET archivado=1, archivado_por='jornada' "
                            f"WHERE COALESCE(archivado,0)=0 AND {sql_iso()} < ?", (j,))
            tocados = cur.rowcount
            c.execute("INSERT OR REPLACE INTO meta (clave,valor) VALUES ('ultima_jornada_barrida',?)", (j,))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
    _ULTIMA_JORNADA_BARRIDA = j
    if tocados:
        print(f"[JORNADA] {j}: {tocados} pedido(s) de dias anteriores archivados", flush=True)
    return tocados


_ENDPOINTS_BARRIDO = {"estacion_pizzas", "estacion_bebidas", "cocina_pedidos",
                      "cajero_cobrar", "mesero_nuevo", "mesero_pedidos", "admin_resumen"}


@app.before_request
def _guardia_jornada():
    """Dispara el barrido al entrar a una pantalla operativa. Si falla, se
    anota y el usuario ni se entera: nunca debe tumbar una peticion."""
    if request.endpoint in _ENDPOINTS_BARRIDO:
        try:
            barrer_jornada()
        except Exception as e:
            print(f"[JORNADA] el barrido fallo: {e}", flush=True)


def listar_estacion(tipo, jornada=None):
    """Comandas pendientes de UNA estacion, agrupadas por ronda.

    Cada comanda es una RONDA, no un pedido: es el modelo mental de una cocina
    (un ticket = una tanda). Asi una segunda ronda no arrastra lo ya preparado.
    """
    if tipo not in ESTACIONES:
        raise ValueError(f"Estacion desconocida: {tipo!r}")
    j = jornada or jornada_actual()
    with _conn() as c:
        filas = c.execute(
            f"""SELECT * FROM pedidos
                WHERE COALESCE(anulado,0)=0 AND COALESCE(archivado,0)=0
                  AND {sql_iso()}=?
                  AND EXISTS (SELECT 1 FROM items WHERE pedido_id=pedidos.id
                              AND tipo=? AND COALESCE(despachado,0)=0)
                ORDER BY id""", (j, tipo)).fetchall()
        if not filas:
            return []
        ids = [r["id"] for r in filas]
        por_pedido = {}
        for bloque in _en_bloques(ids):
            ph = ",".join("?" * len(bloque))
            for r in c.execute(f"SELECT * FROM items WHERE pedido_id IN ({ph}) ORDER BY id", bloque):
                por_pedido.setdefault(r["pedido_id"], []).append(r)

    comandas = []
    ahora_min = ahora().hour * 60 + ahora().minute
    for r in filas:
        todos = _items_de_filas(por_pedido.get(r["id"], []))
        mios = [i for i in todos if i["tipo"] == tipo and not i["despachado"]]
        if not mios:
            continue
        otra = [i for i in todos if i["tipo"] != tipo and not i["despachado"]]
        # Una comanda por ronda pendiente
        for ronda in sorted({i["ronda"] for i in mios}):
            de_ronda = [i for i in mios if i["ronda"] == ronda]
            hora_txt = de_ronda[0]["creado_hora"] or r["hora"] or ""
            try:
                hh, mm = hora_txt.split(":")
                minutos = max(0, ahora_min - (int(hh) * 60 + int(mm)))
            except Exception:
                minutos = 0
            comandas.append({
                "pedido_id": r["id"],
                "codigo": r["codigo"],
                "mesa_num": _col_de(r, "mesa_num", ""),
                "mesero": r["mesero"],
                "hora": hora_txt,
                "fecha": r["fecha"],
                "franja_hora": _col_de(r, "franja_hora", ""),
                "notas": _col_de(r, "notas", ""),
                "ronda": ronda,
                # OJO: no llamar a esta clave "items". En Jinja `k.items` resuelve
                # al metodo .items() del diccionario, no a la clave, y la pantalla
                # revienta. "productos" es ademas el nombre que ya usa el resto.
                "productos": de_ronda,
                "otra_estacion": otra,
                "minutos": minutos,
                "urgencia": "roja" if minutos >= 20 else ("naranja" if minutos >= 10 else "verde"),
            })
    comandas.sort(key=lambda x: -x["minutos"])
    return comandas


def _col_de(row, nombre, defecto):
    try:
        v = row[nombre]
        return defecto if v is None else v
    except (KeyError, IndexError):
        return defecto


def agregar_items_entregados(c, pid, items):
    """Añade items a un pedido marcandolos YA entregados, en una ronda nueva.

    Es para la venta rapida: la bebida se saca de la nevera y se entrega en el
    acto. A diferencia de actualizar_pedido(), esto NO toca nada de lo que ya
    estaba pendiente — ni lo reinserta ni lo renumera. Asi una pizza que cocina
    todavia no ha hecho se queda exactamente como estaba.
    """
    ronda = (c.execute("SELECT COALESCE(MAX(ronda),0) FROM items WHERE pedido_id=?",
                       (pid,)).fetchone()[0] or 0) + 1
    hora = ahora().strftime("%H:%M")
    for i in items:
        c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit,"
                  "despachado,ronda,creado_hora) VALUES (?,?,?,?,?,1,?,?)",
                  (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"], ronda, hora))
    c.execute("UPDATE pedidos SET ultima_ronda=? WHERE id=?", (ronda, pid))
    return _sync_estado(c, pid)


def entregar_estacion(c, pid, tipo, ronda=None):
    """Marca como entregados SOLO los items de UNA estacion.

    Esta es la unica funcion autorizada a escribir `despachado`. El `AND tipo=?`
    es obligatorio y `tipo` viene de un enum cerrado: es lo que impide que
    marcar las bebidas arrastre las pizzas. La funcion vieja `marcar_listo`
    hacia `UPDATE items SET despachado=1 WHERE pedido_id=?`, sin filtrar.
    """
    if tipo not in ESTACIONES:
        raise ValueError(f"Estacion desconocida: {tipo!r}. Validas: {sorted(ESTACIONES)}")
    sql = ("UPDATE items SET despachado=1 WHERE pedido_id=? AND tipo=? "
           "AND (despachado=0 OR despachado IS NULL)")
    args = [pid, tipo]
    if ronda is not None:
        sql += " AND ronda=?"
        args.append(ronda)
    c.execute(sql, args)
    return _sync_estado(c, pid)


def _pedido_from_row(row, prods, pagos_list):
    total_pagado = sum(p["monto"] for p in pagos_list)
    total = row["total"]
    saldo = max(0, total - total_pagado)
    # Acceso defensivo a columnas que pueden no existir en BDs antiguas
    try: cobrado_por = row["cobrado_por"] or ""
    except: cobrado_por = ""
    try: notas = row["notas"] or ""
    except: notas = ""
    try: franja_hora = row["franja_hora"] or ""
    except: franja_hora = ""
    # Columnas de la v2. Acceso defensivo igual que las de arriba: si la base
    # todavia no esta migrada, la app sigue funcionando con los valores por defecto.
    def _col(nombre, defecto):
        try:
            v = row[nombre]
            return defecto if v is None else v
        except (KeyError, IndexError):
            return defecto

    return {
        "id": row["id"], "mesa": row["codigo"], "mesero": row["mesero"],
        "jornada":       _col("jornada", ""),
        "estado_cocina": _col("estado_cocina", "Pendiente"),
        "estado_barra":  _col("estado_barra", "Pendiente"),
        "estado_cuenta": _col("estado_cuenta", "Abierta"),
        "cierre_manual": bool(_col("cierre_manual", 0)),
        "mesa_num":      _col("mesa_num", ""),
        "tipo_venta":    _col("tipo_venta", "reserva"),
        "archivado":     bool(_col("archivado", 0)),
        "anulado":       bool(_col("anulado", 0)),
        "ultima_ronda":  _col("ultima_ronda", 1),
        "estado": row["estado"], "total": total, "hora": row["hora"],
        "fecha": row["fecha"], "pago": row["pago"], "modificado": bool(row["modificado"]),
        "notas": notas, "franja_hora": franja_hora,
        "cobrado_por": cobrado_por, "productos": prods,
        "pagos": pagos_list, "total_pagado": total_pagado, "saldo": saldo,
    }

def _items_de_filas(rows):
    result = []
    for r in rows:
        try:
            desp = r["despachado"]
        except (KeyError, IndexError):
            desp = 0
        try:
            ronda = r["ronda"] or 1
        except (KeyError, IndexError):
            ronda = 1
        try:
            creado_hora = r["creado_hora"] or ""
        except (KeyError, IndexError):
            creado_hora = ""
        result.append({
            "id": r["id"],
            "nombre": r["nombre"], "tipo": r["tipo"],
            "cantidad": r["cantidad"], "precio_unit": r["precio_unit"],
            "despachado": bool(desp),
            "ronda": ronda, "creado_hora": creado_hora
        })
    return result


def _get_items(c, pid):
    return _items_de_filas(
        c.execute("SELECT * FROM items WHERE pedido_id=? ORDER BY id", (pid,)).fetchall())

def _en_bloques(ids, tam=400):
    """SQLite limita cuantos parametros admite una consulta, asi que los IN(...)
    se parten en bloques."""
    for i in range(0, len(ids), tam):
        yield ids[i:i + tam]


def listar_pedidos(jornada=None, estado=None, estado_cuenta=None,
                   archivado=None, anulado=0, limit=None):
    """Carga pedidos con 3 consultas fijas en vez de 1+2N.

    Antes: una consulta para los pedidos y DOS MAS POR CADA UNO (items y pagos).
    Con 358 pedidos eran 717 consultas por pantalla, y la tablet de cocina las
    repetia cada 30 segundos. Ahora son tres: pedidos, todos sus items de golpe,
    todos sus pagos de golpe; el armado se hace en Python.
    """
    cond, args = [], []
    if jornada is not None:
        cond.append(f"{sql_iso()}=?"); args.append(jornada)
    if estado is not None:
        cond.append("estado=?"); args.append(estado)
    if estado_cuenta is not None:
        cond.append("estado_cuenta=?"); args.append(estado_cuenta)
    if archivado is not None:
        cond.append("COALESCE(archivado,0)=?"); args.append(int(archivado))
    if anulado is not None:
        cond.append("COALESCE(anulado,0)=?"); args.append(int(anulado))

    sql = "SELECT * FROM pedidos"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"

    with _conn() as c:
        try:
            filas = c.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            # Base sin migrar todavia: reintentar sin las columnas nuevas.
            filas = c.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()

        ids = [r["id"] for r in filas]
        items, pagos = {}, {}
        for bloque in _en_bloques(ids):
            ph = ",".join("?" * len(bloque))
            for r in c.execute(f"SELECT * FROM items WHERE pedido_id IN ({ph}) ORDER BY id", bloque):
                items.setdefault(r["pedido_id"], []).append(r)
            try:
                for r in c.execute(f"SELECT * FROM pagos WHERE pedido_id IN ({ph}) ORDER BY id", bloque):
                    pagos.setdefault(r["pedido_id"], []).append(r)
            except sqlite3.OperationalError:
                pass  # base antigua sin tabla `pagos`

        return [_pedido_from_row(r, _items_de_filas(items.get(r["id"], [])),
                                 _pagos_de_filas(pagos.get(r["id"], []))) for r in filas]


def get_pedidos():
    """Todos los pedidos. Se conserva para no tocar las pantallas actuales;
    las nuevas usan listar_pedidos() con filtros."""
    return listar_pedidos(anulado=None)

def get_pedido(pid):
    with _conn() as c:
        row = c.execute("SELECT * FROM pedidos WHERE id=?", (pid,)).fetchone()
        if not row: return None
        return _pedido_from_row(row, _get_items(c, pid), _get_pagos(c, pid))

def nuevo_pedido(mesa, mesero, items, notas="", franja_hora=""):
    total = sum(i["cantidad"] * i["precio_unit"] for i in items)
    hora  = ahora().strftime("%H:%M")
    fecha = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO pedidos (codigo,mesero,estado,total,hora,fecha,notas,franja_hora,jornada) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (mesa, mesero, "Pendiente", total, hora, fecha, notas, franja_hora, jornada_actual()))
        pid = cur.lastrowid
        for i in items:
            c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit,ronda,creado_hora) "
                      "VALUES (?,?,?,?,?,1,?)",
                      (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"], hora))
        _sync_estado(c, pid)
    return get_pedido(pid)

def registrar_pago(pid, monto, metodo, cobrado_por, marcar_pagado=True):
    """Registra un pago. Si marcar_pagado=False, guarda el pago pero no cambia el estado."""
    fecha = ahora().strftime("%d/%m/%Y")
    hora  = ahora().strftime("%H:%M")
    try:
        with _conn() as c:
            c.execute("INSERT INTO pagos (pedido_id,monto,metodo,cobrado_por,fecha,hora,jornada) "
                      "VALUES (?,?,?,?,?,?,?)",
                      (pid, monto, metodo, cobrado_por, fecha, hora, jornada_actual()))
            if marcar_pagado:
                total_pedido = c.execute("SELECT total FROM pedidos WHERE id=?", (pid,)).fetchone()["total"]
                total_pagado = c.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE pedido_id=?", (pid,)).fetchone()[0]
                if total_pagado >= total_pedido:
                    c.execute("UPDATE pedidos SET estado='Pagado', pago=?, cobrado_por=? WHERE id=?",
                              (metodo, cobrado_por, pid))
            else:
                # Solo guardar método de pago en el pedido, mantener estado actual
                c.execute("UPDATE pedidos SET pago=?, cobrado_por=? WHERE id=?",
                          (metodo, cobrado_por, pid))
            _sync_estado(c, pid)
    except:
        with _conn() as c:
            if marcar_pagado:
                c.execute("UPDATE pedidos SET estado='Pagado', pago=?, cobrado_por=? WHERE id=?",
                          (metodo, cobrado_por, pid))
            else:
                c.execute("UPDATE pedidos SET pago=?, cobrado_por=? WHERE id=?",
                          (metodo, cobrado_por, pid))

def cobrar_pedido(pid, metodo, cobrado_por=""):
    """Cobra el saldo pendiente del pedido."""
    pedido = get_pedido(pid)
    if not pedido: return
    saldo = pedido["saldo"]
    if saldo <= 0: saldo = pedido["total"]  # fallback si no hay pagos previos
    registrar_pago(pid, saldo, metodo, cobrado_por)

def actualizar_pedido(pid, items, notas=None, franja_hora=None):
    """Actualiza el pedido preservando items ya despachados.
    Los 'items'recibidos son los NO despachados (lo que el mesero puede editar).
    Los items ya despachados se mantienen intactos en BD.
    El total del pedido = suma de items despachados + items nuevos."""
    with _conn() as c:
        # Borrar solo los items NO despachados (los nuevos/editables)
        try:
            c.execute("DELETE FROM items WHERE pedido_id=? AND (despachado=0 OR despachado IS NULL)", (pid,))
        except:
            c.execute("DELETE FROM items WHERE pedido_id=?", (pid,))
        # Los items nuevos entran en la ronda siguiente a la ya despachada, para
        # que las estaciones distingan "esto es adicional" de lo ya preparado.
        ronda = (c.execute("SELECT COALESCE(MAX(ronda),0) FROM items WHERE pedido_id=? "
                           "AND despachado=1", (pid,)).fetchone()[0] or 0) + 1
        hora_now = ahora().strftime("%H:%M")
        # Insertar los items nuevos (no despachados por defecto)
        for i in items:
            try:
                c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit,despachado,ronda,creado_hora) "
                          "VALUES (?,?,?,?,?,0,?,?)",
                          (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"], ronda, hora_now))
            except:
                c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit) VALUES (?,?,?,?,?)",
                          (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"]))
        # Recalcular total con TODOS los items (despachados + nuevos)
        total = c.execute("SELECT COALESCE(SUM(cantidad*precio_unit),0) FROM items WHERE pedido_id=?", (pid,)).fetchone()[0]
        if notas is not None and franja_hora is not None:
            c.execute("UPDATE pedidos SET total=?,modificado=1,notas=?,franja_hora=? WHERE id=?",
                      (total, notas, franja_hora, pid))
        else:
            c.execute("UPDATE pedidos SET total=?,modificado=1 WHERE id=?", (total, pid))
        c.execute("UPDATE pedidos SET ultima_ronda=COALESCE((SELECT MAX(ronda) FROM items "
                  "WHERE pedido_id=?),1) WHERE id=?", (pid, pid))
        _sync_estado(c, pid)

def marcar_listo(pid):
    with _conn() as c:
        # Marcar todos los items no despachados como despachados (histórico)
        # Antes: UPDATE items SET despachado=1 WHERE pedido_id=?  (sin filtrar por
        # tipo). Ahora pasa por entregar_estacion, que obliga a nombrar la estacion.
        # El efecto es el mismo porque se llama a las dos, pero ya no queda ninguna
        # ruta capaz de marcar ambas sin decirlo.
        try:
            for _est in ESTACIONES:
                entregar_estacion(c, pid, _est)
        except Exception as e:
            print(f"[WARN] entregar_estacion fallo en #{pid}: {e}", flush=True)
        pedido = c.execute("SELECT pago, total FROM pedidos WHERE id=?", (pid,)).fetchone()
        if pedido and pedido["pago"]:
            # Ya tiene pago registrado → pasar directo a Pagado (era cobro inmediato)
            total_pagado = c.execute(
                "SELECT COALESCE(SUM(monto),0) FROM pagos WHERE pedido_id=?", (pid,)).fetchone()[0]
            if total_pagado >= pedido["total"]:
                c.execute("UPDATE pedidos SET estado='Pagado' WHERE id=?", (pid,))
                return
        c.execute("UPDATE pedidos SET estado='Listo' WHERE id=?", (pid,))

def add_notificacion(pid, codigo, detalle, total):
    with _conn() as c:
        c.execute("INSERT INTO notificaciones (pid,codigo,detalle,total) VALUES (?,?,?,?)",
                  (pid, codigo, detalle, total))

def get_notificaciones_nuevas():
    with _conn() as c:
        rows = c.execute("SELECT * FROM notificaciones WHERE vista=0").fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            c.execute(f"UPDATE notificaciones SET vista=1 WHERE id IN ({','.join('?'*len(ids))})", ids)
        return [{"pid": r["pid"], "codigo": r["codigo"], "detalle": r["detalle"], "total": r["total"]} for r in rows]

# ── INVENTARIO ────────────────────────────────────────
def _producto_base(nombre, catalogo):
    """Quita la variante del nombre para poder agrupar.

    'Jugo Natural — Maracuyá — en agua'  ->  'Jugo Natural — Maracuyá'
    'Cerveza Poker — Michelada'          ->  'Cerveza Poker'

    Se resuelve contra el catalogo (el prefijo mas largo que sea un producto
    real) en vez de cortar por el separador: hay productos que YA llevan un
    guion en su nombre, como los jugos.
    """
    if nombre in catalogo:
        return nombre
    mejor = None
    for prod in catalogo:
        if nombre.startswith(prod) and (mejor is None or len(prod) > len(mejor)):
            mejor = prod
    return mejor or nombre.split(" — ")[0]


def get_reporte(fecha_ini, fecha_fin):
    """Tablero de ventas de un rango.

    Tres cosas que antes estaban mal:
    1. El dinero por canal salia de `pedidos.pago`, que guarda el ULTIMO metodo
       usado. Con un pago dividido (mitad efectivo, mitad Nequi) atribuia el
       total a uno solo. Ahora sale de la tabla `pagos`, que es la verdad.
    2. Los productos se agrupaban por el nombre crudo, asi que 'Criolla /
       Mexicana' y 'Hawaiana / Criolla' eran filas distintas y los sabores no
       se veian por ningun lado.
    3. No habia cuenta de pizzas ni de bebidas.
    """
    fi, ff = rango_iso(fecha_ini, fecha_fin)
    with _conn() as c:
        pagados = c.execute(
            f"SELECT * FROM pedidos WHERE estado='Pagado' AND {sql_iso()} BETWEEN ? AND ?",
            (fi, ff)).fetchall()
        ids = [r["id"] for r in pagados]

        # ── Dinero: de la tabla de pagos, no de la columna del pedido ──
        por_metodo, por_cobrador = {}, {}
        try:
            for r in c.execute(
                    f"SELECT metodo, cobrado_por, monto FROM pagos "
                    f"WHERE {sql_iso()} BETWEEN ? AND ?", (fi, ff)):
                por_metodo[r["metodo"]] = por_metodo.get(r["metodo"], 0) + r["monto"]
                por_cobrador[r["cobrado_por"]] = por_cobrador.get(r["cobrado_por"], 0) + r["monto"]
        except Exception:
            pass

        # ── Productos ──────────────────────────────────────────────────
        filas = []
        if ids:
            for bloque in _en_bloques(ids):
                ph = ",".join("?" * len(bloque))
                filas += c.execute(
                    f"SELECT nombre, tipo, cantidad, precio_unit FROM items "
                    f"WHERE pedido_id IN ({ph})", bloque).fetchall()

    catalogo = set(get_catalogo_bebidas()) | set(get_catalogo_pizzas())

    n_pizzas = n_bebidas = val_pizzas = val_bebidas = 0
    n_un_sabor = n_dos_sabores = 0
    un_sabor, dos_sabores, bebidas = {}, {}, {}
    for r in filas:
        val = r["cantidad"] * r["precio_unit"]
        if r["tipo"] == "Pizza":
            n_pizzas += r["cantidad"]
            val_pizzas += val
            # Una pizza de dos sabores es UNA pizza, no dos. Se separan las de
            # un sabor de las de dos, que es lo que de verdad dice que se pide.
            partes = [x.strip() for x in r["nombre"].split("/") if x.strip()]
            if len(partes) > 1:
                n_dos_sabores += r["cantidad"]
                # 'Criolla / Mexicana' y 'Mexicana / Criolla' son la MISMA
                # pizza: se ordenan los sabores para que no salgan en dos filas.
                combo = " / ".join(sorted(partes))
                dos_sabores[combo] = dos_sabores.get(combo, 0) + r["cantidad"]
            else:
                n_un_sabor += r["cantidad"]
                s = partes[0] if partes else r["nombre"]
                un_sabor[s] = un_sabor.get(s, 0) + r["cantidad"]
        else:
            n_bebidas += r["cantidad"]
            val_bebidas += val
            base = _producto_base(r["nombre"], catalogo)
            d = bebidas.setdefault(base, {"cantidad": 0, "valor": 0})
            d["cantidad"] += r["cantidad"]
            d["valor"] += val

    total_ventas = sum(r["total"] for r in pagados)
    total_cobrado = sum(por_metodo.values())
    por_dia = {}
    for r in pagados:
        por_dia[r["fecha"]] = por_dia.get(r["fecha"], 0) + r["total"]

    return {
        "total_ventas": total_ventas,
        "total_cobrado": total_cobrado,
        "n_pedidos": len(pagados),
        "ticket_prom": total_ventas / len(pagados) if pagados else 0,
        "n_pizzas": n_pizzas, "n_bebidas": n_bebidas,
        "n_un_sabor": n_un_sabor, "n_dos_sabores": n_dos_sabores,
        "val_pizzas": val_pizzas, "val_bebidas": val_bebidas,
        "por_metodo": dict(sorted(por_metodo.items(), key=lambda x: -x[1])),
        "por_cobrador": dict(sorted(por_cobrador.items(), key=lambda x: -x[1])),
        "un_sabor": sorted(({"nombre": k, "cantidad": v} for k, v in un_sabor.items()),
                           key=lambda x: -x["cantidad"]),
        "dos_sabores": sorted(({"nombre": k, "cantidad": v} for k, v in dos_sabores.items()),
                              key=lambda x: -x["cantidad"]),
        "bebidas": sorted(({"nombre": k, **v} for k, v in bebidas.items()),
                          key=lambda x: -x["cantidad"]),
        "por_dia": dict(sorted(por_dia.items(),
                               key=lambda x: fecha_a_iso(x[0], "0000-00-00"))),
        # Compatibilidad con el CSV y pantallas viejas
        "top_items": sorted(({"nombre": k, "tipo": "Bebida", "cantidad": v["cantidad"],
                              "valor": v["valor"]} for k, v in bebidas.items()),
                            key=lambda x: -x["cantidad"]),
    }


def get_inventario_hoy():
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT * FROM inventario WHERE fecha=? ORDER BY tipo,nombre", (hoy,)).fetchall()
        if rows:
            return [{"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"],
                     "stock": r["stock"], "stock_inicial": r["stock_inicial"] if "stock_inicial" in r.keys() else 0,
                     "alerta_min": r["alerta_min"]} for r in rows]
        # No hay inventario hoy → copiar el stock final del último día registrado
        ultimo_dia = c.execute(
            "SELECT DISTINCT fecha FROM inventario WHERE fecha!=? ORDER BY rowid DESC LIMIT 1",
            (hoy,)).fetchone()
        if not ultimo_dia:
            return []
        fecha_ant = ultimo_dia["fecha"]
        rows_ant = c.execute(
            "SELECT nombre, tipo, stock, alerta_min FROM inventario WHERE fecha=? AND tipo!='pulpa'",
            (fecha_ant,)).fetchall()
        # Crear registros de hoy con el stock final de ayer.
        # OR IGNORE + indice unico: si el otro worker se adelanto, este no
        # duplica, simplemente no inserta.
        for r in rows_ant:
            c.execute(
                "INSERT OR IGNORE INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) "
                "VALUES (?,?,?,?,?,?)",
                (r["nombre"], r["tipo"], r["stock"], r["stock"], r["alerta_min"], hoy))
        # Leer los recién creados
        rows = c.execute("SELECT * FROM inventario WHERE fecha=? ORDER BY tipo,nombre", (hoy,)).fetchall()
        return [{"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"],
                 "stock": r["stock"], "stock_inicial": r["stock_inicial"] if "stock_inicial" in r.keys() else 0,
                 "alerta_min": r["alerta_min"]} for r in rows]

def get_stock_dict():
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT nombre, stock FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        if not rows:
            # Forzar arrastre del día anterior
            get_inventario_hoy()
            rows = c.execute("SELECT nombre, stock FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        return {r["nombre"]: r["stock"] for r in rows}

def get_stock_con_alertas():
    """Retorna dict: {nombre: {'stock': N, 'alerta_min': M, 'tipo': T}} para el día de hoy."""
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT nombre, tipo, stock, alerta_min FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        if not rows:
            get_inventario_hoy()
            rows = c.execute("SELECT nombre, tipo, stock, alerta_min FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        return {r["nombre"]: {"stock": r["stock"], "alerta_min": r["alerta_min"], "tipo": r["tipo"]} for r in rows}

def get_productos_stock_bajo():
    """Retorna lista de productos con stock <= alerta_min (excluyendo pulpas que son del día)."""
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute(
            "SELECT nombre, tipo, stock, alerta_min FROM inventario "
            "WHERE fecha=? AND alerta_min > 0 AND stock <= alerta_min "
            "ORDER BY stock ASC, nombre ASC", (hoy,)).fetchall()
        return [{"nombre": r["nombre"], "tipo": r["tipo"], "stock": r["stock"],
                 "alerta_min": r["alerta_min"]} for r in rows]

def validar_stock_pedido(items):
    """Valida que haya stock suficiente para TODOS los items del pedido.
    Retorna (ok: bool, error_msg: str). Suma cantidades por producto (puede haber duplicados)."""
    stock = get_stock_dict()
    # Agrupar cantidades por key de stock
    requerido = {}
    for item in items:
        key = _item_a_stock_key(item["nombre"], item["tipo"])
        if key:
            requerido[key] = requerido.get(key, 0) + item["cantidad"]
    # Validar cada requerimiento contra stock disponible
    for key, cantidad in requerido.items():
        disponible = stock.get(key)
        if disponible is not None and cantidad > disponible:
            return False, f'Solo quedan {disponible} de "{key}". Pediste {cantidad}.'
    return True, ""

def upsert_inventario(nombre, tipo, stock, alerta_min=None):
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        ex = c.execute("SELECT id,alerta_min,stock_inicial FROM inventario WHERE nombre=? AND fecha=?", (nombre, hoy)).fetchone()
        if ex:
            amin = alerta_min if alerta_min is not None else ex["alerta_min"]
            # Si stock_inicial era 0 (nunca se cargó), actualizarlo también
            si = ex["stock_inicial"] if ex["stock_inicial"] > 0 else stock
            c.execute("UPDATE inventario SET stock=?,alerta_min=?,stock_inicial=? WHERE id=?",
                      (max(0, stock), amin, si, ex["id"]))
        else:
            amin = alerta_min if alerta_min is not None else 5
            # OR IGNORE por la misma carrera: entre el SELECT de arriba y este
            # INSERT, el otro worker pudo haber creado la fila.
            c.execute("INSERT OR IGNORE INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) "
                      "VALUES (?,?,?,?,?,?)",
                      (nombre, tipo, max(0, stock), max(0, stock), amin, hoy))
            # Si la perdio, ajustar la fila que si entro, para no perder el dato.
            c.execute("UPDATE inventario SET stock=?,alerta_min=? WHERE nombre=? AND fecha=?",
                      (max(0, stock), amin, nombre, hoy))

def ajustar_stock(nombre, delta):
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        c.execute("UPDATE inventario SET stock=MAX(0,stock+?) WHERE nombre=? AND fecha=?", (delta, nombre, hoy))

def _item_a_stock_key(nombre, tipo):
    if tipo == "Pizza": return "Pizza (masa)"
    if nombre.startswith("Soda Italiana"):
        if "Frutos Rojos" in nombre:     return "Soda Italiana - Frutos Rojos"
        if "Frutos Amarillos" in nombre: return "Soda Italiana - Frutos Amarillos"
    for key in ["Gaseosa","Agua 600ml","Cerveza Águila Light","Cerveza Águila","Cerveza Coronita",
                "Limonada de Coco","Cerezada","Cerveza Poker"]:
        if nombre.startswith(key): return key
    # Dynamic items from catalogo
    try:
        with _conn() as c:
            row = c.execute("SELECT nombre FROM catalogo WHERE en_inventario=1 AND nombre=?", (nombre,)).fetchone()
            if row: return row["nombre"]
    except: pass
    if nombre.startswith("Jugo Natural"):
        partes = nombre.split(" — ", 1)
        if len(partes) > 1: return partes[1]
    return None

def descontar_inventario(items):
    for item in items:
        key = _item_a_stock_key(item["nombre"], item["tipo"])
        if key: ajustar_stock(key, -item["cantidad"])

def restaurar_inventario(items):
    for item in items:
        key = _item_a_stock_key(item["nombre"], item["tipo"])
        if key: ajustar_stock(key, +item["cantidad"])

def get_pulpas_hoy():
    """DEPRECATED: Los jugos ahora son bebidas normales del catálogo. Retorna lista vacía por compatibilidad."""
    return []

# ── CIERRE ────────────────────────────────────────────
def get_vendido_hoy(fecha):
    vendido = {}
    with _conn() as c:
        rows = c.execute(
            "SELECT i.nombre, i.tipo, SUM(i.cantidad) as total "
            "FROM items i JOIN pedidos p ON p.id=i.pedido_id "
            "WHERE p.estado='Pagado' AND p.fecha=? "
            "GROUP BY i.nombre, i.tipo", (fecha,)).fetchall()
    for r in rows:
        vendido[r["nombre"]] = {"cantidad": r["total"], "tipo": r["tipo"]}
    return vendido

def get_cierre_fechas():
    with _conn() as c:
        rows = c.execute(
            f"SELECT DISTINCT fecha FROM cierres_inventario ORDER BY {sql_iso()} DESC").fetchall()
        return [r["fecha"] for r in rows]

# ── GASTOS ────────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(DB_PATH) if os.path.dirname(DB_PATH) else '.', 'facturas')
try: os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except:
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'facturas')
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_FACTURA_EXT = {'png','jpg','jpeg','pdf','webp','heic'}
def _allowed_factura(fn):
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_FACTURA_EXT

def _fecha_a_iso(fecha_str):
    """Alias historico de fecha_a_iso(). Se mantiene el '9999-99-99'de antes
    para no cambiar el comportamiento de quien todavia lo llame."""
    return fecha_a_iso(fecha_str, "9999-99-99")

def crear_gasto(categoria, proveedor, descripcion, monto, metodo_pago, registrado_por, factura_path=""):
    fecha = ahora().strftime("%d/%m/%Y")
    hora  = ahora().strftime("%H:%M")
    with _conn() as c:
        c.execute("INSERT INTO gastos (fecha,hora,categoria,proveedor,descripcion,monto,metodo_pago,factura_path,registrado_por) VALUES (?,?,?,?,?,?,?,?,?)",
                  (fecha, hora, categoria, proveedor, descripcion, monto, metodo_pago, factura_path, registrado_por))

def get_gastos(fecha_filtro=None):
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        if fecha_filtro == 'todos':
            rows = c.execute("SELECT * FROM gastos ORDER BY id DESC").fetchall()
        else:
            filtro = fecha_filtro or hoy
            rows = c.execute("SELECT * FROM gastos WHERE fecha=? ORDER BY id DESC", (filtro,)).fetchall()
        return [dict(r) for r in rows]

def get_gastos_rango(fecha_ini, fecha_fin):
    # Antes traia la tabla entera a memoria y filtraba en Python. Ahora filtra
    # en SQL con la misma expresion ISO que el resto de los reportes.
    with _conn() as c:
        rows = c.execute(
            f"SELECT * FROM gastos WHERE {sql_iso()} BETWEEN ? AND ? ORDER BY {sql_iso()}, id",
            rango_iso(fecha_ini, fecha_fin)).fetchall()
    return [dict(r) for r in rows]

def get_total_gastos_hoy():
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(monto),0) as total FROM gastos WHERE fecha=?", (hoy,)).fetchone()
        return row["total"] if row else 0

def eliminar_gasto(gid):
    with _conn() as c:
        row = c.execute("SELECT factura_path FROM gastos WHERE id=?", (gid,)).fetchone()
        if row and row["factura_path"]:
            try:
                fp = os.path.join(UPLOAD_FOLDER, os.path.basename(row["factura_path"]))
                if os.path.exists(fp): os.remove(fp)
            except: pass
        c.execute("DELETE FROM gastos WHERE id=?", (gid,))

# ── AUTH ──────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    """Solo administradores: reportes, cierre, gastos y usuarios.
    Es lo unico reservado; todo lo operativo lo hace cualquiera del equipo."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        if session.get('rol') != ROL_ADMIN:
            flash('Esa sección es solo para administradores', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


def rol_required(*roles):
    """Compatibilidad con los roles viejos.

    Ya no existen 'Mesero', 'Cajero'ni 'Cocina': cualquiera del equipo entra.
    Solo se sigue filtrando 'Administrador'.
    """
    if ROL_ADMIN in roles:
        return admin_required
    return login_required

def solo_luis(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario' not in session: return redirect(url_for('login'))
        if session.get('usuario') != 'luis':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ── ROUTES ────────────────────────────────────────────
@app.route('/')
def index():
    if 'usuario' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    error = None
    if request.method == 'POST':
        u = request.form.get('usuario','').strip()
        pw = request.form.get('password','').strip()
        datos = verificar_credenciales(u, pw)
        if datos:
            session.clear()
            session['usuario'] = datos['usuario']
            session['nombre']  = datos['nombre']
            session['rol']     = datos['rol']
            return redirect(url_for('dashboard'))
        error = "Usuario o contraseña incorrectos"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

def construir_avisos():
    """Lo que necesita atención ahora mismo, para la campana de arriba.

    Antes esto ocupaba dos tarjetas en el inicio. Sacarlo a la campana libera
    esa pantalla y hace que los avisos se vean desde CUALQUIER sitio, no solo
    al entrar.
    """
    avisos = []
    try:
        abiertas = [x for x in listar_pedidos(estado_cuenta="Abierta") if x["total"] > 0]
    except Exception:
        abiertas = []
    j = jornada_actual()
    viejas = [x for x in abiertas if x["jornada"] and x["jornada"] < j]
    hoy_ab = [x for x in abiertas if x not in viejas]
    if viejas:
        avisos.append({"tipo": "urgente", "icono": "cobrar",
                       "titulo": f"{len(viejas)} cuenta{'s' if len(viejas) != 1 else ''} de días anteriores",
                       "detalle": f"{fmt_cop(sum(x['saldo'] for x in viejas))} sin cobrar",
                       "url": url_for('cajero_cobrar', filtro='anteriores')})
    if hoy_ab:
        avisos.append({"tipo": "aviso", "icono": "cobrar",
                       "titulo": f"{len(hoy_ab)} cuenta{'s' if len(hoy_ab) != 1 else ''} abierta{'s' if len(hoy_ab) != 1 else ''} hoy",
                       "detalle": f"{fmt_cop(sum(x['saldo'] for x in hoy_ab))} por cobrar",
                       "url": url_for('cajero_cobrar')})
    try:
        bajo = get_productos_stock_bajo()
    except Exception:
        bajo = []
    if bajo:
        avisos.append({"tipo": "aviso", "icono": "inventario",
                       "titulo": f"{len(bajo)} producto{'s' if len(bajo) != 1 else ''} por acabarse",
                       "detalle": ", ".join(b["nombre"] for b in bajo[:3])
                                  + (f" y {len(bajo) - 3} más" if len(bajo) > 3 else ""),
                       "url": url_for('admin_inventario')})
    try:
        k = len(listar_estacion("Pizza")) + len(listar_estacion("Bebida"))
    except Exception:
        k = 0
    if k:
        avisos.append({"tipo": "info", "icono": "cocina",
                       "titulo": f"{k} comanda{'s' if k != 1 else ''} en cocina",
                       "detalle": "por preparar", "url": url_for('estacion_pizzas')})
    return avisos


@app.context_processor
def _datos_barra():
    """Lo que la barra inferior necesita en cualquier pantalla."""
    if 'usuario' not in session:
        return {}
    try:
        n = len([p for p in listar_pedidos(estado_cuenta="Abierta") if p["total"] > 0])
    except Exception:
        n = 0
    try:
        k = len(listar_estacion("Pizza")) + len(listar_estacion("Bebida"))
    except Exception:
        k = 0
    try:
        avisos = construir_avisos()
    except Exception:
        avisos = []
    return {"nav_por_cobrar": n, "nav_cocina": k, "avisos": avisos,
            "n_urgentes": sum(1 for a in avisos if a["tipo"] == "urgente")}


@app.route('/dashboard')
@login_required
def dashboard():
    """Pantalla de inicio: lo mas usado a un toque, y lo que necesita atencion.

    No es solo un menu. Arriba avisa de lo que esta pendiente -- cuentas sin
    cobrar, productos por acabarse -- para que se vea al entrar, sin tener que
    ir a buscarlo.
    """
    j = jornada_actual()
    try:
        abiertas = [p for p in listar_pedidos(estado_cuenta="Abierta") if p["total"] > 0]
    except Exception:
        abiertas = []
    try:
        cocina = len(listar_estacion("Pizza")) + len(listar_estacion("Bebida"))
    except Exception:
        cocina = 0
    try:
        bajo = len(get_productos_stock_bajo())
    except Exception:
        bajo = 0
    try:
        efectivo = resumen_caja(j, session['nombre'])["efectivo"]
    except Exception:
        efectivo = 0
    return render_template('home.html',
        hoy=_fecha_larga(), cocina=cocina, stock_bajo=bajo,
        por_cobrar=len(abiertas), deuda=sum(p["saldo"] for p in abiertas),
        mi_efectivo=efectivo,
        es_admin=(session.get('rol') == ROL_ADMIN))


# ── ADMIN ─────────────────────────────────────────────
@app.route('/admin/resumen')
@rol_required('Administrador')
def admin_resumen():
    hoy   = ahora().strftime("%d/%m/%Y")
    todos = get_pedidos()
    hoy_todos    = [p for p in todos if p["fecha"]==hoy]
    hoy_pagados  = [p for p in hoy_todos if p["estado"]=="Pagado"]
    pendientes   = sum(1 for p in hoy_todos if p["estado"]=="Pendiente")
    listos       = sum(1 for p in hoy_todos if p["estado"]=="Listo")
    cobros_pendientes = sum(1 for p in todos if p["estado"]=="Listo" and p["fecha"]!=hoy)

    # Pizzas vendidas hoy (de pedidos pagados)
    pizzas_vendidas = 0
    with _conn() as c:
        row = c.execute(
            "SELECT COALESCE(SUM(i.cantidad),0) as total FROM items i "
            "JOIN pedidos p ON p.id=i.pedido_id "
            "WHERE i.tipo='Pizza' AND p.fecha=? AND p.estado='Pagado'", (hoy,)).fetchone()
        pizzas_vendidas = row["total"] if row else 0

    # Masas disponibles
    stock = get_stock_dict()
    masas_disponibles = stock.get("Pizza (masa)", 0)
    masas_iniciales = 0
    try:
        with _conn() as c:
            row = c.execute("SELECT stock_inicial FROM inventario WHERE nombre='Pizza (masa)' AND fecha=?", (hoy,)).fetchone()
            if row: masas_iniciales = row["stock_inicial"] if row["stock_inicial"] > 0 else 0
    except: pass

    # Pagos del día desde tabla pagos
    pagos_hoy = []
    try:
        with _conn() as c:
            pagos_hoy = c.execute("SELECT * FROM pagos WHERE fecha=?", (hoy,)).fetchall()
    except: pass
    total_cobrado  = sum(r["monto"] for r in pagos_hoy)
    total_cobros   = len(pagos_hoy)
    metodos_hoy = {}
    for r in pagos_hoy:
        metodos_hoy[r["metodo"]] = metodos_hoy.get(r["metodo"], 0) + r["monto"]
    por_cobrador = {}
    for r in pagos_hoy:
        por_cobrador[r["cobrado_por"]] = por_cobrador.get(r["cobrado_por"], 0) + r["monto"]

    # Fallback: si no hay pagos en tabla pagos, usar total de pedidos pagados (BD antigua)
    if total_cobrado == 0 and hoy_pagados:
        total_cobrado = sum(p["total"] for p in hoy_pagados)
        total_cobros = len(hoy_pagados)

    # Total pendiente por cobrar (pedidos Listo de hoy)
    total_por_cobrar = sum(p["saldo"] for p in hoy_todos if p["estado"]=="Listo" and p["saldo"]>0)

    # Top productos vendidos hoy (de pedidos pagados)
    top_items = []
    with _conn() as c:
        pag_ids = [p["id"] for p in hoy_pagados]
        if pag_ids:
            ph = ",".join("?" * len(pag_ids))
            rows = c.execute(
                f"SELECT nombre, tipo, SUM(cantidad) as tc, SUM(cantidad*precio_unit) as tv "
                f"FROM items WHERE pedido_id IN ({ph}) GROUP BY nombre ORDER BY tc DESC LIMIT 10", pag_ids).fetchall()
            top_items = [{"nombre": r["nombre"], "tipo": r["tipo"], "cantidad": r["tc"], "valor": r["tv"]} for r in rows]

    # Productos con stock bajo (alerta para el admin)
    stock_bajo = get_productos_stock_bajo()

    # Gastos del día (solo para luis)
    es_luis = session.get('usuario') == 'luis'
    total_gastos_hoy = get_total_gastos_hoy() if es_luis else 0
    utilidad_hoy = total_cobrado - total_gastos_hoy if es_luis else 0

    return render_template('admin_resumen.html',
        hoy=hoy, total_pedidos_hoy=len(hoy_todos),
        pagados=len(hoy_pagados), pendientes=pendientes, listos=listos,
        cobros_pendientes=cobros_pendientes,
        pizzas_vendidas=pizzas_vendidas, masas_disponibles=masas_disponibles,
        masas_iniciales=masas_iniciales,
        total_cobrado=total_cobrado, total_cobros=total_cobros,
        total_por_cobrar=total_por_cobrar,
        metodos_hoy=metodos_hoy, por_cobrador=por_cobrador,
        top_items=top_items, ultimos=hoy_todos[:10],
        stock_bajo=stock_bajo,
        es_luis=es_luis, total_gastos_hoy=total_gastos_hoy, utilidad_hoy=utilidad_hoy)

@app.route('/admin/inventario', methods=['GET','POST'])
@login_required   # trabajo diario: cargar stock
def admin_inventario():
    if request.method == 'POST':
        data    = request.get_json()
        inv_std = get_inv_estandar()
        for nombre, (tipo, _) in inv_std.items():
            stock  = int(data.get(f'stock_{nombre}', 0))
            alerta = int(data.get(f'alerta_{nombre}', 5))
            upsert_inventario(nombre, tipo, stock, alerta)
        for item in data.get('nuevos', []):
            nombre   = item.get('nombre','').strip()
            tipo_cat = item.get('tipo_cat','bebida')
            stock    = int(item.get('stock',0) or 0)
            alerta   = int(item.get('alerta',5) or 5)
            if nombre:
                try:
                    with _conn() as c:
                        c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo) VALUES (?,?,0,1,?,1)",
                                  (nombre, tipo_cat, alerta))
                        c.execute("UPDATE catalogo SET en_inventario=1,activo=1,alerta_min=? WHERE nombre=?",
                                  (alerta, nombre))
                except: pass
                upsert_inventario(nombre, 'bebida', stock, alerta)
        return jsonify({'ok': True})
    inv_dict = {i["nombre"]: i for i in get_inventario_hoy()}
    pulpas   = get_pulpas_hoy()
    inv_std  = get_inv_estandar()
    return render_template('admin_inventario.html', inv_estandar=inv_std, inv_dict=inv_dict, pulpas=pulpas)

@app.route('/admin/cierre', methods=['GET','POST'])
@login_required   # trabajo diario: conteo fisico al cerrar
def admin_cierre():
    hoy   = ahora().strftime("%d/%m/%Y")
    fecha = request.args.get('fecha', hoy)
    if request.method == 'POST':
        try:
            data         = request.get_json()
            fecha_cierre = data.get('fecha', hoy)
            items_cierre = data.get('items', [])
            with _conn() as c:
                c.execute("DELETE FROM cierres_inventario WHERE fecha=?", (fecha_cierre,))
                for it in items_cierre:
                    c.execute(
                        "INSERT INTO cierres_inventario (fecha,nombre,tipo,stock_inicial,vendido,teorico,real_contado,diferencia,nota) VALUES (?,?,?,?,?,?,?,?,?)",
                        (fecha_cierre, it["nombre"], it["tipo"], it["stock_inicial"],
                         it["vendido"], it["teorico"], it["real_contado"], it["diferencia"], it.get("nota","")))
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500
    try:
        with _conn() as c:
            inv_rows = c.execute("SELECT * FROM inventario WHERE fecha=? ORDER BY tipo,nombre", (fecha,)).fetchall()
        inv_fecha    = {r["nombre"]: r["stock"] for r in inv_rows}
        inv_inicial  = {r["nombre"]: (r["stock_inicial"] if r["stock_inicial"] > 0 else r["stock"]) for r in inv_rows}
        vendido_dict = get_vendido_hoy(fecha)
        pizza_vend   = sum(v["cantidad"] for v in vendido_dict.values() if v["tipo"] == "Pizza")
        cierre_items = []
        for nombre, (tipo, _) in get_inv_estandar().items():
            stock_ini = inv_inicial.get(nombre, 0)
            if tipo == "pizza":
                vend = pizza_vend
            else:
                vend = sum(v["cantidad"] for k, v in vendido_dict.items() if k.startswith(nombre))
            teorico = max(0, stock_ini - vend)
            cierre_items.append({"nombre": nombre, "tipo": tipo,
                                  "stock_inicial": stock_ini, "vendido": vend, "teorico": teorico})
        with _conn() as c:
            pulpa_rows = c.execute("SELECT nombre, stock, stock_inicial FROM inventario WHERE tipo='pulpa' AND fecha=?", (fecha,)).fetchall()
        for r in pulpa_rows:
            s_ini = r["stock_inicial"] if r["stock_inicial"] > 0 else r["stock"]
            vend = sum(v["cantidad"] for k, v in vendido_dict.items() if k.startswith("Jugo Natural") and r["nombre"] in k)
            teorico = max(0, s_ini - vend)
            cierre_items.append({"nombre": r["nombre"], "tipo": "pulpa",
                                  "stock_inicial": s_ini, "vendido": vend, "teorico": teorico})
        with _conn() as c:
            saved = c.execute("SELECT * FROM cierres_inventario WHERE fecha=? ORDER BY nombre", (fecha,)).fetchall()
        saved_dict = {r["nombre"]: dict(r) for r in saved}
        return render_template('admin_cierre.html',
            cierre_items=cierre_items, fecha=fecha, hoy=hoy,
            saved_dict=saved_dict, fechas_disponibles=get_cierre_fechas(),
            sin_inventario=len(inv_fecha)==0)
    except Exception as e:
        return render_template('admin_cierre.html',
            cierre_items=[], fecha=fecha, hoy=hoy,
            saved_dict={}, fechas_disponibles=get_cierre_fechas(),
            sin_inventario=True, error=str(e))

@app.route('/admin/cierre/historial')
@login_required   # trabajo diario: ver conteos anteriores
def admin_cierre_historial():
    fechas = get_cierre_fechas()
    cierres_por_fecha = {}
    for f in fechas:
        with _conn() as c:
            rows = c.execute("SELECT * FROM cierres_inventario WHERE fecha=? ORDER BY tipo,nombre", (f,)).fetchall()
            cierres_por_fecha[f] = [dict(r) for r in rows]
    return render_template('admin_cierre_historial.html', cierres_por_fecha=cierres_por_fecha, fechas=fechas)

@app.route('/admin/cierre/csv')
@login_required   # trabajo diario: exportar el conteo
def admin_cierre_csv():
    fi = request.args.get('fi',''); ff = request.args.get('ff','')
    with _conn() as c:
        if fi and ff:
            rows = c.execute(
                f"SELECT * FROM cierres_inventario WHERE {sql_iso()} BETWEEN ? AND ? "
                f"ORDER BY {sql_iso()},tipo,nombre", rango_iso(fi, ff)).fetchall()
        else:
            rows = c.execute(
                f"SELECT * FROM cierres_inventario ORDER BY {sql_iso()} DESC,tipo,nombre").fetchall()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Fecha","Ítem","Tipo","Inicial","Vendido","Teórico","Real","Diferencia","Nota"])
    for r in rows: w.writerow([r["fecha"],r["nombre"],r["tipo"],r["stock_inicial"],r["vendido"],r["teorico"],r["real_contado"],r["diferencia"],r["nota"]])
    return Response(out.getvalue(), mimetype='text/csv', headers={"Content-Disposition":"attachment;filename=cierre_inventario.csv"})

@app.route('/admin/pedidos')
@rol_required('Administrador')
def admin_pedidos():
    hoy  = ahora().strftime("%d/%m/%Y")
    ayer_dt = ahora() - timedelta(days=1)
    ayer = ayer_dt.strftime("%d/%m/%Y")
    fecha_filtro = request.args.get('fecha', hoy)
    todos = get_pedidos()
    if fecha_filtro == 'todos':
        pedidos_filtrados = todos
    else:
        pedidos_filtrados = [p for p in todos if p["fecha"] == fecha_filtro]
    return render_template('admin_pedidos.html',
        pedidos=pedidos_filtrados, fecha_filtro=fecha_filtro, hoy=hoy, ayer=ayer)

@app.route('/admin/pedido/<int:pid>/eliminar', methods=['POST'])
@rol_required('Administrador')
def admin_eliminar_pedido(pid):
    with _conn() as c:
        c.execute("DELETE FROM items WHERE pedido_id=?", (pid,))
        c.execute("DELETE FROM notificaciones WHERE pid=?", (pid,))
        c.execute("DELETE FROM pagos WHERE pedido_id=?", (pid,))
        c.execute("DELETE FROM pedidos WHERE id=?", (pid,))
    flash(f'Pedido #{pid} eliminado', 'success')
    return redirect(url_for('admin_pedidos'))

@app.route('/admin/pedido/<int:pid>/reabrir', methods=['POST'])
@login_required   # trabajo diario: añadir a una reserva ya pagada
def admin_reabrir_pedido(pid):
    pedido = get_pedido(pid)
    if not pedido:
        flash('Pedido no encontrado', 'error')
        return redirect(url_for('admin_pedidos'))
    with _conn() as c:
        c.execute("UPDATE pedidos SET estado='Pendiente' WHERE id=?", (pid,))
    flash(f'Pedido #{pid} reabierto — puedes editarlo y agregar productos', 'success')
    return redirect(url_for('mesero_editar', pid=pid))

@app.route('/admin/reportes')
@rol_required('Administrador')
def admin_reportes():
    hoy = ahora().strftime("%d/%m/%Y")
    periodo = request.args.get('periodo', 'hoy')
    now = ahora()

    if periodo == 'ayer':
        d = now - timedelta(days=1)
        fi = ff = d.strftime("%d/%m/%Y")
        titulo = f"Ayer, {d.day} de {_MESES[d.month - 1]}"
    elif periodo == 'semana':
        ini_s = now - timedelta(days=now.weekday())
        fi, ff = ini_s.strftime("%d/%m/%Y"), hoy
        titulo = f"Del {ini_s.day} de {_MESES[ini_s.month - 1]} a hoy"
    elif periodo == 'mes':
        fi, ff = f"01/{now.month:02d}/{now.year}", hoy
        titulo = f"{_MESES[now.month - 1].capitalize()} de {now.year}"
    elif periodo == 'rango':
        # Los campos de fecha del navegador vienen en ISO
        fi = iso_a_fecha(request.args.get('d1', '')) or hoy
        ff = iso_a_fecha(request.args.get('d2', '')) or hoy
        titulo = f"Del {fi} al {ff}"
    else:
        periodo = 'hoy'
        fi = ff = hoy
        titulo = f"Hoy, {now.day} de {_MESES[now.month - 1]}"

    return render_template('admin_reportes.html',
        data=get_reporte(fi, ff), periodo=periodo, fi=fi, ff=ff, hoy=hoy,
        titulo_rango=titulo, metodos=METODOS_PAGO,
        d1=fecha_a_iso(fi, ""), d2=fecha_a_iso(ff, ""))


@app.route('/admin/reportes/csv')
@rol_required('Administrador')
def admin_csv():
    fi = request.args.get('fi',''); ff = request.args.get('ff','')
    with _conn() as c:
        rows = c.execute(
            "SELECT p.id,p.codigo,p.mesero,p.estado,p.total,p.hora,p.fecha,p.pago,"
            "i.nombre,i.tipo,i.cantidad,i.precio_unit "
            "FROM pedidos p JOIN items i ON i.pedido_id=p.id "
            f"WHERE p.estado='Pagado' AND {sql_iso('p')} BETWEEN ? AND ? ORDER BY p.id",
            rango_iso(fi, ff)).fetchall()
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["ID","Código","Mesero","Estado","Total","Hora","Fecha","Pago","Ítem","Tipo","Cantidad","Precio"])
    for r in rows: w.writerow(list(r))
    return Response(out.getvalue(), mimetype='text/csv', headers={"Content-Disposition":f"attachment;filename=reporte_{fi}_{ff}.csv"})

# ── GASTOS (solo Luis) ────────────────────────────────
@app.route('/admin/gastos', methods=['GET','POST'])
@solo_luis
def admin_gastos():
    if request.method == 'POST':
        categoria = request.form.get('categoria','').strip()
        proveedor = request.form.get('proveedor','').strip()
        descripcion = request.form.get('descripcion','').strip()
        monto_str = request.form.get('monto','0').strip().replace('.','').replace(',','')
        metodo_pago = request.form.get('metodo_pago','Efectivo').strip()
        try: monto = float(monto_str)
        except: monto = 0
        if not categoria or monto <= 0:
            flash('Completa al menos categoría y monto', 'error')
            return redirect(url_for('admin_gastos'))
        factura_path = ''
        archivo = request.files.get('factura')
        if archivo and archivo.filename:
            if _allowed_factura(archivo.filename):
                ext = archivo.filename.rsplit('.',1)[1].lower()
                safe_name = secure_filename(f"factura_{uuid.uuid4().hex[:12]}.{ext}")
                try:
                    archivo.save(os.path.join(UPLOAD_FOLDER, safe_name))
                    factura_path = safe_name
                except Exception as e:
                    flash(f'No se pudo guardar la factura: {e}', 'error')
            else:
                flash('Formato no soportado (usa jpg, png, pdf, webp)', 'error')
        crear_gasto(categoria, proveedor, descripcion, monto, metodo_pago, session['nombre'], factura_path)
        flash(f'Gasto registrado: {categoria} — ${monto:,.0f}'.replace(',','.'), 'success')
        return redirect(url_for('admin_gastos'))
    hoy = ahora().strftime("%d/%m/%Y")
    ayer = (ahora() - timedelta(days=1)).strftime("%d/%m/%Y")
    filtro = request.args.get('filtro', 'hoy')
    if filtro == 'hoy': gastos = get_gastos(hoy); titulo = f"Hoy · {hoy}"
    elif filtro == 'ayer': gastos = get_gastos(ayer); titulo = f"Ayer · {ayer}"
    elif filtro == 'mes':
        now = ahora(); fi = f"01/{now.month:02d}/{now.year}"
        gastos = get_gastos_rango(fi, hoy); titulo = f"Mes actual"
    elif filtro == 'todos': gastos = get_gastos('todos'); titulo = "Todos"
    else: gastos = get_gastos(filtro); titulo = f"Fecha · {filtro}"
    por_cat = {}
    for g in gastos: por_cat[g['categoria']] = por_cat.get(g['categoria'], 0) + g['monto']
    total_periodo = sum(g['monto'] for g in gastos)
    return render_template('admin_gastos.html',
        gastos=gastos, categorias=CATEGORIAS_GASTO, total_periodo=total_periodo,
        por_categoria=por_cat, filtro=filtro, titulo_filtro=titulo, hoy=hoy, ayer=ayer)

@app.route('/admin/gasto/<int:gid>/eliminar', methods=['POST'])
@solo_luis
def admin_eliminar_gasto(gid):
    eliminar_gasto(gid)
    flash('Gasto eliminado', 'success')
    return redirect(url_for('admin_gastos', filtro=request.form.get('filtro','hoy')))

@app.route('/admin/gastos/factura/<path:filename>')
@solo_luis
def admin_ver_factura(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin/usuarios', methods=['GET','POST'])
@rol_required('Administrador')
def admin_usuarios():
    """Alta, edicion y baja de usuarios. Ahora en la base, no en memoria.

    Antes esto modificaba el diccionario `USUARIOS` en RAM: los cambios se
    perdian al reiniciar y ni siquiera se veian en el otro worker de gunicorn.
    """
    if request.method == 'POST':
        accion = request.form.get('action')
        uid    = request.form.get('id', '').strip()

        if accion == 'update' and uid:
            nombre = request.form.get('nombre', '').strip()
            rol    = request.form.get('rol', ROL_OPERADOR)
            pwd    = request.form.get('password', '').strip()
            if rol not in ROLES:
                rol = ROL_OPERADOR
            with _conn() as c:
                actual = c.execute("SELECT usuario,rol FROM usuarios WHERE id=?", (uid,)).fetchone()
                if not actual:
                    flash('No se encontró ese usuario', 'error')
                    return redirect(url_for('admin_usuarios'))
                # Nadie puede dejar el negocio sin administradores
                if actual["rol"] == ROL_ADMIN and rol != ROL_ADMIN:
                    quedan = c.execute("SELECT COUNT(*) FROM usuarios WHERE rol=? AND activo=1 "
                                       "AND id<>?", (ROL_ADMIN, uid)).fetchone()[0]
                    if quedan == 0:
                        flash('No puedes quitar el último administrador', 'error')
                        return redirect(url_for('admin_usuarios'))
                if nombre:
                    c.execute("UPDATE usuarios SET nombre=? WHERE id=?", (nombre, uid))
                c.execute("UPDATE usuarios SET rol=? WHERE id=?", (rol, uid))
                if pwd:
                    c.execute("UPDATE usuarios SET password_hash=? WHERE id=?",
                              (generate_password_hash(pwd), uid))
            flash(f'@{actual["usuario"]} actualizado'
                  + (' (contraseña nueva)' if pwd else ''), 'success')

        elif accion == 'delete' and uid:
            with _conn() as c:
                row = c.execute("SELECT usuario,rol FROM usuarios WHERE id=?", (uid,)).fetchone()
                if not row:
                    flash('No se encontró ese usuario', 'error')
                elif row["usuario"] == session.get('usuario'):
                    flash('No puedes eliminar tu propia cuenta', 'error')
                elif row["rol"] == ROL_ADMIN and c.execute(
                        "SELECT COUNT(*) FROM usuarios WHERE rol=? AND activo=1 AND id<>?",
                        (ROL_ADMIN, uid)).fetchone()[0] == 0:
                    flash('No puedes eliminar el último administrador', 'error')
                else:
                    # Baja logica: sus pedidos y cobros historicos siguen intactos
                    c.execute("UPDATE usuarios SET activo=0 WHERE id=?", (uid,))
                    flash(f'@{row["usuario"]} desactivado', 'success')

        elif accion == 'create':
            u  = request.form.get('new_username', '').strip().lower()
            nn = request.form.get('new_nombre', '').strip()
            np = request.form.get('new_password', '').strip()
            nr = request.form.get('new_rol', ROL_OPERADOR)
            if nr not in ROLES:
                nr = ROL_OPERADOR
            if not (u and nn and np):
                flash('Faltan datos: usuario, nombre y contraseña', 'error')
            else:
                with _conn() as c:
                    ex = c.execute("SELECT id,activo FROM usuarios WHERE usuario=?", (u,)).fetchone()
                    if ex and ex["activo"]:
                        flash(f'El usuario @{u} ya existe', 'error')
                    elif ex:
                        c.execute("UPDATE usuarios SET nombre=?,password_hash=?,rol=?,activo=1 "
                                  "WHERE id=?", (nn, generate_password_hash(np), nr, ex["id"]))
                        flash(f'@{u} reactivado', 'success')
                    else:
                        c.execute("INSERT INTO usuarios (usuario,nombre,password_hash,rol,activo,creado) "
                                  "VALUES (?,?,?,?,1,?)",
                                  (u, nn, generate_password_hash(np), nr,
                                   ahora().strftime("%d/%m/%Y %H:%M")))
                        flash(f'@{u} creado como {nr}', 'success')
        return redirect(url_for('admin_usuarios'))

    return render_template('admin_usuarios.html', usuarios=get_usuarios(), roles=ROLES)


def _handle_menu(form, tipo_catalogo):
    """Alta, edicion y baja de productos del catalogo.

    Todo se hace POR ID, no por nombre. Antes se buscaba por (nombre, tipo),
    pero `catalogo.nombre` es UNIQUE GLOBAL: si el nombre ya existia con otro
    tipo, el INSERT OR IGNORE se ignoraba Y el UPDATE no encontraba nada, asi
    que el producto no se guardaba sin decir por que. Por ese camino acabaron
    bebidas registradas como tipo 'pizza'en la base de produccion.

    Ademas ahora cada operacion devuelve un mensaje: nada falla en silencio.
    """
    action = form.get('action')

    if action == 'update':
        cid    = form.get('id', '').strip()
        nombre = form.get('new_name', '').strip()
        precio = float(form.get('precio', 0) or 0)
        if not cid or not nombre:
            return ('Falta el nombre.', 'error')
        with _conn() as c:
            # Si otro producto ya tiene ese nombre, decirlo en vez de fallar mudo
            choca = c.execute("SELECT id,tipo FROM catalogo WHERE nombre=? AND id<>?",
                              (nombre, cid)).fetchone()
            if choca:
                return (f'Ya existe otro producto llamado "{nombre}" 'f'(tipo {choca["tipo"]}). Usa otro nombre.', 'error')
            cur = c.execute("UPDATE catalogo SET nombre=?, precio=? WHERE id=?",
                            (nombre, precio, cid))
            if not cur.rowcount:
                return ('No se encontro ese producto.', 'error')
            if form.get('en_inventario') is not None:
                c.execute("UPDATE catalogo SET en_inventario=?, alerta_min=? WHERE id=?",
                          (1 if form.get('en_inventario') == '1' else 0,
                           int(form.get('alerta_min', 5) or 5), cid))
            if form.get('categoria') in CATEGORIAS_BEBIDA:
                c.execute("UPDATE catalogo SET categoria=? WHERE id=?",
                          (form.get('categoria'), cid))
        return (f'Guardado: {nombre} — {fmt_cop(precio)}', 'success')

    if action == 'delete':
        cid = form.get('id', '').strip()
        if not cid:
            return ('Falta el producto.', 'error')
        with _conn() as c:
            row = c.execute("SELECT nombre FROM catalogo WHERE id=?", (cid,)).fetchone()
            # Baja logica: el producto sigue existiendo para los pedidos viejos
            cur = c.execute("UPDATE catalogo SET activo=0 WHERE id=?", (cid,))
        if not cur.rowcount:
            return ('No se encontro ese producto.', 'error')
        return (f'Eliminado: {row["nombre"]}', 'success')

    if action == 'add':
        nombre = form.get('name', '').strip()
        precio = float(form.get('precio', 0) or 0)
        en_inv = 1 if form.get('en_inventario') == '1' else 0
        alerta = int(form.get('alerta_min', 5) or 5)
        if not nombre:
            return ('Escribe un nombre.', 'error')
        with _conn() as c:
            ex = c.execute("SELECT id,tipo,activo FROM catalogo WHERE nombre=?", (nombre,)).fetchone()
            if ex and ex["tipo"] != tipo_catalogo:
                return (f'"{nombre}" ya existe como {ex["tipo"]}. 'f'Cambiale el nombre o editalo en su seccion.', 'error')
            if ex:
                # Reactivar uno dado de baja, con los datos nuevos
                c.execute("UPDATE catalogo SET precio=?, activo=1, en_inventario=?, alerta_min=? "
                          "WHERE id=?", (precio, en_inv, alerta, ex["id"]))
                verbo = 'Reactivado' if not ex["activo"] else 'Actualizado'
            else:
                cat = form.get('categoria')
                if cat not in CATEGORIAS_BEBIDA:
                    cat = categoria_sugerida(nombre)
                c.execute("INSERT INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo,categoria) "
                          "VALUES (?,?,?,?,?,1,?)", (nombre, tipo_catalogo, precio, en_inv, alerta, cat))
                verbo = 'Agregado'
        if precio <= 0:
            return (f'{verbo}: {nombre}, pero SIN PRECIO. Se venderia gratis — 'f'editalo y ponle precio.', 'error')
        return (f'{verbo}: {nombre} — {fmt_cop(precio)}', 'success')

    return None


@app.route('/admin/menu/pizzas', methods=['GET','POST'])
@login_required   # trabajo diario: agregar pizzas
def admin_menu_pizzas():
    if request.method == 'POST':
        msg = _handle_menu(request.form, 'pizza')
        if msg:
            flash(msg[0], msg[1])
        return redirect(url_for('admin_menu_pizzas'))
    return render_template('admin_menu.html', productos=get_catalogo_admin('pizzas'),
                           tipo='pizzas', titulo='Menú Pizzas', icono_seccion='pizza',
                           categorias=CATEGORIAS_BEBIDA)

@app.route('/admin/menu/bebidas', methods=['GET','POST'])
@login_required   # trabajo diario: agregar bebidas
def admin_menu_bebidas():
    if request.method == 'POST':
        msg = _handle_menu(request.form, 'bebida')
        if msg:
            flash(msg[0], msg[1])
        return redirect(url_for('admin_menu_bebidas'))
    return render_template('admin_menu.html', productos=get_catalogo_admin('bebidas'),
                           tipo='bebidas', titulo='Menú Bebidas', icono_seccion='bebidas',
                           categorias=CATEGORIAS_BEBIDA)

# ── MESERO ────────────────────────────────────────────
@app.route('/mesero/nuevo', methods=['GET','POST'])
@rol_required('Mesero')
def mesero_nuevo():
    if request.method == 'POST':
        data       = request.get_json()
        codigo     = data.get('codigo','').strip()
        mesa_num   = data.get('mesa_num','').strip()
        items      = data.get('items',[])
        notas      = data.get('notas','')
        franja     = data.get('franja', FRANJAS_HORA[0])
        cobrar_ya  = data.get('cobrar_ya', False)
        metodo_pago= data.get('metodo_pago','Efectivo')
        if not codigo or not items:
            return jsonify({'error':'Datos incompletos'}), 400
        # Validar stock de TODO el pedido (masas + bebidas)
        ok, error_msg = validar_stock_pedido(items)
        if not ok:
            return jsonify({'error': error_msg}), 400
        p = nuevo_pedido(codigo, session['nombre'], items, notas, franja)
        if mesa_num:
            with _conn() as c:
                c.execute("UPDATE pedidos SET mesa_num=? WHERE id=?", (mesa_num, p['id']))
        descontar_inventario(items)
        solo_bebidas = all(i["tipo"] != "Pizza" for i in items)
        # Flujo simplificado:
        # - Solo bebidas + pagó → directo a Pagado (no cocina, no cobro)
        # - Solo bebidas + no pagó → Listo (va a cobro)
        # - Con pizzas + pagó → Pendiente (cocina, con banner "ya pagado"), al marcar listo → Pagado
        # - Con pizzas + no pagó → Pendiente (cocina, luego cobro)
        if cobrar_ya:
            if solo_bebidas:
                # Directo a Pagado
                registrar_pago(p['id'], p['total'], metodo_pago, session['nombre'], marcar_pagado=True)
            else:
                # Pago registrado pero sigue en Pendiente para que cocina lo prepare.
                # Cuando cocina marque listo, marcar_listo() detectará el pago y pasará a Pagado.
                registrar_pago(p['id'], p['total'], metodo_pago, session['nombre'], marcar_pagado=False)
            return jsonify({'ok':True,'id':p['id'],'cobrado':True,'solo_bebidas':solo_bebidas})
        else:
            if solo_bebidas:
                # Sin pizzas → no pasa por cocina, va directo al cobro
                with _conn() as c:
                    c.execute("UPDATE pedidos SET estado='Listo' WHERE id=?", (p['id'],))
            # Con pizzas queda en Pendiente (cocina lo ve)
            return jsonify({'ok':True,'id':p['id'],'cobrado':False,'solo_bebidas':solo_bebidas})
    stock  = get_stock_dict()
    alertas = {k: v["alerta_min"] for k, v in get_stock_con_alertas().items()}
    pulpas = get_pulpas_hoy()
    return render_template('mesero_nuevo.html',
        sabores=get_catalogo_pizzas(), grupos=get_bebidas_por_categoria(), franjas=FRANJAS_HORA,
        stock_json=json.dumps(stock), alertas_json=json.dumps(alertas),
        metodos=METODOS_PAGO, precio_pizza=PRECIO_PIZZA)

@app.route('/mesero/pedidos')
@rol_required('Mesero')
def mesero_pedidos():
    hoy  = ahora().strftime("%d/%m/%Y")
    # Mostrar todos los pedidos del día (ordenados por hora, más recientes primero)
    todos = [p for p in get_pedidos() if p["fecha"] == hoy]
    return render_template('mesero_pedidos.html', pedidos=todos)

@app.route('/mesero/pedido/<int:pid>/editar', methods=['GET','POST'])
@rol_required('Mesero')
def mesero_editar(pid):
    pedido = get_pedido(pid)
    if not pedido:
        return redirect(url_for('mesero_pedidos'))
    # Separar items despachados (histórico, no editables) de los no despachados (editables)
    items_despachados = [i for i in pedido['productos'] if i.get('despachado')]
    items_no_despachados = [i for i in pedido['productos'] if not i.get('despachado')]
    if request.method == 'POST':
        data   = request.get_json()
        items  = data.get('items',[])  # Estos son los items que el mesero editó (los no despachados)
        notas  = data.get('notas', pedido['notas'])
        franja = data.get('franja', pedido['franja_hora'])
        cobrar_ya   = data.get('cobrar_ya', False)
        metodo_pago = data.get('metodo_pago', '')
        if not items and not items_despachados:
            return jsonify({'error':'El pedido no puede quedar vacío'}), 400
        # Restaurar inventario SOLO de los items no despachados (los que se están modificando)
        restaurar_inventario(items_no_despachados)
        ok, error_msg = validar_stock_pedido(items)
        if not ok:
            # Rollback: volver a descontar lo original
            descontar_inventario(items_no_despachados)
            return jsonify({'error': error_msg}), 400
        actualizar_pedido(pid, items, notas, franja)
        descontar_inventario(items)
        if pedido['estado'] == 'Listo':
            with _conn() as c:
                c.execute("UPDATE pedidos SET estado='Pendiente' WHERE id=?", (pid,))
        # Registrar pago si se indicó (cobra el saldo pendiente)
        if cobrar_ya and metodo_pago:
            pedido_actual = get_pedido(pid)
            saldo = pedido_actual["saldo"]
            if saldo > 0:
                # Si el pedido tiene pizzas nuevas por preparar, mantener en Pendiente
                # (cocina lo marca listo y ahí pasa a Pagado automáticamente)
                tiene_pizzas_nuevas = any(i["tipo"]=="Pizza" for i in items)
                marcar = not tiene_pizzas_nuevas  # Solo pasar a Pagado si NO hay pizzas nuevas por preparar
                registrar_pago(pid, saldo, metodo_pago, session['nombre'], marcar_pagado=marcar)
        items_txt = ", ".join(f"{i['cantidad']}x {i['nombre']}" for i in items)
        add_notificacion(pid, pedido['mesa'], items_txt, sum(i["cantidad"]*i["precio_unit"] for i in items))
        return jsonify({'ok':True})
    pulpas = get_pulpas_hoy()
    # Para el template: el pedido muestra solo los items no despachados como editables
    pedido_editable = dict(pedido)
    pedido_editable['productos'] = items_no_despachados
    pedido_editable['productos_despachados'] = items_despachados
    return render_template('mesero_editar.html', pedido=pedido_editable,
        sabores=get_catalogo_pizzas(), grupos=get_bebidas_por_categoria(), franjas=FRANJAS_HORA,
        pulpas_json=json.dumps(pulpas))

# ── CAJERO ────────────────────────────────────────────
# El cambio de fondo: antes se cobraba lo que COCINA hubiera marcado como listo,
# asi que un olvido dejaba el pedido fuera de la caja para siempre. En los datos
# reales eso dejo 12 pedidos preparados y entregados sin cobrar: $641.000 entre
# junio y septiembre. Ahora se cobra toda CUENTA ABIERTA, marque cocina o no.

@app.route('/cajero/cobrar')
@login_required
def cajero_cobrar():
    j = jornada_actual()
    filtro = request.args.get('filtro', 'hoy')
    abiertas = [p for p in listar_pedidos(estado_cuenta="Abierta") if p["total"] > 0]
    de_hoy    = [p for p in abiertas if p["jornada"] == j]
    anteriores = [p for p in abiertas if p["jornada"] != j]

    por_fecha = {}
    for p in sorted(anteriores, key=lambda x: x["jornada"], reverse=True):
        por_fecha.setdefault(p["fecha"], []).append(p)

    return render_template('cajero_cobrar.html',
        pedidos=de_hoy, anteriores_por_fecha=por_fecha,
        pendientes_anteriores=anteriores,
        hoy=ahora().strftime("%d/%m/%Y"), filtro=filtro,
        count_hoy=len(de_hoy), count_anteriores=len(anteriores),
        deuda_anterior=sum(p["saldo"] for p in anteriores),
        metodos=METODOS_PAGO)


@app.route('/cajero/cobrar/<int:pid>', methods=['POST'])
@login_required
def cajero_pagar(pid):
    pedido = get_pedido(pid)
    if not pedido:
        flash('No se encontró ese pedido', 'error')
        return redirect(url_for('cajero_cobrar'))

    metodo = request.form.get('metodo', 'Efectivo')
    if metodo not in METODOS_PAGO:
        flash('Medio de pago no válido', 'error')
        return redirect(url_for('cajero_cobrar'))

    # Monto parcial: dividir la cuenta es rutina en una pizzeria por reserva
    crudo = (request.form.get('monto') or '').replace('.', '').replace(',', '').strip()
    if crudo:
        try:
            monto = round(float(crudo))
        except ValueError:
            flash('Ese monto no se entiende', 'error')
            return redirect(url_for('cajero_cobrar'))
        if monto <= 0:
            flash('El monto debe ser mayor que cero', 'error')
            return redirect(url_for('cajero_cobrar'))
        if monto > pedido["saldo"]:
            flash(f'Son {fmt_cop(pedido["saldo"])}, no puedes cobrar más', 'error')
            return redirect(url_for('cajero_cobrar'))
    else:
        monto = pedido["saldo"]

    registrar_pago(pid, monto, metodo, session['nombre'], marcar_pagado=False)
    q = get_pedido(pid)
    if q["estado_cuenta"] == "Cerrada":
        flash(f'{q["mesa"]} — {fmt_cop(monto)} en {metodo}. Cuenta cerrada.', 'success')
    else:
        flash(f'{q["mesa"]} — {fmt_cop(monto)} en {metodo}. 'f'Falta {fmt_cop(q["saldo"])}.', 'success')
    return redirect(url_for('cajero_cobrar'))


@app.route('/cajero/cuenta/<int:pid>/cerrar_manual', methods=['POST'])
@login_required
def cerrar_cuenta_manual(pid):
    """Cerrar sin cobro completo: una cortesía o un descuento en el mostrador.

    Existe para que nadie tenga que falsear un cobro cuando decide no cobrar.
    Queda registrado quién y por qué.
    """
    motivo = (request.form.get('motivo') or '').strip()
    if not motivo:
        flash('Escribe por qué se cierra sin cobrar', 'error')
        return redirect(url_for('cajero_cobrar'))
    with _conn() as c:
        c.execute("UPDATE pedidos SET cierre_manual=1, notas=COALESCE(notas,'')||? WHERE id=?",
                  (f"\n[Cerrada sin cobro por {session['nombre']}: {motivo}]", pid))
        _sync_estado(c, pid)
    flash(f'Pedido #{pid} cerrado sin cobro — queda registrado', 'success')
    return redirect(url_for('cajero_cobrar'))


def resumen_caja(jornada=None, persona=None):
    """Lo cobrado por medio de pago, y cuánto efectivo hay que entregar.

    Solo el efectivo se entrega: lo electrónico ya llegó a la cuenta.
    """
    j = jornada or jornada_actual()
    cond, args = [f"{sql_iso()}=?"], [j]
    if persona:
        cond.append("cobrado_por=?"); args.append(persona)
    with _conn() as c:
        filas = c.execute(f"SELECT * FROM pagos WHERE {' AND '.join(cond)} ORDER BY id",
                          args).fetchall()
    por_metodo, por_persona = {}, {}
    for r in filas:
        por_metodo[r["metodo"]] = por_metodo.get(r["metodo"], 0) + r["monto"]
        por_persona.setdefault(r["cobrado_por"], {"total": 0, "efectivo": 0})
        por_persona[r["cobrado_por"]]["total"] += r["monto"]
        if r["metodo"] == "Efectivo":
            por_persona[r["cobrado_por"]]["efectivo"] += r["monto"]
    return {"jornada": j, "pagos": [dict(r) for r in filas],
            "total": sum(r["monto"] for r in filas),
            "efectivo": por_metodo.get("Efectivo", 0),
            "por_metodo": por_metodo, "por_persona": por_persona}


@app.route('/cajero/caja')
@login_required
def cajero_caja():
    j = jornada_actual()
    yo = session['nombre']
    todo = resumen_caja(j)
    mio  = resumen_caja(j, yo)
    entregas = {}
    try:
        with _conn() as c:
            entregas = {r["persona"]: dict(r) for r in c.execute(
                "SELECT * FROM entregas_caja WHERE jornada=?", (j,))}
    except Exception:
        pass
    return render_template('cajero_caja.html',
        hoy=ahora().strftime("%d/%m/%Y"), jornada=j, yo=yo,
        mio=mio, todo=todo, entregas=entregas,
        metodos_iconos=METODOS_PAGO,
        es_admin=(session.get('rol') == ROL_ADMIN),
        vista=request.args.get('vista', 'mia'))


@app.route('/cajero/caja/entrega', methods=['POST'])
@admin_required
def registrar_entrega():
    """El administrador anota cuánto efectivo recibió de cada persona."""
    persona = (request.form.get('persona') or '').strip()
    crudo = (request.form.get('entregado') or '').replace('.', '').replace(',', '').strip()
    try:
        entregado = round(float(crudo))
    except ValueError:
        flash('Ese monto no se entiende', 'error')
        return redirect(url_for('cajero_caja', vista='todos'))
    j = jornada_actual()
    esperado = resumen_caja(j, persona)["efectivo"]
    with _conn() as c:
        c.execute("INSERT OR REPLACE INTO entregas_caja "
                  "(jornada,persona,esperado,entregado,diferencia,recibido_por,fecha,hora,nota) "
                  "VALUES (?,?,?,?,?,?,?,?,?)",
                  (j, persona, esperado, entregado, entregado - esperado, session['nombre'],
                   ahora().strftime("%d/%m/%Y"), ahora().strftime("%H:%M"),
                   (request.form.get('nota') or '').strip()))
    dif = entregado - esperado
    if dif == 0:
        flash(f'{persona} entregó {fmt_cop(entregado)} — cuadra exacto', 'success')
    else:
        flash(f'{persona}: esperado {fmt_cop(esperado)}, recibido {fmt_cop(entregado)} 'f'({"sobra" if dif > 0 else "falta"} {fmt_cop(abs(dif))})', 'error')
    return redirect(url_for('cajero_caja', vista='todos'))


# ── F9: VENTA RAPIDA ──────────────────────────────────
# Una gaseosa suelta no puede costar lo mismo que un pedido completo. Antes
# habia que abrir o reabrir la reserva para algo que se despacha en 5 segundos.
#
# El sistema NUNCA inventa un codigo: los codigos son de reserva de ESTADIA y
# los da el alojamiento. Una venta sin codigo se identifica por su numero de
# pedido y queda marcada como 'rapida'; el campo `codigo` se deja vacio.

def buscar_cuenta_por_codigo(codigo):
    """Busca una cuenta de la jornada actual con ese codigo de estadia."""
    codigo = (codigo or "").strip()
    if not codigo:
        return None
    j = jornada_actual()
    with _conn() as c:
        r = c.execute(
            f"""SELECT id FROM pedidos
                WHERE COALESCE(anulado,0)=0 AND {sql_iso()}=?
                  AND LOWER(TRIM(codigo))=LOWER(?)
                ORDER BY id DESC LIMIT 1""", (j, codigo)).fetchone()
    return get_pedido(r["id"]) if r else None


@app.route('/api/reserva')
@login_required
def api_reserva():
    """Comprobacion en vivo mientras se escribe el codigo: muestra a donde va
    la venta ANTES de confirmar."""
    p = buscar_cuenta_por_codigo(request.args.get('codigo', ''))
    if not p:
        return jsonify({"existe": False})
    return jsonify({
        "existe": True, "id": p["id"], "codigo": p["mesa"], "mesa_num": p["mesa_num"],
        "total": p["total"], "saldo": p["saldo"],
        "cerrada": p["estado_cuenta"] == "Cerrada",
        "texto": (f'{p["mesa"]}' + (f' · Mesa {p["mesa_num"]}' if p["mesa_num"] else '')
                  + (f' · debe {fmt_cop(p["saldo"])}' if p["saldo"] > 0
                     else '· ya pagó, se reabrirá con esta bebida')),
    })


@app.route('/venta-rapida', methods=['GET', 'POST'])
@login_required
def venta_rapida():
    if request.method == 'POST':
        datos  = request.get_json(silent=True) or {}
        items  = datos.get('items', [])
        codigo = (datos.get('codigo') or '').strip()
        metodo = datos.get('metodo') or ''
        a_cuenta = bool(datos.get('a_cuenta'))
        confirmado = bool(datos.get('confirmado'))

        if not items:
            return jsonify({'error': 'No hay nada que vender'}), 400

        # A proposito NO se valida el stock aqui. En la venta rapida la nevera
        # es la verdad, no el contador: si el inventario dice 0 y fisicamente
        # hay cerveza, tiene que poder venderse. La pantalla ya avisa antes de
        # agregarla, y el stock se descuenta igual (sin bajar de 0).

        cuenta = buscar_cuenta_por_codigo(codigo) if codigo else None

        # Un error de tecleo no puede crear una cuenta fantasma en silencio
        if codigo and not cuenta and not confirmado:
            return jsonify({'confirmar': True,
                            'mensaje': f'No hay ninguna cuenta abierta hoy con el código 'f'"{codigo}". ¿Crear una nueva?'}), 409

        if not a_cuenta and metodo not in METODOS_PAGO:
            return jsonify({'error': 'Elige cómo paga'}), 400

        total = sum(i['cantidad'] * i['precio_unit'] for i in items)

        if cuenta:
            # Se SUMA a la estadia: ronda nueva, el total acumulado queda visible.
            # Se insertan ya entregados y SIN tocar lo pendiente: si hay una
            # pizza esperando en cocina, se queda esperando.
            pid = cuenta['id']
            with _conn() as c:
                agregar_items_entregados(c, pid, items)
            destino = f'sumado a {cuenta["mesa"]}'
        else:
            # Venta directa: sin codigo inventado, marcada como 'rapida'
            p = nuevo_pedido(codigo, session['nombre'], items,
                             notas='' if codigo else 'Venta directa (sin reserva)')
            pid = p['id']
            with _conn() as c:
                c.execute("UPDATE pedidos SET tipo_venta='rapida' WHERE id=?", (pid,))
                # Pedido recien creado: todo lo que tiene es lo que se acaba de
                # vender y entregar, asi que marcar por estacion es exacto.
                for est in ESTACIONES:
                    entregar_estacion(c, pid, est)
            destino = f'{codigo}' if codigo else f'Venta directa #{pid}'

        descontar_inventario(items)

        if not a_cuenta:
            registrar_pago(pid, total, metodo, session['nombre'], marcar_pagado=False)

        q = get_pedido(pid)
        return jsonify({'ok': True, 'id': pid, 'destino': destino,
                        'total': total, 'saldo': q['saldo'],
                        'a_cuenta': a_cuenta, 'metodo': metodo})

    return render_template('venta_rapida.html',
                           grupos=get_bebidas_por_categoria(),
                           stock_json=json.dumps(get_stock_dict()),
                           metodos=METODOS_PAGO)


# ── COCINA Y BARRA ────────────────────────────────────
# Dos pantallas, dos rutas, una sola plantilla. La estacion NO viene del
# cliente: la fija el servidor en cada ruta. Por eso es imposible que marcar
# bebidas arrastre las pizzas, aunque alguien manipule el formulario.

def _pantalla_estacion(tipo):
    comandas = listar_estacion(tipo)
    otro = "Bebida" if tipo == "Pizza" else "Pizza"
    return render_template(
        'estacion.html',
        comandas=comandas, tipo=tipo, otro=otro,
        es_pizzas=(tipo == "Pizza"),
        titulo="Cocina",
        subtitulo="Pizzas" if tipo == "Pizza" else "Bebidas",
        jornada=jornada_actual(),
        pendientes_otra=len(listar_estacion(otro)))


@app.route('/estacion/pizzas')
@login_required
def estacion_pizzas():
    return _pantalla_estacion("Pizza")


@app.route('/estacion/bebidas')
@login_required
def estacion_bebidas():
    return _pantalla_estacion("Bebida")


@app.route('/estacion/pizzas/<int:pid>/<int:ronda>/entregar', methods=['POST'])
@login_required
def entregar_pizzas(pid, ronda):
    with _conn() as c:
        entregar_estacion(c, pid, "Pizza", ronda)      # 'Pizza' fijo, no del cliente
    return redirect(url_for('estacion_pizzas'))


@app.route('/estacion/bebidas/<int:pid>/<int:ronda>/entregar', methods=['POST'])
@login_required
def entregar_bebidas(pid, ronda):
    with _conn() as c:
        entregar_estacion(c, pid, "Bebida", ronda)     # 'Bebida' fijo, no del cliente
    return redirect(url_for('estacion_bebidas'))


@app.route('/estacion/<cual>/<int:pid>/<int:ronda>/deshacer', methods=['POST'])
@login_required
def deshacer_entrega(cual, pid, ronda):
    """Deshacer, en vez de pedir confirmacion.

    En una tablet con las manos grasosas, un cuadro de confirmacion se toca en
    automatico y no protege de nada. Una ventana para deshacer si.
    """
    tipo = "Pizza" if cual == "pizzas" else "Bebida"
    with _conn() as c:
        c.execute("UPDATE items SET despachado=0 WHERE pedido_id=? AND ronda=? AND tipo=? "
                  "AND despachado=1", (pid, ronda, tipo))
        c.execute("UPDATE pedidos SET archivado=0 WHERE id=?", (pid,))
        _sync_estado(c, pid)
    flash(f'Deshecho: pedido #{pid} vuelve a {"cocina" if tipo == "Pizza" else "barra"}', 'success')
    return redirect(url_for('estacion_pizzas' if tipo == "Pizza" else 'estacion_bebidas'))


# ── Rutas viejas: se mantienen para las tablets que tengan la pagina
#    cacheada durante el despliegue. No dan 404 en mitad de un servicio.
@app.route('/cocina/pedidos')
@login_required
def cocina_pedidos():
    return redirect(url_for('estacion_pizzas'))


@app.route('/cocina/pedido/<int:pid>/listo', methods=['POST'])
@login_required
def cocina_listo(pid):
    with _conn() as c:
        for est in ESTACIONES:
            entregar_estacion(c, pid, est)
    return redirect(url_for('estacion_pizzas'))


@app.route('/cocina/notificaciones')
@login_required
def cocina_notifs():
    return jsonify(get_notificaciones_nuevas())


@app.route('/api/pedidos_count')
@login_required
def api_pedidos_count():
    activos = sum(1 for p in get_pedidos() if p["estado"]=="Pendiente")
    return jsonify({"count": activos})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
