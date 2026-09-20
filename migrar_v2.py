#!/usr/bin/env python3
"""
Migracion a la v2.0 de Jacapizza — F1.

Solo AÑADE columnas y rellena las nuevas. No borra, no renombra y no toca
ninguna columna existente. La columna `estado` de siempre se conserva.

Uso:
    python migrar_v2.py --db ruta/a/pizza_data.db            # simulacro (no escribe)
    python migrar_v2.py --db ruta/a/pizza_data.db --aplicar  # escribe de verdad

El simulacro es el modo por defecto a proposito: imprime exactamente lo que
cambiaria y compara las ventas de cada dia antes y despues. Si algun total se
mueve, hay que parar y entender por que antes de aplicar nada.
"""
import argparse, sqlite3, sys
from datetime import datetime, timedelta, timezone

TZ_COL = timezone(timedelta(hours=-5))
JORNADA_CORTE_H = 5


def ahora():
    return datetime.now(TZ_COL)


def jornada_actual():
    """La jornada corta a las 5 AM: el servicio nocturno es una sola unidad."""
    return (ahora() - timedelta(hours=JORNADA_CORTE_H)).strftime("%Y-%m-%d")


# ── Columnas nuevas ──────────────────────────────────────────────────
# Todas con DEFAULT, asi que las filas existentes quedan validas al instante
# y el codigo anterior sigue funcionando sobre esta base.
COLUMNAS = [
    ("pedidos", "jornada",        "TEXT DEFAULT ''"),
    ("pedidos", "estado_cocina",  "TEXT DEFAULT 'Pendiente'"),
    ("pedidos", "estado_barra",   "TEXT DEFAULT 'Pendiente'"),
    ("pedidos", "estado_cuenta",  "TEXT DEFAULT 'Abierta'"),
    ("pedidos", "cierre_manual",  "INTEGER DEFAULT 0"),
    ("pedidos", "mesa_num",       "TEXT DEFAULT ''"),
    ("pedidos", "tipo_venta",     "TEXT DEFAULT 'reserva'"),
    ("pedidos", "archivado",      "INTEGER DEFAULT 0"),
    ("pedidos", "archivado_por",  "TEXT DEFAULT ''"),
    ("pedidos", "ultima_ronda",   "INTEGER DEFAULT 1"),
    ("pedidos", "anulado",        "INTEGER DEFAULT 0"),
    ("pedidos", "anulado_por",    "TEXT DEFAULT ''"),
    ("pedidos", "anulado_motivo", "TEXT DEFAULT ''"),
    ("items",   "ronda",          "INTEGER DEFAULT 1"),
    ("items",   "creado_hora",    "TEXT DEFAULT ''"),
    ("items",   "nota",           "TEXT DEFAULT ''"),
    ("pagos",   "jornada",        "TEXT DEFAULT ''"),
    ("gastos",  "jornada",        "TEXT DEFAULT ''"),
    ("cierres_inventario", "jornada", "TEXT DEFAULT ''"),
]

# dd/mm/yyyy -> yyyy-mm-dd. strftime siempre rellena con ceros, asi que las
# posiciones son fijas y basta con cortar.
_ISO = "substr({c},7,4)||'-'||substr({c},4,2)||'-'||substr({c},1,2)"


def _iso(col="fecha"):
    return _ISO.format(c=col)


BACKFILL = [
    # ── 1. Fechas ordenables ─────────────────────────────────────────
    ("jornada de pedidos",
     f"UPDATE pedidos SET jornada={_iso()} WHERE (jornada IS NULL OR jornada='') AND length(fecha)=10"),
    ("jornada de pagos",
     f"UPDATE pagos SET jornada={_iso()} WHERE (jornada IS NULL OR jornada='') AND length(fecha)=10"),
    ("jornada de gastos",
     f"UPDATE gastos SET jornada={_iso()} WHERE (jornada IS NULL OR jornada='') AND length(fecha)=10"),
    ("jornada de cierres",
     f"UPDATE cierres_inventario SET jornada={_iso()} WHERE (jornada IS NULL OR jornada='') AND length(fecha)=10"),

    # ── 2. Rondas — VAN PRIMERO, y el orden importa ──────────────────
    # La unica pista de que hubo una segunda ronda es que convivan items
    # despachados y sin despachar en el mismo pedido. Marcar items como
    # despachados (paso 4) borra esa pista, asi que esto tiene que ir antes.
    ("rondas de items",
     "UPDATE items SET ronda=1 WHERE ronda IS NULL OR ronda=0"),
    ("segunda ronda en pedidos reabiertos",
     """UPDATE items SET ronda=2
        WHERE (despachado=0 OR despachado IS NULL)
          AND pedido_id IN (SELECT pedido_id FROM items WHERE despachado=1)"""),
    ("ultima ronda por pedido",
     "UPDATE pedidos SET ultima_ronda = COALESCE((SELECT MAX(ronda) FROM items WHERE pedido_id=pedidos.id),1)"),
    ("hora de creacion de items",
     """UPDATE items SET creado_hora = COALESCE((SELECT hora FROM pedidos WHERE id=items.pedido_id),'')
        WHERE creado_hora IS NULL OR creado_hora=''"""),

    # ── 3. Estado de cada estacion, deducido de los items ────────────
    ("estado de cocina (pizzas)",
     """UPDATE pedidos SET estado_cocina = CASE WHEN EXISTS(
            SELECT 1 FROM items WHERE pedido_id=pedidos.id AND tipo='Pizza'
                   AND (despachado=0 OR despachado IS NULL))
          THEN 'Pendiente' ELSE 'Entregado' END"""),
    ("estado de barra (bebidas)",
     """UPDATE pedidos SET estado_barra = CASE WHEN EXISTS(
            SELECT 1 FROM items WHERE pedido_id=pedidos.id AND tipo='Bebida'
                   AND (despachado=0 OR despachado IS NULL))
          THEN 'Pendiente' ELSE 'Entregado' END"""),

    # ── 4. EL HISTORIAL MANDA ────────────────────────────────────────
    # Un pedido en 'Listo' o 'Pagado' ya termino su ciclo: una persona lo dio
    # por entregado. Hace falta porque los pedidos de SOLO BEBIDAS saltan la
    # cocina y pasan directo a 'Listo' (app.py:1176) sin pasar por
    # marcar_listo(), asi que sus items quedaron con despachado=0 aunque se
    # sirvieron. Sin esto, 4 pedidos reales reviven como pendientes.
    ("estaciones de pedidos ya terminados",
     """UPDATE pedidos SET estado_cocina='Entregado', estado_barra='Entregado'
        WHERE estado IN ('Listo','Pagado')"""),
    ("items de pedidos terminados",
     """UPDATE items SET despachado=1
        WHERE (despachado=0 OR despachado IS NULL)
          AND pedido_id IN (SELECT id FROM pedidos WHERE estado IN ('Listo','Pagado'))"""),

    # ── 5. Estado de cuenta ──────────────────────────────────────────
    # Tampoco se recalcula desde `pagos`: antes del 22/03/2026 esa tabla no se
    # usaba y el cobro se anotaba solo en la columna `pedidos.pago`.
    ("estado de cuenta",
     "UPDATE pedidos SET estado_cuenta = CASE WHEN estado='Pagado' THEN 'Cerrada' ELSE 'Abierta' END"),
    # Marca honesta: lo cerro una persona sin que los pagos cubran el total
    # (epoca anterior a la tabla `pagos`, o descuento dado en el mostrador).
    ("marca de cierre manual",
     """UPDATE pedidos SET cierre_manual=1
        WHERE estado='Pagado'
          AND COALESCE((SELECT SUM(monto) FROM pagos WHERE pedido_id=pedidos.id),0) < total - 1"""),

    # ── 6. Archivado ─────────────────────────────────────────────────
    # Sale de las pantallas de estacion, pero NUNCA se toca estado_cuenta:
    # lo que deba plata sigue apareciendo en cobro.
    ("archivado de jornadas pasadas",
     "UPDATE pedidos SET archivado=1, archivado_por='migracion' WHERE archivado=0 AND jornada < :j"),
]

def columnas_de(c, tabla):
    return {r[1] for r in c.execute(f"PRAGMA table_info({tabla})")}


def ventas_por_jornada(c):
    """La cifra que NO se puede mover: ventas cobradas por dia."""
    return {r[0]: (r[1], r[2]) for r in c.execute(
        "SELECT fecha, COUNT(*), CAST(SUM(total) AS INT) FROM pedidos "
        "WHERE estado='Pagado' GROUP BY fecha")}


def estado_derivado(c):
    """Lo que la v2 mostrara como `estado`, a partir de las columnas nuevas."""
    return {r[0]: r[1] for r in c.execute("""
        SELECT id, CASE
            WHEN anulado=1              THEN 'Anulado'
            WHEN estado_cuenta='Cerrada' THEN 'Pagado'
            WHEN estado_cocina='Entregado' AND estado_barra='Entregado' THEN 'Listo'
            ELSE 'Pendiente' END
        FROM pedidos""")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--aplicar", action="store_true",
                    help="escribe de verdad (sin esto solo simula)")
    args = ap.parse_args()

    modo = "APLICANDO" if args.aplicar else "SIMULACRO (no se escribe nada)"
    print(f"\n{'='*66}\n  MIGRACION v2.0 — {modo}\n  base: {args.db}\n{'='*66}")

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=15000")

    integridad = c.execute("PRAGMA integrity_check").fetchone()[0]
    if integridad != "ok":
        print(f"\n  LA BASE NO PASA integrity_check: {integridad}")
        sys.exit(1)

    antes_ventas = ventas_por_jornada(c)
    antes_estado = {r[0]: r[1] for r in c.execute("SELECT id, estado FROM pedidos")}
    print(f"\n  pedidos={len(antes_estado)}  jornadas con venta={len(antes_ventas)}"
          f"  total historico=${sum(v[1] for v in antes_ventas.values()):,}")

    # Todo dentro de una transaccion. En simulacro se deshace al final, asi que
    # se puede ver el resultado exacto sin arriesgar nada.
    c.execute("BEGIN IMMEDIATE")

    print("\n  --- COLUMNAS ---")
    nuevas = 0
    for tabla, col, tipo in COLUMNAS:
        if col in columnas_de(c, tabla):
            continue
        c.execute(f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo}")
        print(f"    + {tabla}.{col}")
        nuevas += 1
    print(f"    {nuevas} añadidas, {len(COLUMNAS)-nuevas} ya existian")

    print("\n  --- RELLENO ---")
    j = jornada_actual()
    for etiqueta, sql in BACKFILL:
        cur = c.execute(sql, {"j": j}) if ":j" in sql else c.execute(sql)
        print(f"    {cur.rowcount:>6} filas  {etiqueta}")

    # ── La comprobacion que decide si se sigue o se para ──────────────
    print(f"\n  {'='*62}\n  COMPROBACION\n  {'='*62}")

    despues_ventas = ventas_por_jornada(c)
    movidas = [(f, antes_ventas.get(f), despues_ventas.get(f))
               for f in set(antes_ventas) | set(despues_ventas)
               if antes_ventas.get(f) != despues_ventas.get(f)]

    derivado = estado_derivado(c)
    difieren = [(i, antes_estado[i], derivado[i]) for i in antes_estado
                if antes_estado[i] != derivado.get(i)]

    print(f"\n  Jornadas con las ventas movidas: {len(movidas)}")
    for f, a, d in sorted(movidas)[:10]:
        print(f"     {f}  antes={a}  despues={d}")

    print(f"\n  Pedidos cuyo estado derivado NO coincide con el guardado: {len(difieren)}")
    for i, a, d in difieren[:10]:
        print(f"     #{i}  guardado='{a}'  derivado='{d}'")

    manual = c.execute("SELECT COUNT(*), CAST(COALESCE(SUM(total),0) AS INT) "
                       "FROM pedidos WHERE cierre_manual=1").fetchone()
    print(f"\n  Marcados como cierre manual: {manual[0]} pedidos (${manual[1]:,})")
    if manual[0]:
        print("     (cerrados por una persona sin que los pagos cubran el total:")
        print("      epoca anterior a la tabla `pagos`, o descuento en el mostrador)")
        for r in c.execute("SELECT id, fecha, codigo, CAST(total AS INT) t, pago "
                           "FROM pedidos WHERE cierre_manual=1 ORDER BY id"):
            print(f"      #{r['id']:<4} {r['fecha']}  {r['codigo']:<16} ${r['t']:>7,}  pago={r['pago']}")

    ok = not movidas and not difieren
    print(f"\n  {'='*62}")
    if ok:
        print("  RESULTADO: ninguna cifra historica se movio. Se puede aplicar.")
    else:
        print("  RESULTADO: HAY CAMBIOS SIN EXPLICAR. NO APLICAR.")
    print(f"  {'='*62}")

    if args.aplicar and ok:
        c.commit()
        print("\n  Cambios guardados.\n")
    elif args.aplicar:
        c.rollback()
        print("\n  Deshecho por la comprobacion: no se guardo nada.\n")
        sys.exit(1)
    else:
        c.rollback()
        print("\n  Simulacro: no se escribio nada. Usa --aplicar cuando estes listo.\n")
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
