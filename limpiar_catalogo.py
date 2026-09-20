#!/usr/bin/env python3
"""
Limpieza del catálogo de Jacapizza.

Quita los productos que nunca se vendieron y que ensucian la pantalla de pedido,
y el duplicado que se estaba cobrando como pizza.

NADA SE BORRA de verdad: se marca `activo=0`, que es como el propio sistema da
de baja un producto. Los pedidos históricos NO se tocan — viven en la tabla
`items`, que es independiente del catálogo. Si algo sale mal, se revierte
poniendo `activo=1`.

    python limpiar_catalogo.py --db ruta.db            # simulacro
    python limpiar_catalogo.py --db ruta.db --aplicar   # de verdad
"""
import argparse, sqlite3, sys

# ── Lo que se da de baja, y por qué ───────────────────────────────────
# El comentario de cada uno es lo que se verificó en los datos reales.
BAJAS = [
    ("Como Cuba libre",
     "guardado como PIZZA (descuenta masa del inventario) · 0 ventas"),
    ("Cóctel Gin Tonic",
     "guardado como PIZZA (descuenta masa del inventario) · 0 ventas"),
    ("Coca-Cola (preparada)",
     "guardado como PIZZA; se cobró 1 vez a $20.000 · el bueno es "
     "'Coca-Cola preparada' sin paréntesis, con 61 ventas a $5.000"),
    ("Cóctel- Cuba libre",
     "precio $0 (se vendería gratis) · 0 ventas · duplicado de 'Como Cuba libre'"),
    ("Cóctel- Gin tonic",
     "precio $0 (se vendería gratis) · 0 ventas · duplicado de 'Cóctel Gin Tonic'"),
    ("Jugo Natural - Mango",
     "precio $0 · 0 ventas · guion normal en vez del largo que usan los demás jugos"),
    ("Limonada hierbabuena",
     "precio $0 (se vendería gratis) · 0 ventas"),
]

# ── Lo que NO se toca, a propósito ────────────────────────────────────
NO_TOCAR = [
    ("Cola y pola", "está en $0 y ya se regaló 1 unidad, pero el precio "
                    "correcto solo lo sabe el dueño"),
    ("Agua / Agua 600ml", "las dos se venden de verdad (16 y 20 uds). Puede que "
                          "sean la botella pequeña y la de 600ml"),
]


def ventas(c, nombre):
    return c.execute("SELECT COALESCE(SUM(cantidad),0) FROM items WHERE nombre=?",
                     (nombre,)).fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--aplicar", action="store_true")
    args = ap.parse_args()

    modo = "APLICANDO" if args.aplicar else "SIMULACRO (no se escribe nada)"
    print(f"\n{'='*70}\n  LIMPIEZA DEL CATÁLOGO — {modo}\n{'='*70}")

    c = sqlite3.connect(args.db)
    c.row_factory = sqlite3.Row
    if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        print("  La base no pasa integrity_check. Parando.")
        sys.exit(1)

    antes_activos = c.execute("SELECT COUNT(*) FROM catalogo WHERE activo=1").fetchone()[0]
    antes_items   = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    antes_pedidos = c.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]

    c.execute("BEGIN IMMEDIATE")
    print(f"\n  Catálogo activo antes: {antes_activos} productos\n")
    print("  ── SE DAN DE BAJA ─────────────────────────────────────────────")
    hechas = 0
    for nombre, motivo in BAJAS:
        r = c.execute("SELECT id,tipo,precio,activo FROM catalogo WHERE nombre=?",
                      (nombre,)).fetchone()
        if not r:
            print(f"    -  {nombre:24} ya no existe")
            continue
        if not r["activo"]:
            print(f"    -  {nombre:24} ya estaba de baja")
            continue
        n = ventas(c, nombre)
        # Red de seguridad: si algo se vendió más de lo esperado, no tocarlo
        if n > 1:
            print(f"    !  {nombre:24} TIENE {n} VENTAS — se deja, revísalo a mano")
            continue
        c.execute("UPDATE catalogo SET activo=0 WHERE id=?", (r["id"],))
        hechas += 1
        print(f"    ✓  {nombre:24} ${r['precio']:>7,.0f} ({r['tipo']})")
        print(f"       {motivo}")

    print(f"\n  ── NO SE TOCAN (los decides tú) ───────────────────────────────")
    for nombre, motivo in NO_TOCAR:
        print(f"    ·  {nombre:24} {motivo}")

    # ── Comprobaciones: nada del historial puede haberse movido ──────
    print(f"\n{'='*70}\n  COMPROBACIÓN\n{'='*70}")
    d_items   = c.execute("SELECT COUNT(*) FROM items").fetchone()[0] - antes_items
    d_pedidos = c.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] - antes_pedidos
    activos   = c.execute("SELECT COUNT(*) FROM catalogo WHERE activo=1").fetchone()[0]

    print(f"\n    pedidos          {antes_pedidos} → {antes_pedidos + d_pedidos}   "
          f"({'sin cambios' if d_pedidos == 0 else 'CAMBIARON'})")
    print(f"    líneas vendidas  {antes_items} → {antes_items + d_items}   "
          f"({'sin cambios' if d_items == 0 else 'CAMBIARON'})")
    print(f"    catálogo activo  {antes_activos} → {activos}   (-{hechas})")

    # Que no se haya dado de baja algo que sí se vende
    vivos = c.execute("""SELECT DISTINCT i.nombre FROM items i
                         JOIN catalogo c ON c.nombre = i.nombre
                         WHERE c.activo = 0
                         GROUP BY i.nombre HAVING SUM(i.cantidad) > 1""").fetchall()
    print(f"    productos de baja con más de 1 venta: {len(vivos)}"
          + (f" → {[v['nombre'] for v in vivos]}" if vivos else ""))

    ok = (d_items == 0 and d_pedidos == 0 and not vivos)
    print(f"\n{'='*70}")
    print("  RESULTADO: el historial está intacto. Se puede aplicar." if ok
          else "  RESULTADO: algo se movió. NO APLICAR.")
    print("=" * 70)

    if args.aplicar and ok:
        c.commit()
        print("\n  Cambios guardados.")
        print("  Para revertir: UPDATE catalogo SET activo=1 WHERE nombre IN (...)\n")
    elif args.aplicar:
        c.rollback()
        print("\n  Deshecho por la comprobación: no se guardó nada.\n")
        sys.exit(1)
    else:
        c.rollback()
        print("\n  Simulacro: no se escribió nada. Usa --aplicar cuando quieras.\n")
    c.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
