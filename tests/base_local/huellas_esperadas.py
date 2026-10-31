"""Calcula las huellas de estructura que DEBE tener producción después de cada migración de multiempresa
(1, 2, 3, 3b, 3c y 4), reconstruyendo producción en un Postgres LOCAL desde un respaldo y aplicándolas una por
una. Sirve para verificar la etapa B del despliegue con lecturas. No toca Supabase.

Uso:  python tests/base_local/huellas_esperadas.py "<carpeta del respaldo>"   (escribe huellas_esperadas.json ahí)
"""
import json
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)
import copia_produccion  # noqa: E402
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402

RESPALDO = sys.argv[1]
srv, uri, directorio = localdb.iniciar()
su = conectar(uri)
SQL = open(os.path.join(RESPALDO, "huellas_estructura.sql")).read()
huellas = lambda: {r["categoria"]: [r["cantidad"], r["huella"]] for r in q(su, SQL)}
MULTI = [a for a in localdb.migraciones() if "_multiempresa_" in os.path.basename(a)]
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if a not in MULTI and not a.endswith("20261021000000_resumen_mensual_pagos.sql"):
            cur.execute(open(a).read())
copia_produccion.cargar(su, RESPALDO)
resultado = {"antes": huellas()}
prod = json.load(open(os.path.join(RESPALDO, "huellas_produccion.json")))
assert resultado["antes"] == prod, "la reconstrucción local no coincide con producción"
for a in MULTI:
    with su.cursor() as cur:
        cur.execute(open(a).read())
    nombre = os.path.basename(a).split("_", 1)[1].removesuffix(".sql")
    resultado[nombre] = huellas()
    print(nombre, {k: v[0] for k, v in resultado[nombre].items()})
with open(os.path.join(RESPALDO, "huellas_esperadas.json"), "w") as f:
    json.dump(resultado, f, indent=1, sort_keys=True)
localdb.borrar(srv, directorio)
print("huellas esperadas guardadas; la reconstrucción local coincide con producción antes de migrar")
