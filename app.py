from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response, send_from_directory
import sqlite3, os, csv, io, json, sys, traceback, uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from werkzeug.utils import secure_filename

print(f"[BOOT] Python {sys.version}", flush=True)
print(f"[BOOT] CWD: {os.getcwd()}", flush=True)
print(f"[BOOT] DB_PATH env: {os.environ.get('DB_PATH','not set')}", flush=True)

# ── ZONA HORARIA COLOMBIA (UTC-5) ────────────────────
TZ_COL = timezone(timedelta(hours=-5))

def ahora():
    """Retorna datetime actual en hora Colombia."""
    return datetime.now(TZ_COL)

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
CATEGORIAS_GASTO = [
    "Insumos cocina", "Bebidas", "Empaques", "Servicios",
    "Mantenimiento y locativos", "Transporte/mandados", "Jornales", "Otros",
]

@app.template_filter('fromjson')
def fromjson_filter(v):
    try: return json.loads(v)
    except: return {}

@app.template_filter('cop')
def fmt_cop(v):
    try: return f"${float(v):,.0f}".replace(",",".")
    except: return "$0"

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=5)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    try:
        print(f"[BOOT] init_db starting, DB_PATH={DB_PATH}", flush=True)
        with _conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                codigo TEXT NOT NULL, mesero TEXT NOT NULL,
                estado TEXT DEFAULT 'Pendiente', total REAL DEFAULT 0,
                hora TEXT, fecha TEXT, pago TEXT, modificado INTEGER DEFAULT 0,
                notas TEXT DEFAULT '', franja_hora TEXT DEFAULT ''
            );
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
                diferencia INTEGER DEFAULT 0, nota TEXT DEFAULT ''
            );
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
                        "ALTER TABLE items ADD COLUMN despachado INTEGER DEFAULT 0"]:
                try: c.execute(col)
                except: pass
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
def _get_pagos(c, pid):
    try:
        rows = c.execute("SELECT * FROM pagos WHERE pedido_id=? ORDER BY id", (pid,)).fetchall()
        return [{"id": r["id"], "monto": r["monto"], "metodo": r["metodo"],
                 "cobrado_por": r["cobrado_por"], "fecha": r["fecha"], "hora": r["hora"]} for r in rows]
    except:
        return []

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
    return {
        "id": row["id"], "mesa": row["codigo"], "mesero": row["mesero"],
        "estado": row["estado"], "total": total, "hora": row["hora"],
        "fecha": row["fecha"], "pago": row["pago"], "modificado": bool(row["modificado"]),
        "notas": notas, "franja_hora": franja_hora,
        "cobrado_por": cobrado_por, "productos": prods,
        "pagos": pagos_list, "total_pagado": total_pagado, "saldo": saldo,
    }

def _get_items(c, pid):
    rows = c.execute("SELECT * FROM items WHERE pedido_id=? ORDER BY id", (pid,)).fetchall()
    result = []
    for r in rows:
        try:
            desp = r["despachado"]
        except (KeyError, IndexError):
            desp = 0
        result.append({
            "id": r["id"],
            "nombre": r["nombre"], "tipo": r["tipo"],
            "cantidad": r["cantidad"], "precio_unit": r["precio_unit"],
            "despachado": bool(desp)
        })
    return result

def get_pedidos():
    with _conn() as c:
        rows = c.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
        return [_pedido_from_row(r, _get_items(c, r["id"]), _get_pagos(c, r["id"])) for r in rows]

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
            "INSERT INTO pedidos (codigo,mesero,estado,total,hora,fecha,notas,franja_hora) VALUES (?,?,?,?,?,?,?,?)",
            (mesa, mesero, "Pendiente", total, hora, fecha, notas, franja_hora))
        pid = cur.lastrowid
        for i in items:
            c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit) VALUES (?,?,?,?,?)",
                      (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"]))
    return get_pedido(pid)

def registrar_pago(pid, monto, metodo, cobrado_por, marcar_pagado=True):
    """Registra un pago. Si marcar_pagado=False, guarda el pago pero no cambia el estado."""
    fecha = ahora().strftime("%d/%m/%Y")
    hora  = ahora().strftime("%H:%M")
    try:
        with _conn() as c:
            c.execute("INSERT INTO pagos (pedido_id,monto,metodo,cobrado_por,fecha,hora) VALUES (?,?,?,?,?,?)",
                      (pid, monto, metodo, cobrado_por, fecha, hora))
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
    Los 'items' recibidos son los NO despachados (lo que el mesero puede editar).
    Los items ya despachados se mantienen intactos en BD.
    El total del pedido = suma de items despachados + items nuevos."""
    with _conn() as c:
        # Borrar solo los items NO despachados (los nuevos/editables)
        try:
            c.execute("DELETE FROM items WHERE pedido_id=? AND (despachado=0 OR despachado IS NULL)", (pid,))
        except:
            c.execute("DELETE FROM items WHERE pedido_id=?", (pid,))
        # Insertar los items nuevos (no despachados por defecto)
        for i in items:
            try:
                c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit,despachado) VALUES (?,?,?,?,?,0)",
                          (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"]))
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

def marcar_listo(pid):
    with _conn() as c:
        # Marcar todos los items no despachados como despachados (histórico)
        try:
            c.execute("UPDATE items SET despachado=1 WHERE pedido_id=?", (pid,))
        except: pass
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
def get_reporte(fecha_ini, fecha_fin):
    with _conn() as c:
        pagados = c.execute(
            "SELECT * FROM pedidos WHERE estado='Pagado' AND fecha>=? AND fecha<=?",
            (fecha_ini, fecha_fin)).fetchall()
        total_ventas = sum(r["total"] for r in pagados)
        n_pedidos    = len(pagados)
        ticket_prom  = total_ventas / n_pedidos if n_pedidos else 0
        por_metodo = {}
        for r in pagados:
            m = r["pago"] or "N/A"
            por_metodo[m] = por_metodo.get(m, 0) + r["total"]
        por_dia = {}
        for r in pagados:
            por_dia[r["fecha"]] = por_dia.get(r["fecha"], 0) + r["total"]
        ids = [r["id"] for r in pagados]
        top_items = []
        if ids:
            ph = ",".join("?" * len(ids))
            rows = c.execute(
                f"SELECT nombre, tipo, SUM(cantidad) as tc, SUM(cantidad*precio_unit) as tv "
                f"FROM items WHERE pedido_id IN ({ph}) GROUP BY nombre ORDER BY tc DESC", ids).fetchall()
            top_items = [{"nombre": r["nombre"], "tipo": r["tipo"], "cantidad": r["tc"], "valor": r["tv"]} for r in rows]
        return {"total_ventas": total_ventas, "n_pedidos": n_pedidos, "ticket_prom": ticket_prom,
                "por_metodo": por_metodo, "por_dia": por_dia, "top_items": top_items}

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
        # Crear registros de hoy con el stock final de ayer
        for r in rows_ant:
            c.execute(
                "INSERT INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) VALUES (?,?,?,?,?,?)",
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
            c.execute("INSERT INTO inventario (nombre,tipo,stock,stock_inicial,alerta_min,fecha) VALUES (?,?,?,?,?,?)",
                      (nombre, tipo, max(0, stock), max(0, stock), amin, hoy))

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
            "SELECT DISTINCT fecha FROM cierres_inventario ORDER BY fecha DESC").fetchall()
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
    try:
        d, m, y = fecha_str.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except: return "9999-99-99"

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
    fi_iso = _fecha_a_iso(fecha_ini)
    ff_iso = _fecha_a_iso(fecha_fin)
    with _conn() as c:
        rows = c.execute("SELECT * FROM gastos").fetchall()
    return [dict(r) for r in rows if fi_iso <= _fecha_a_iso(r["fecha"]) <= ff_iso]

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

def rol_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'usuario' not in session: return redirect(url_for('login'))
            if session['rol'] not in roles and session['rol'] != 'Administrador':
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

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
        p = request.form.get('password','').strip()
        if u in USUARIOS and USUARIOS[u]['password'] == p:
            session['usuario'] = u
            session['rol']     = USUARIOS[u]['rol']
            session['nombre']  = USUARIOS[u]['nombre']
            # Multi-rol: guardar lista de roles disponibles
            session['roles']   = USUARIOS[u].get('roles', [USUARIOS[u]['rol']])
            return redirect(url_for('dashboard'))
        error = "Usuario o contraseña incorrectos"
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/cambiar_rol/<rol>')
@login_required
def cambiar_rol(rol):
    roles_disponibles = session.get('roles', [session.get('rol')])
    if rol in roles_disponibles:
        session['rol'] = rol
        session['rol_elegido'] = True  # Ya eligió, no volver a mostrar selector
    return redirect(url_for('dashboard'))

@app.route('/elegir_rol')
@login_required
def elegir_rol():
    """Muestra la pantalla de selección de rol (resetea la elección)."""
    roles = session.get('roles', [session.get('rol')])
    if len(roles) > 1:
        session['rol_elegido'] = False
        return render_template('selector_rol.html', roles=roles, nombre=session['nombre'])
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
@login_required
def dashboard():
    roles = session.get('roles', [session.get('rol')])
    # Si tiene multi-rol y aún no eligió, mostrar selector
    if len(roles) > 1 and not session.get('rol_elegido'):
        return render_template('selector_rol.html', roles=roles, nombre=session['nombre'])
    rol = session['rol']
    if   rol == 'Administrador': return redirect(url_for('admin_resumen'))
    elif rol == 'Mesero':        return redirect(url_for('mesero_nuevo'))
    elif rol == 'Cajero':        return redirect(url_for('cajero_cobrar'))
    elif rol == 'Cocina':        return redirect(url_for('cocina_pedidos'))
    return redirect(url_for('login'))

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
@rol_required('Administrador')
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
@rol_required('Administrador')
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
@rol_required('Administrador')
def admin_cierre_historial():
    fechas = get_cierre_fechas()
    cierres_por_fecha = {}
    for f in fechas:
        with _conn() as c:
            rows = c.execute("SELECT * FROM cierres_inventario WHERE fecha=? ORDER BY tipo,nombre", (f,)).fetchall()
            cierres_por_fecha[f] = [dict(r) for r in rows]
    return render_template('admin_cierre_historial.html', cierres_por_fecha=cierres_por_fecha, fechas=fechas)

@app.route('/admin/cierre/csv')
@rol_required('Administrador')
def admin_cierre_csv():
    fi = request.args.get('fi',''); ff = request.args.get('ff','')
    with _conn() as c:
        if fi and ff:
            rows = c.execute("SELECT * FROM cierres_inventario WHERE fecha>=? AND fecha<=? ORDER BY fecha,tipo,nombre", (fi,ff)).fetchall()
        else:
            rows = c.execute("SELECT * FROM cierres_inventario ORDER BY fecha DESC,tipo,nombre").fetchall()
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
    flash(f'🗑 Pedido #{pid} eliminado', 'success')
    return redirect(url_for('admin_pedidos'))

@app.route('/admin/pedido/<int:pid>/reabrir', methods=['POST'])
@rol_required('Administrador')
def admin_reabrir_pedido(pid):
    pedido = get_pedido(pid)
    if not pedido:
        flash('Pedido no encontrado', 'error')
        return redirect(url_for('admin_pedidos'))
    with _conn() as c:
        c.execute("UPDATE pedidos SET estado='Pendiente' WHERE id=?", (pid,))
    flash(f'🔓 Pedido #{pid} reabierto — puedes editarlo y agregar productos', 'success')
    return redirect(url_for('mesero_editar', pid=pid))

@app.route('/admin/reportes')
@rol_required('Administrador')
def admin_reportes():
    hoy     = ahora().strftime("%d/%m/%Y")
    periodo = request.args.get('periodo','hoy')
    fi      = request.args.get('fi', hoy)
    ff      = request.args.get('ff', hoy)
    if periodo == 'hoy':     fi = ff = hoy
    elif periodo == 'semana':
        now = ahora()
        fi  = (now - timedelta(days=now.weekday())).strftime("%d/%m/%Y"); ff = hoy
    elif periodo == 'mes':
        now = ahora(); fi = f"01/{now.month:02d}/{now.year}"; ff = hoy
    data = get_reporte(fi, ff)
    return render_template('admin_reportes.html', data=data, periodo=periodo, fi=fi, ff=ff, hoy=hoy)

@app.route('/admin/reportes/csv')
@rol_required('Administrador')
def admin_csv():
    fi = request.args.get('fi',''); ff = request.args.get('ff','')
    with _conn() as c:
        rows = c.execute(
            "SELECT p.id,p.codigo,p.mesero,p.estado,p.total,p.hora,p.fecha,p.pago,"
            "i.nombre,i.tipo,i.cantidad,i.precio_unit "
            "FROM pedidos p JOIN items i ON i.pedido_id=p.id "
            "WHERE p.estado='Pagado' AND p.fecha>=? AND p.fecha<=? ORDER BY p.id",
            (fi,ff)).fetchall()
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
        flash(f'✅ Gasto registrado: {categoria} — ${monto:,.0f}'.replace(',','.'), 'success')
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
    flash('🗑 Gasto eliminado', 'success')
    return redirect(url_for('admin_gastos', filtro=request.form.get('filtro','hoy')))

@app.route('/admin/gastos/factura/<path:filename>')
@solo_luis
def admin_ver_factura(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/admin/usuarios', methods=['GET','POST'])
@rol_required('Administrador')
def admin_usuarios():
    if request.method == 'POST':
        action = request.form.get('action')
        u = request.form.get('username','').strip()
        if action == 'update' and u in USUARIOS:
            USUARIOS[u]['nombre']   = request.form.get('nombre','').strip() or USUARIOS[u]['nombre']
            USUARIOS[u]['password'] = request.form.get('password','').strip() or USUARIOS[u]['password']
            USUARIOS[u]['rol']      = request.form.get('rol','Mesero')
            flash(f'Usuario @{u} actualizado ✅','success')
        elif action == 'delete' and u in USUARIOS and u != 'admin':
            del USUARIOS[u]; flash(f'Usuario @{u} eliminado','success')
        elif action == 'create':
            nu = request.form.get('new_username','').strip()
            nn = request.form.get('new_nombre','').strip()
            np = request.form.get('new_password','').strip()
            nr = request.form.get('new_rol','Mesero')
            if nu and nn and np and nu not in USUARIOS:
                USUARIOS[nu] = {'password':np,'rol':nr,'nombre':nn}
                flash(f'Usuario @{nu} creado ✅','success')
            elif nu in USUARIOS:
                flash(f'El usuario @{nu} ya existe','error')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin_usuarios.html', usuarios=USUARIOS, roles=["Administrador","Mesero","Cajero","Cocina"])

def _handle_menu(form, tipo_catalogo):
    action = form.get('action')
    if action == 'update':
        old_name = form.get('old_name','').strip(); new_name = form.get('new_name','').strip()
        precio   = float(form.get('precio',0) or 0)
        if old_name and new_name:
            with _conn() as c:
                c.execute("UPDATE catalogo SET nombre=?,precio=? WHERE nombre=? AND tipo=?",
                          (new_name, precio, old_name, tipo_catalogo))
    elif action == 'delete':
        name = form.get('name','').strip()
        if name:
            with _conn() as c:
                c.execute("UPDATE catalogo SET activo=0 WHERE nombre=? AND tipo=?", (name, tipo_catalogo))
    elif action == 'add':
        name   = form.get('name','').strip(); precio = float(form.get('precio',0) or 0)
        en_inv = 1 if form.get('en_inventario') == '1' else 0
        alerta = int(form.get('alerta_min',5) or 5)
        if name:
            with _conn() as c:
                c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min,activo) VALUES (?,?,?,?,?,1)",
                          (name, tipo_catalogo, precio, en_inv, alerta))
                c.execute("UPDATE catalogo SET precio=?,activo=1,en_inventario=?,alerta_min=? WHERE nombre=? AND tipo=?",
                          (precio, en_inv, alerta, name, tipo_catalogo))

@app.route('/admin/menu/pizzas', methods=['GET','POST'])
@rol_required('Administrador')
def admin_menu_pizzas():
    if request.method == 'POST':
        _handle_menu(request.form, 'pizza')
        return redirect(url_for('admin_menu_pizzas'))
    return render_template('admin_menu.html', menu=get_catalogo_pizzas(), tipo='pizzas', titulo='Menú Pizzas', icono='🍕')

@app.route('/admin/menu/bebidas', methods=['GET','POST'])
@rol_required('Administrador')
def admin_menu_bebidas():
    if request.method == 'POST':
        _handle_menu(request.form, 'bebida')
        return redirect(url_for('admin_menu_bebidas'))
    return render_template('admin_menu.html', menu=get_catalogo_bebidas(), tipo='bebidas', titulo='Menú Bebidas', icono='🥤')

# ── MESERO ────────────────────────────────────────────
@app.route('/mesero/nuevo', methods=['GET','POST'])
@rol_required('Mesero')
def mesero_nuevo():
    if request.method == 'POST':
        data       = request.get_json()
        codigo     = data.get('codigo','').strip()
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
        sabores=get_catalogo_pizzas(), bebidas=get_catalogo_bebidas(), franjas=FRANJAS_HORA,
        stock_json=json.dumps(stock), alertas_json=json.dumps(alertas),
        pulpas_json=json.dumps(pulpas),
        toppings=TOPPINGS, precio_pizza=PRECIO_PIZZA)

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
        sabores=get_catalogo_pizzas(), bebidas=get_catalogo_bebidas(), franjas=FRANJAS_HORA,
        pulpas_json=json.dumps(pulpas))

# ── CAJERO ────────────────────────────────────────────
@app.route('/cajero/cobrar')
@rol_required('Cajero')
def cajero_cobrar():
    hoy = ahora().strftime("%d/%m/%Y")
    todos = get_pedidos()
    filtro = request.args.get('filtro', 'hoy')
    # Mostrar: estado Listo, O cualquier pedido con saldo>0 que no esté Pagado
    por_cobrar = [p for p in todos if p["estado"]=="Listo" or (p["estado"]!="Pagado" and p["saldo"]>0 and p["total_pagado"]>0)]
    # Deduplicar por id
    vistos = set()
    unicos = []
    for p in por_cobrar:
        if p["id"] not in vistos:
            vistos.add(p["id"])
            unicos.append(p)
    pendientes_anteriores = [p for p in unicos if p["fecha"]!=hoy]
    de_hoy = [p for p in unicos if p["fecha"]==hoy]
    # Agrupar anteriores por fecha (para template nuevo)
    anteriores_por_fecha = {}
    for p in pendientes_anteriores:
        anteriores_por_fecha.setdefault(p["fecha"], []).append(p)
    return render_template('cajero_cobrar.html',
        pedidos=de_hoy,
        pendientes_anteriores=pendientes_anteriores,
        anteriores_por_fecha=anteriores_por_fecha,
        hoy=hoy, filtro=filtro,
        count_hoy=len(de_hoy),
        count_anteriores=len(pendientes_anteriores))

@app.route('/cajero/cobrar/<int:pid>', methods=['POST'])
@rol_required('Cajero')
def cajero_pagar(pid):
    metodo = request.form.get('metodo','Efectivo')
    cobrar_pedido(pid, metodo, session['nombre'])
    flash(f'✅ Pedido #{pid} cobrado — {metodo}','success')
    return redirect(url_for('cajero_cobrar'))

@app.route('/cajero/cobrar/<int:pid>/confirmar_pago', methods=['POST'])
@rol_required('Cajero')
def cajero_confirmar_pago(pid):
    pedido = get_pedido(pid)
    if pedido and pedido["saldo"] <= 0 and pedido["total_pagado"] > 0:
        # Ya está completamente pagado, solo cambiar estado
        with _conn() as c:
            c.execute("UPDATE pedidos SET estado='Pagado' WHERE id=?", (pid,))
    flash(f'✅ Pedido #{pid} confirmado como pagado','success')
    return redirect(url_for('cajero_cobrar'))

@app.route('/cajero/caja')
@rol_required('Cajero')
def cajero_caja():
    hoy = ahora().strftime("%d/%m/%Y")
    pag = [p for p in get_pedidos() if p["estado"]=="Pagado" and p["fecha"]==hoy]
    # Obtener todos los pagos del día para resumen preciso
    pagos_hoy = []
    try:
        with _conn() as c:
            pagos_hoy = c.execute("SELECT * FROM pagos WHERE fecha=? ORDER BY id", (hoy,)).fetchall()
    except: pass
    total   = sum(r["monto"] for r in pagos_hoy)
    metodos = {}
    for r in pagos_hoy:
        m = r["metodo"]
        metodos[m] = metodos.get(m, 0) + r["monto"]
    # Resumen por cobrador
    por_cobrador = {}
    for r in pagos_hoy:
        cb = r["cobrado_por"]
        por_cobrador[cb] = por_cobrador.get(cb, 0) + r["monto"]
    # Fallback si tabla pagos no tiene datos (BD antigua)
    if total == 0 and pag:
        total = sum(p["total"] for p in pag)
        for p in pag:
            m = p["pago"] or "N/A"
            metodos[m] = metodos.get(m, 0) + p["total"]
    return render_template('cajero_caja.html', pedidos=pag, total=total,
        metodos=metodos, por_cobrador=por_cobrador, hoy=hoy)

# ── COCINA ────────────────────────────────────────────
@app.route('/cocina/pedidos')
@rol_required('Cocina')
def cocina_pedidos():
    todos   = get_pedidos()
    activos = [p for p in todos if p["estado"]=="Pendiente"]
    for p in activos:
        # Items que la cocina debe preparar ahora (no despachados)
        nuevos = [i for i in p["productos"] if not i.get("despachado")]
        # Items ya despachados anteriormente (histórico)
        despachados = [i for i in p["productos"] if i.get("despachado")]
        p["pizzas_nuevas"]  = [i for i in nuevos if i["tipo"]=="Pizza"]
        p["bebidas_nuevas"] = [i for i in nuevos if i["tipo"]=="Bebida"]
        p["pizzas_despachadas"]  = [i for i in despachados if i["tipo"]=="Pizza"]
        p["bebidas_despachadas"] = [i for i in despachados if i["tipo"]=="Bebida"]
        # Para compatibilidad con template viejo
        p["pizzas"]  = p["pizzas_nuevas"]
        p["bebidas"] = p["bebidas_nuevas"]
        p["tiene_despachados"] = len(despachados) > 0
        # ¿Está ya completamente pagado? Si sí, el botón dirá "Cerrar pedido" en vez de "Pasar a cobro"
        p["ya_pagado"] = p["total_pagado"] >= p["total"] and p["total_pagado"] > 0
    grupos = {}
    for p in activos:
        k = p.get("franja_hora") or "Sin hora"
        grupos.setdefault(k,[]).append(p)
    franjas_ord = [f for f in FRANJAS_HORA if f in grupos]
    if "Sin hora" in grupos: franjas_ord.append("Sin hora")
    return render_template('cocina_pedidos.html', activos=activos, grupos=grupos, franjas_ord=franjas_ord)

@app.route('/cocina/pedido/<int:pid>/listo', methods=['POST'])
@rol_required('Cocina')
def cocina_listo(pid):
    marcar_listo(pid)
    return redirect(url_for('cocina_pedidos'))

@app.route('/cocina/notificaciones')
@rol_required('Cocina')
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
