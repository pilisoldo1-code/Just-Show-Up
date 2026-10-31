"""Genera la migración 3 de multiempresa y su vuelta atrás.

- manual.sql: funciones reescritas a mano (recorridos por empresa, roles, Mercado Pago, empresas).
- SOLO_ADMIN: funciones que no cambian salvo que pasan a ser solo de la administradora; se toman de la
  base reconstruida con las migraciones anteriores y se reemplaza public.es_profesora() por public.es_admin().
- Vuelta atrás: la definición original exacta de cada función reemplazada y el borrado de las nuevas.

Uso (con requirements-dev.txt):  python scripts/multiempresa_3/generar.py
"""
import os
import re
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(AQUI))
sys.path.insert(0, os.path.join(REPO, "tests", "base_local"))
import localdb  # noqa: E402
from localdb import conectar, q  # noqa: E402

MIGRACION = os.path.join(REPO, "supabase/migrations/20261031000300_multiempresa_3_funciones.sql")
VUELTA = os.path.join(REPO, "supabase/pendientes/vuelta_atras_multiempresa_3.sql")
SOLO_ADMIN = ["confirmar_factura", "registrar_pago", "anular_pago", "anular_factura", "ajustar_linea",
              "quitar_ajuste_linea", "descuento_general", "actualizar_cedula", "crear_tipo_clase",
              "aplicar_cambio_tipo", "previsualizar_cambio_tipo", "cambiar_estado_tipo_clase", "cambiar_imagen_tipo_clase"]

srv, uri, d = localdb.iniciar()
su = conectar(uri)
with su.cursor() as cur:
    cur.execute(localdb.SHIM)
    for a in localdb.migraciones():
        if os.path.basename(a) < os.path.basename(MIGRACION):
            cur.execute(open(a).read())


def definiciones(nombre):
    return q(su, """select pg_get_functiondef(p.oid) d, pg_get_function_identity_arguments(p.oid) a
                    from pg_proc p where p.pronamespace = 'public'::regnamespace and p.proname = %s""", (nombre,))


manual = open(os.path.join(AQUI, "manual.sql")).read()
generadas = []
for nombre in SOLO_ADMIN:
    defs = definiciones(nombre)
    assert len(defs) == 1, (nombre, len(defs))
    texto = defs[0]["d"]
    if "public.es_profesora()" not in texto:
        print(f"{nombre}: no usa es_profesora() directamente (la controla otra función); sin cambios")
        continue
    generadas.append(f"-- {nombre}: solo la administradora\n" + texto.replace("public.es_profesora()", "public.es_admin()").rstrip() + ";\n")

final = manual.rstrip() + "\n\n-- ---------- solo la administradora (generadas) ----------\n\n" + "\n".join(generadas)
open(MIGRACION, "w").write(final)

# vuelta atrás
nombres = sorted(set(re.findall(r"function public\.([a-z_0-9]+)\(", final, re.I)))
restaurar, borrar = [], []
for n in nombres:
    defs = definiciones(n)
    if defs:
        restaurar.append(defs[0]["d"].rstrip() + ";\n")
    else:
        sig = re.search(r"function public\." + n + r"\(([^)]*)\)", final, re.I).group(1)
        tipos = ", ".join(p.split()[-1] for p in sig.split(",") if p.strip()) if sig.strip() else ""
        borrar.append(f"drop function public.{n}({tipos});")
vuelta = """-- VUELTA ATRÁS de 20261031000300_multiempresa_3_funciones.sql (NO es una migración).
-- Generada por scripts/multiempresa_3/generar.py: deja cada función reemplazada exactamente como estaba,
-- borra las funciones nuevas, el trigger de la profesora en las líneas y bloqueo.id_profesora. Se niega si
-- hay más de una empresa. Todo o nada. Correr en el SQL Editor de Supabase solo si Julieta lo autoriza.

begin;

do $$
begin
    if (select count(*) from public.empresa) > 1 then
        raise exception 'Hay más de una empresa: sin la parte 3 sus datos se mezclarían. No se hizo nada.';
    end if;
end;
$$;

drop trigger completar_profesora_linea on public.resumen_mensual_detalle;
""" + "\n".join(borrar) + "\n\n" + "\n".join(restaurar) + """
alter table public.bloqueo drop column id_profesora;

delete from supabase_migrations.schema_migrations where name = 'multiempresa_3_funciones';

commit;
"""
open(VUELTA, "w").write(vuelta)
localdb.borrar(srv, d)
print(f"migración: {len(final.splitlines())} líneas ({len(generadas)} funciones generadas); "
      f"vuelta atrás: {len(restaurar)} restauradas, {len(borrar)} borradas")
