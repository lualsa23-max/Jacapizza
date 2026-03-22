from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
import sqlite3, os, csv, io, json
from datetime import datetime, timedelta, timezone
from functools import wraps

# ── ZONA HORARIA COLOMBIA (UTC-5) ────────────────────
TZ_COL = timezone(timedelta(hours=-5))

def ahora():
    """Retorna datetime actual en hora Colombia."""
    return datetime.now(TZ_COL)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'jacapizza-secret-2024-xK9!')

DB_PATH = os.environ.get('DB_PATH', '/data/pizza_data.db')
_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try: os.makedirs(_db_dir, exist_ok=True)
    except: DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pizza_data.db')

USUARIOS = {
    "admin":   {"password": "admin123",  "rol": "Administrador", "nombre": "Natalia de Sarmiento"},
    "luis":    {"password": "luis2026",   "rol": "Administrador", "nombre": "Luis Sarmiento"},
    "mesero1": {"password": "mesero123", "rol": "Mesero",        "nombre": "Daniela Suárez"},
    "cajero1": {"password": "cajero123", "rol": "Cajero",        "nombre": "Caren Muñetón"},
    "cocina1": {"password": "cocina123", "rol": "Cocina",        "nombre": "Chef y Chefa"},
}
FRANJAS_HORA = [
    "7:00 PM","7:15 PM","7:30 PM","7:45 PM",
    "8:00 PM","8:15 PM","8:30 PM","8:45 PM","9:00 PM",
]
BEBIDAS_DEFAULT = {
    "Gaseosa":4000,"Agua 600ml":4000,"Soda Italiana":5000,
    "Cerveza Águila":4000,"Cerveza Águila Light":4000,"Cerveza Coronita":5000,
    "Cerveza Poker":4000,"Jugo Natural (agua)":7000,"Limonada de Coco":7000,"Cerezada":7000,
}
PIZZAS_DEFAULT = {
    "Hawaiana":20000,"Pollo con Champiñones":20000,"Mexicana":20000,
    "Pepperoni":20000,"Criolla":20000,"Vegetariana":20000,
}
INV_DEFAULT = {
    "Pizza (masa)":("pizza",3),"Agua 600ml":("bebida",5),"Gaseosa":("bebida",5),
    "Cerveza Águila":("bebida",5),"Cerveza Águila Light":("bebida",5),
    "Cerveza Coronita":("bebida",5),"Cerveza Poker":("bebida",5),
    "Soda Italiana - Frutos Rojos":("bebida",5),"Soda Italiana - Frutos Amarillos":("bebida",5),
    "Limonada de Coco":("bebida",5),"Cerezada":("bebida",5),
}

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
            stock_inicial INTEGER DEFAULT 0,
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
        """)
        for col in ["ALTER TABLE pedidos ADD COLUMN notas TEXT DEFAULT ''",
                    "ALTER TABLE pedidos ADD COLUMN franja_hora TEXT DEFAULT ''",
                    "ALTER TABLE inventario ADD COLUMN stock_inicial INTEGER DEFAULT 0",
                    "ALTER TABLE pedidos ADD COLUMN cobrado_por TEXT DEFAULT ''"]:
            try: c.execute(col)
            except: pass

init_db()

def _seed_catalogo():
    try:
        with _conn() as c:
            count = c.execute("SELECT COUNT(*) FROM catalogo").fetchone()[0]
            if count > 0: return
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
            for nombre, precio in [("Jugo Natural (agua)",7000),("Soda Italiana",5000)]:
                c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,?,0,0)",
                         (nombre,"bebida_especial",precio))
            c.execute("INSERT OR IGNORE INTO catalogo (nombre,tipo,precio,en_inventario,alerta_min) VALUES (?,?,0,1,3)",
                     ("Pizza (masa)","pizza_inv"))
    except Exception as e:
        print("Seed error:", e)

_seed_catalogo()

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
    rows = c.execute("SELECT * FROM pagos WHERE pedido_id=? ORDER BY id", (pid,)).fetchall()
    return [{"id": r["id"], "monto": r["monto"], "metodo": r["metodo"],
             "cobrado_por": r["cobrado_por"], "fecha": r["fecha"], "hora": r["hora"]} for r in rows]

def _pedido_from_row(row, prods, pagos_list):
    total_pagado = sum(p["monto"] for p in pagos_list)
    total = row["total"]
    saldo = max(0, total - total_pagado)
    return {
        "id": row["id"], "mesa": row["codigo"], "mesero": row["mesero"],
        "estado": row["estado"], "total": total, "hora": row["hora"],
        "fecha": row["fecha"], "pago": row["pago"], "modificado": bool(row["modificado"]),
        "notas": row["notas"] or "", "franja_hora": row["franja_hora"] or "",
        "cobrado_por": row["cobrado_por"] or "", "productos": prods,
        "pagos": pagos_list, "total_pagado": total_pagado, "saldo": saldo,
    }

def _get_items(c, pid):
    rows = c.execute("SELECT * FROM items WHERE pedido_id=?", (pid,)).fetchall()
    return [{"nombre": r["nombre"], "tipo": r["tipo"], "cantidad": r["cantidad"], "precio_unit": r["precio_unit"]} for r in rows]

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

def registrar_pago(pid, monto, metodo, cobrado_por):
    """Registra un pago parcial o total en la tabla pagos."""
    fecha = ahora().strftime("%d/%m/%Y")
    hora  = ahora().strftime("%H:%M")
    with _conn() as c:
        c.execute("INSERT INTO pagos (pedido_id,monto,metodo,cobrado_por,fecha,hora) VALUES (?,?,?,?,?,?)",
                  (pid, monto, metodo, cobrado_por, fecha, hora))
        # Recalcular: si total_pagado >= total pedido → Pagado
        total_pedido = c.execute("SELECT total FROM pedidos WHERE id=?", (pid,)).fetchone()["total"]
        total_pagado = c.execute("SELECT COALESCE(SUM(monto),0) FROM pagos WHERE pedido_id=?", (pid,)).fetchone()[0]
        if total_pagado >= total_pedido:
            c.execute("UPDATE pedidos SET estado='Pagado', pago=?, cobrado_por=? WHERE id=?",
                      (metodo, cobrado_por, pid))

def cobrar_pedido(pid, metodo, cobrado_por=""):
    """Cobra el saldo pendiente del pedido."""
    pedido = get_pedido(pid)
    if not pedido: return
    saldo = pedido["saldo"]
    if saldo <= 0: saldo = pedido["total"]  # fallback si no hay pagos previos
    registrar_pago(pid, saldo, metodo, cobrado_por)

def actualizar_pedido(pid, items, notas=None, franja_hora=None):
    total = sum(i["cantidad"] * i["precio_unit"] for i in items)
    with _conn() as c:
        c.execute("DELETE FROM items WHERE pedido_id=?", (pid,))
        for i in items:
            c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit) VALUES (?,?,?,?,?)",
                      (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"]))
        if notas is not None and franja_hora is not None:
            c.execute("UPDATE pedidos SET total=?,modificado=1,notas=?,franja_hora=? WHERE id=?",
                      (total, notas, franja_hora, pid))
        else:
            c.execute("UPDATE pedidos SET total=?,modificado=1 WHERE id=?", (total, pid))

def marcar_listo(pid):
    with _conn() as c:
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
        return [{"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"],
                 "stock": r["stock"], "alerta_min": r["alerta_min"]} for r in rows]

def get_stock_dict():
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT nombre, stock FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        return {r["nombre"]: r["stock"] for r in rows}

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
    hoy = ahora().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute(
            "SELECT nombre,stock,alerta_min FROM inventario WHERE tipo='pulpa' AND fecha=? ORDER BY nombre", (hoy,)).fetchall()
        return [{"nombre": r["nombre"], "stock": r["stock"], "alerta_min": r["alerta_min"]} for r in rows]

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
            return redirect(url_for('dashboard'))
        error = "Usuario o contraseña incorrectos"
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
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
    total_dia  = sum(p["total"] for p in todos if p["fecha"]==hoy and p["estado"]=="Pagado")
    pagados    = sum(1 for p in todos if p["estado"]=="Pagado")
    pendientes = sum(1 for p in todos if p["estado"]=="Pendiente")
    cobros_pendientes = sum(1 for p in todos if p["estado"]=="Listo" and p["fecha"]!=hoy)
    return render_template('admin_resumen.html',
        total_dia=total_dia, total_pedidos=len(todos),
        pagados=pagados, pendientes=pendientes,
        cobros_pendientes=cobros_pendientes, ultimos=todos[:10], hoy=hoy)

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
        hoy = ahora().strftime("%d/%m/%Y")
        with _conn() as c:
            c.execute("DELETE FROM inventario WHERE tipo='pulpa' AND fecha=?", (hoy,))
        for p in data.get('pulpas', []):
            if p.get('nombre','').strip():
                upsert_inventario(p['nombre'].strip(), 'pulpa', int(p.get('stock',0)), 3)
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
        stock     = get_stock_dict()
        total_piz = sum(i["cantidad"] for i in items if i["tipo"]=="Pizza")
        masas     = stock.get("Pizza (masa)")
        if masas is not None and total_piz > masas:
            return jsonify({'error': f'Solo quedan {masas} masa(s) de pizza disponibles'}), 400
        p = nuevo_pedido(codigo, session['nombre'], items, notas, franja)
        descontar_inventario(items)
        # Si es solo bebidas (sin pizzas), salta cocina → directo a Listo
        solo_bebidas = all(i["tipo"] != "Pizza" for i in items)
        if solo_bebidas and not cobrar_ya:
            with _conn() as c:
                c.execute("UPDATE pedidos SET estado='Listo' WHERE id=?", (p['id'],))
        if cobrar_ya:
            registrar_pago(p['id'], p['total'], metodo_pago, session['nombre'])
            return jsonify({'ok':True,'id':p['id'],'cobrado':True,'solo_bebidas':solo_bebidas})
        return jsonify({'ok':True,'id':p['id'],'cobrado':False,'solo_bebidas':solo_bebidas})
    stock  = get_stock_dict()
    pulpas = get_pulpas_hoy()
    return render_template('mesero_nuevo.html',
        sabores=get_catalogo_pizzas(), bebidas=get_catalogo_bebidas(), franjas=FRANJAS_HORA,
        stock_json=json.dumps(stock), pulpas_json=json.dumps(pulpas))

@app.route('/mesero/pedidos')
@rol_required('Mesero')
def mesero_pedidos():
    mis = [p for p in get_pedidos() if p["mesero"]==session['nombre']]
    return render_template('mesero_pedidos.html', pedidos=mis)

@app.route('/mesero/pedido/<int:pid>/editar', methods=['GET','POST'])
@rol_required('Mesero')
def mesero_editar(pid):
    pedido = get_pedido(pid)
    if not pedido:
        return redirect(url_for('mesero_pedidos'))
    if request.method == 'POST':
        data   = request.get_json()
        items  = data.get('items',[])
        notas  = data.get('notas', pedido['notas'])
        franja = data.get('franja', pedido['franja_hora'])
        cobrar_ya   = data.get('cobrar_ya', False)
        metodo_pago = data.get('metodo_pago', '')
        if not items:
            return jsonify({'error':'El pedido no puede quedar vacío'}), 400
        restaurar_inventario(pedido['productos'])
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
                registrar_pago(pid, saldo, metodo_pago, session['nombre'])
        items_txt = ", ".join(f"{i['cantidad']}x {i['nombre']}" for i in items)
        add_notificacion(pid, pedido['mesa'], items_txt, sum(i["cantidad"]*i["precio_unit"] for i in items))
        return jsonify({'ok':True})
    pulpas = get_pulpas_hoy()
    return render_template('mesero_editar.html', pedido=pedido,
        sabores=get_catalogo_pizzas(), bebidas=get_catalogo_bebidas(), franjas=FRANJAS_HORA,
        pulpas_json=json.dumps(pulpas))

# ── CAJERO ────────────────────────────────────────────
@app.route('/cajero/cobrar')
@rol_required('Cajero')
def cajero_cobrar():
    hoy = ahora().strftime("%d/%m/%Y")
    todos_listos = [p for p in get_pedidos() if p["estado"]=="Listo"]
    pendientes_anteriores = [p for p in todos_listos if p["fecha"]!=hoy]
    de_hoy = [p for p in todos_listos if p["fecha"]==hoy]
    return render_template('cajero_cobrar.html', pedidos=de_hoy,
        pendientes_anteriores=pendientes_anteriores, hoy=hoy)

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
    with _conn() as c:
        pagos_hoy = c.execute("SELECT * FROM pagos WHERE fecha=? ORDER BY id", (hoy,)).fetchall()
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
    return render_template('cajero_caja.html', pedidos=pag, total=total,
        metodos=metodos, por_cobrador=por_cobrador, hoy=hoy)

# ── COCINA ────────────────────────────────────────────
@app.route('/cocina/pedidos')
@rol_required('Cocina')
def cocina_pedidos():
    todos   = get_pedidos()
    activos = [p for p in todos if p["estado"]=="Pendiente"]
    for p in activos:
        p["pizzas"]  = [i for i in p["productos"] if i["tipo"]=="Pizza"]
        p["bebidas"] = [i for i in p["productos"] if i["tipo"]=="Bebida"]
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
