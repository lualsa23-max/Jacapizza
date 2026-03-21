from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
import sqlite3, os, csv, io, json
from datetime import datetime, timedelta
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'jacapizza-secret-2024-xK9!')

DB_PATH = os.environ.get('DB_PATH', '/data/pizza_data.db')

# Crear directorio de la BD si no existe
_db_dir = os.path.dirname(DB_PATH)
if _db_dir and not os.path.exists(_db_dir):
    try:
        os.makedirs(_db_dir, exist_ok=True)
    except Exception:
        DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pizza_data.db')

USUARIOS = {
    "admin":   {"password": "admin123",  "rol": "Administrador", "nombre": "Luis Sarmiento"},
    "mesero1": {"password": "mesero123", "rol": "Mesero",        "nombre": "Daniela Suárez"},
    "cajero1": {"password": "cajero123", "rol": "Cajero",        "nombre": "Caren Muñetón"},
    "cocina1": {"password": "cocina123", "rol": "Cocina",        "nombre": "Chef y Chefa"},
}
SABORES_PIZZA = {
    "Hawaiana": 20000, "Pollo con Champiñones": 20000,
    "Mexicana": 20000, "Pepperoni": 20000,
    "Criolla":  20000, "Vegetariana": 20000,
}
BEBIDAS = {
    "Gaseosa": 4000, "Agua 600ml": 4000, "Soda Italiana": 5000,
    "Cerveza Águila": 4000, "Cerveza Águila Light": 4000, "Cerveza Coronita": 5000,
    "Jugo Natural (agua)": 7000, "Limonada de Coco": 7000, "Cerezada": 7000,
}
_INV_ESTANDAR = {
    "Pizza (masa)":         ("pizza",  3),
    "Agua 600ml":           ("bebida", 5),
    "Gaseosa":              ("bebida", 5),
    "Cerveza Águila":       ("bebida", 5),
    "Cerveza Águila Light": ("bebida", 5),
    "Cerveza Coronita":     ("bebida", 5),
}
FRANJAS_HORA = [
    "7:00 PM","7:15 PM","7:30 PM","7:45 PM",
    "8:00 PM","8:15 PM","8:30 PM","8:45 PM","9:00 PM",
]

@app.template_filter('fromjson')
def fromjson_filter(value):
    try: return json.loads(value)
    except: return {}

@app.template_filter('cop')
def fmt_cop(valor):
    try: return f"${float(valor):,.0f}".replace(",", ".")
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
            alerta_min INTEGER DEFAULT 5, fecha TEXT NOT NULL
        );
        """)
        for col in ["ALTER TABLE pedidos ADD COLUMN notas TEXT DEFAULT ''",
                    "ALTER TABLE pedidos ADD COLUMN franja_hora TEXT DEFAULT ''"]:
            try: c.execute(col)
            except: pass

init_db()

def _pedido_from_row(row, items):
    return {
        "id": row["id"], "mesa": row["codigo"], "mesero": row["mesero"],
        "estado": row["estado"], "total": row["total"], "hora": row["hora"],
        "fecha": row["fecha"], "pago": row["pago"], "modificado": bool(row["modificado"]),
        "notas": row["notas"] or "", "franja_hora": row["franja_hora"] or "", "productos": items,
    }

def _get_items(c, pid):
    rows = c.execute("SELECT * FROM items WHERE pedido_id=?", (pid,)).fetchall()
    return [{"nombre": r["nombre"], "tipo": r["tipo"], "cantidad": r["cantidad"], "precio_unit": r["precio_unit"]} for r in rows]

def get_pedidos():
    with _conn() as c:
        rows = c.execute("SELECT * FROM pedidos ORDER BY id DESC").fetchall()
        return [_pedido_from_row(r, _get_items(c, r["id"])) for r in rows]

def get_pedido(pid):
    with _conn() as c:
        row = c.execute("SELECT * FROM pedidos WHERE id=?", (pid,)).fetchone()
        if not row: return None
        return _pedido_from_row(row, _get_items(c, pid))

def nuevo_pedido(mesa, mesero, items, notas="", franja_hora=""):
    total = sum(i["cantidad"] * i["precio_unit"] for i in items)
    hora  = datetime.now().strftime("%H:%M")
    fecha = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO pedidos (codigo,mesero,estado,total,hora,fecha,notas,franja_hora) VALUES (?,?,?,?,?,?,?,?)",
            (mesa, mesero, "Pendiente", total, hora, fecha, notas, franja_hora))
        pid = cur.lastrowid
        for i in items:
            c.execute("INSERT INTO items (pedido_id,nombre,tipo,cantidad,precio_unit) VALUES (?,?,?,?,?)",
                      (pid, i["nombre"], i["tipo"], i["cantidad"], i["precio_unit"]))
    return get_pedido(pid)

def cobrar_pedido(pid, metodo):
    with _conn() as c:
        c.execute("UPDATE pedidos SET estado='Pagado', pago=? WHERE id=?", (metodo, pid))

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
    hoy = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT * FROM inventario WHERE fecha=? ORDER BY tipo,nombre", (hoy,)).fetchall()
        return [{"id": r["id"], "nombre": r["nombre"], "tipo": r["tipo"],
                 "stock": r["stock"], "alerta_min": r["alerta_min"]} for r in rows]

def get_stock_dict():
    hoy = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute("SELECT nombre, stock FROM inventario WHERE fecha=?", (hoy,)).fetchall()
        return {r["nombre"]: r["stock"] for r in rows}

def upsert_inventario(nombre, tipo, stock, alerta_min=None):
    hoy = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        ex = c.execute("SELECT id,alerta_min FROM inventario WHERE nombre=? AND fecha=?", (nombre, hoy)).fetchone()
        if ex:
            amin = alerta_min if alerta_min is not None else ex["alerta_min"]
            c.execute("UPDATE inventario SET stock=?,alerta_min=? WHERE id=?", (max(0, stock), amin, ex["id"]))
        else:
            amin = alerta_min if alerta_min is not None else (3 if tipo == "pizza" else 5)
            c.execute("INSERT INTO inventario (nombre,tipo,stock,alerta_min,fecha) VALUES (?,?,?,?,?)",
                      (nombre, tipo, max(0, stock), amin, hoy))

def ajustar_stock(nombre, delta):
    hoy = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        c.execute("UPDATE inventario SET stock=MAX(0,stock+?) WHERE nombre=? AND fecha=?", (delta, nombre, hoy))

def _item_a_stock_key(nombre, tipo):
    if tipo == "Pizza": return "Pizza (masa)"
    for key in ["Gaseosa","Agua 600ml","Cerveza Águila Light","Cerveza Águila","Cerveza Coronita"]:
        if nombre.startswith(key): return key
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
    hoy = datetime.now().strftime("%d/%m/%Y")
    with _conn() as c:
        rows = c.execute(
            "SELECT nombre,stock,alerta_min FROM inventario WHERE tipo='pulpa' AND fecha=? ORDER BY nombre", (hoy,)).fetchall()
        return [{"nombre": r["nombre"], "stock": r["stock"], "alerta_min": r["alerta_min"]} for r in rows]

# ====================== AUTH ======================
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'usuario' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def rol_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'usuario' not in session:
                return redirect(url_for('login'))
            # Administrador tiene acceso a todo
            if session['rol'] not in roles and session['rol'] != 'Administrador':
                return redirect(url_for('dashboard'))
            return f(*args, **kwargs)
        return decorated
    return decorator

# ====================== ROUTES ======================
@app.route('/')
def index():
    if 'usuario' in session: return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        u = request.form.get('usuario', '').strip()
        p = request.form.get('password', '').strip()
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

@app.route('/admin/resumen')
@rol_required('Administrador')
def admin_resumen():
    hoy   = datetime.now().strftime("%d/%m/%Y")
    todos = get_pedidos()
    total_dia  = sum(p["total"] for p in todos if p["fecha"] == hoy and p["estado"] == "Pagado")
    pagados    = sum(1 for p in todos if p["estado"] == "Pagado")
    pendientes = sum(1 for p in todos if p["estado"] == "Pendiente")
    return render_template('admin_resumen.html',
        total_dia=total_dia, total_pedidos=len(todos),
        pagados=pagados, pendientes=pendientes, ultimos=todos[:10], hoy=hoy)

@app.route('/admin/inventario', methods=['GET', 'POST'])
@rol_required('Administrador')
def admin_inventario():
    if request.method == 'POST':
        data = request.get_json()
        for nombre, (tipo, _) in _INV_ESTANDAR.items():
            stock  = int(data.get(f'stock_{nombre}', 0))
            alerta = int(data.get(f'alerta_{nombre}', 5))
            upsert_inventario(nombre, tipo, stock, alerta)
        hoy = datetime.now().strftime("%d/%m/%Y")
        with _conn() as c:
            c.execute("DELETE FROM inventario WHERE tipo='pulpa' AND fecha=?", (hoy,))
        for p in data.get('pulpas', []):
            if p.get('nombre', '').strip():
                upsert_inventario(p['nombre'].strip(), 'pulpa', int(p.get('stock', 0)), 3)
        return jsonify({'ok': True})
    inv_dict = {i["nombre"]: i for i in get_inventario_hoy()}
    pulpas   = get_pulpas_hoy()
    return render_template('admin_inventario.html', inv_estandar=_INV_ESTANDAR, inv_dict=inv_dict, pulpas=pulpas)

@app.route('/admin/pedidos')
@rol_required('Administrador')
def admin_pedidos():
    return render_template('admin_pedidos.html', pedidos=get_pedidos())

@app.route('/admin/pedido/<int:pid>/eliminar', methods=['POST'])
@rol_required('Administrador')
def admin_eliminar_pedido(pid):
    with _conn() as c:
        c.execute("DELETE FROM items WHERE pedido_id=?", (pid,))
        c.execute("DELETE FROM notificaciones WHERE pid=?", (pid,))
        c.execute("DELETE FROM pedidos WHERE id=?", (pid,))
    flash(f'🗑 Pedido #{pid} eliminado', 'success')
    return redirect(url_for('admin_pedidos'))

@app.route('/admin/reportes')
@rol_required('Administrador')
def admin_reportes():
    hoy     = datetime.now().strftime("%d/%m/%Y")
    periodo = request.args.get('periodo', 'hoy')
    fi      = request.args.get('fi', hoy)
    ff      = request.args.get('ff', hoy)
    if periodo == 'hoy':     fi = ff = hoy
    elif periodo == 'semana':
        now = datetime.now()
        fi  = (now - timedelta(days=now.weekday())).strftime("%d/%m/%Y"); ff = hoy
    elif periodo == 'mes':
        now = datetime.now(); fi = f"01/{now.month:02d}/{now.year}"; ff = hoy
    data = get_reporte(fi, ff)
    return render_template('admin_reportes.html', data=data, periodo=periodo, fi=fi, ff=ff, hoy=hoy)

@app.route('/admin/reportes/csv')
@rol_required('Administrador')
def admin_csv():
    fi = request.args.get('fi', '')
    ff = request.args.get('ff', '')
    with _conn() as c:
        rows = c.execute(
            "SELECT p.id,p.codigo,p.mesero,p.estado,p.total,p.hora,p.fecha,p.pago,"
            "i.nombre,i.tipo,i.cantidad,i.precio_unit "
            "FROM pedidos p JOIN items i ON i.pedido_id=p.id "
            "WHERE p.estado='Pagado' AND p.fecha>=? AND p.fecha<=? ORDER BY p.id",
            (fi, ff)).fetchall()
    out = io.StringIO()
    w   = csv.writer(out)
    w.writerow(["ID","Código","Mesero","Estado","Total","Hora","Fecha","Pago","Ítem","Tipo","Cantidad","Precio"])
    for r in rows: w.writerow(list(r))
    return Response(out.getvalue(), mimetype='text/csv',
                    headers={"Content-Disposition": f"attachment;filename=reporte_{fi}_{ff}.csv"})

@app.route('/admin/usuarios', methods=['GET', 'POST'])
@rol_required('Administrador')
def admin_usuarios():
    if request.method == 'POST':
        action = request.form.get('action')
        u = request.form.get('username', '').strip()
        if action == 'update' and u in USUARIOS:
            USUARIOS[u]['nombre']   = request.form.get('nombre', '').strip() or USUARIOS[u]['nombre']
            USUARIOS[u]['password'] = request.form.get('password', '').strip() or USUARIOS[u]['password']
            USUARIOS[u]['rol']      = request.form.get('rol', 'Mesero')
            flash(f'Usuario @{u} actualizado ✅', 'success')
        elif action == 'delete' and u in USUARIOS and u != 'admin':
            del USUARIOS[u]; flash(f'Usuario @{u} eliminado', 'success')
        elif action == 'create':
            nu = request.form.get('new_username', '').strip()
            nn = request.form.get('new_nombre', '').strip()
            np = request.form.get('new_password', '').strip()
            nr = request.form.get('new_rol', 'Mesero')
            if nu and nn and np and nu not in USUARIOS:
                USUARIOS[nu] = {'password': np, 'rol': nr, 'nombre': nn}
                flash(f'Usuario @{nu} creado ✅', 'success')
            elif nu in USUARIOS:
                flash(f'El usuario @{nu} ya existe', 'error')
        return redirect(url_for('admin_usuarios'))
    return render_template('admin_usuarios.html', usuarios=USUARIOS, roles=["Administrador","Mesero","Cajero","Cocina"])

@app.route('/admin/menu/pizzas', methods=['GET', 'POST'])
@rol_required('Administrador')
def admin_menu_pizzas():
    if request.method == 'POST':
        _handle_menu(request.form, SABORES_PIZZA)
        return redirect(url_for('admin_menu_pizzas'))
    return render_template('admin_menu.html', menu=SABORES_PIZZA, tipo='pizzas', titulo='Menú Pizzas', icono='🍕')

@app.route('/admin/menu/bebidas', methods=['GET', 'POST'])
@rol_required('Administrador')
def admin_menu_bebidas():
    if request.method == 'POST':
        _handle_menu(request.form, BEBIDAS)
        return redirect(url_for('admin_menu_bebidas'))
    return render_template('admin_menu.html', menu=BEBIDAS, tipo='bebidas', titulo='Menú Bebidas', icono='🥤')

def _handle_menu(form, menu_dict):
    action = form.get('action')
    if action == 'update':
        old = form.get('old_name'); new = form.get('new_name', '').strip()
        precio = int(form.get('precio', 0) or 0)
        if old in menu_dict and new:
            items = list(menu_dict.items())
            idx   = next((i for i, kv in enumerate(items) if kv[0] == old), None)
            if idx is not None:
                items[idx] = (new, precio); menu_dict.clear(); menu_dict.update(items)
    elif action == 'delete':
        name = form.get('name')
        if name in menu_dict and len(menu_dict) > 1: del menu_dict[name]
    elif action == 'add':
        name = form.get('name', '').strip(); precio = int(form.get('precio', 0) or 0)
        if name: menu_dict[name] = precio

@app.route('/mesero/nuevo', methods=['GET', 'POST'])
@rol_required('Mesero')
def mesero_nuevo():
    if request.method == 'POST':
        data       = request.get_json()
        codigo     = data.get('codigo', '').strip()
        items      = data.get('items', [])
        notas      = data.get('notas', '')
        franja     = data.get('franja', FRANJAS_HORA[0])
        cobrar_ya  = data.get('cobrar_ya', False)
        metodo_pago= data.get('metodo_pago', 'Efectivo')
        if not codigo or not items:
            return jsonify({'error': 'Datos incompletos'}), 400
        stock = get_stock_dict()
        total_piz = sum(i["cantidad"] for i in items if i["tipo"] == "Pizza")
        masas = stock.get("Pizza (masa)")
        if masas is not None and total_piz > masas:
            return jsonify({'error': f'Solo quedan {masas} masa(s) de pizza disponibles'}), 400
        p = nuevo_pedido(codigo, session['nombre'], items, notas, franja)
        descontar_inventario(items)
        # Si cobrar_ya, registrar el pago pero dejar estado Pendiente para que cocina lo vea
        if cobrar_ya:
            with _conn() as c:
                c.execute("UPDATE pedidos SET pago=? WHERE id=?", (metodo_pago, p['id']))
            return jsonify({'ok': True, 'id': p['id'], 'cobrado': True})
        return jsonify({'ok': True, 'id': p['id'], 'cobrado': False})
    stock  = get_stock_dict()
    pulpas = get_pulpas_hoy()
    return render_template('mesero_nuevo.html',
        sabores=SABORES_PIZZA, bebidas=BEBIDAS, franjas=FRANJAS_HORA,
        stock_json=json.dumps(stock), pulpas_json=json.dumps(pulpas))

@app.route('/mesero/pedidos')
@rol_required('Mesero')
def mesero_pedidos():
    mis = [p for p in get_pedidos() if p["mesero"] == session['nombre']]
    return render_template('mesero_pedidos.html', pedidos=mis)

@app.route('/mesero/pedido/<int:pid>/editar', methods=['GET', 'POST'])
@rol_required('Mesero')
def mesero_editar(pid):
    pedido = get_pedido(pid)
    if not pedido or pedido['estado'] in ['Pagado']:
        return redirect(url_for('mesero_pedidos'))
    if request.method == 'POST':
        data   = request.get_json()
        items  = data.get('items', [])
        notas  = data.get('notas', pedido['notas'])
        franja = data.get('franja', pedido['franja_hora'])
        if not items:
            return jsonify({'error': 'El pedido no puede quedar vacío'}), 400
        restaurar_inventario(pedido['productos'])
        actualizar_pedido(pid, items, notas, franja)
        descontar_inventario(items)
        # Si estaba Listo, volver a Pendiente para que cocina lo vea de nuevo
        if pedido['estado'] == 'Listo':
            with _conn() as c:
                c.execute("UPDATE pedidos SET estado='Pendiente' WHERE id=?", (pid,))
        items_txt = ", ".join(f"{i['cantidad']}x {i['nombre']}" for i in items)
        add_notificacion(pid, pedido['mesa'], items_txt, sum(i["cantidad"] * i["precio_unit"] for i in items))
        return jsonify({'ok': True})
    pulpas = get_pulpas_hoy()
    return render_template('mesero_editar.html', pedido=pedido,
        sabores=SABORES_PIZZA, bebidas=BEBIDAS, franjas=FRANJAS_HORA,
        pulpas_json=json.dumps(pulpas))

@app.route('/cajero/cobrar')
@rol_required('Cajero')
def cajero_cobrar():
    listos = [p for p in get_pedidos() if p["estado"] == "Listo"]
    return render_template('cajero_cobrar.html', pedidos=listos)

@app.route('/cajero/cobrar/<int:pid>', methods=['POST'])
@rol_required('Cajero')
def cajero_pagar(pid):
    metodo = request.form.get('metodo', 'Efectivo')
    cobrar_pedido(pid, metodo)
    flash(f'✅ Pedido #{pid} cobrado — {metodo}', 'success')
    return redirect(url_for('cajero_cobrar'))

@app.route('/cajero/caja')
@rol_required('Cajero')
def cajero_caja():
    hoy = datetime.now().strftime("%d/%m/%Y")
    pag = [p for p in get_pedidos() if p["estado"] == "Pagado" and p["fecha"] == hoy]
    total   = sum(p["total"] for p in pag)
    metodos = {}
    for p in pag:
        m = p["pago"] or "N/A"
        metodos[m] = metodos.get(m, 0) + p["total"]
    return render_template('cajero_caja.html', pedidos=pag, total=total, metodos=metodos, hoy=hoy)

@app.route('/cajero/cobrar/<int:pid>/confirmar_pago', methods=['POST'])
@rol_required('Cajero')
def cajero_confirmar_pago(pid):
    """Confirma un pedido que ya fue pagado por el mesero — solo cambia estado a Pagado"""
    with _conn() as c:
        c.execute("UPDATE pedidos SET estado='Pagado' WHERE id=? AND pago IS NOT NULL", (pid,))
    flash(f'✅ Pedido #{pid} confirmado como pagado', 'success')
    return redirect(url_for('cajero_cobrar'))

@app.route('/cocina/pedidos')
@rol_required('Cocina')
def cocina_pedidos():
    todos   = get_pedidos()
    activos = [p for p in todos if p["estado"] == "Pendiente"]
    # Pre-separar pizzas y bebidas por pedido para simplificar el template
    for p in activos:
        p["pizzas"]  = [i for i in p["productos"] if i["tipo"] == "Pizza"]
        p["bebidas"] = [i for i in p["productos"] if i["tipo"] == "Bebida"]
    grupos = {}
    for p in activos:
        k = p.get("franja_hora") or "Sin hora"
        grupos.setdefault(k, []).append(p)
    franjas_ord = [f for f in FRANJAS_HORA if f in grupos]
    if "Sin hora" in grupos:
        franjas_ord.append("Sin hora")
    return render_template('cocina_pedidos.html',
        activos=activos, grupos=grupos, franjas_ord=franjas_ord)

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
    activos = sum(1 for p in get_pedidos() if p["estado"] == "Pendiente")
    return jsonify({"count": activos})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
