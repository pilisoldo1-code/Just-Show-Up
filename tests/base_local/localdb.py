"""Postgres local descartable que replica el esquema de Supabase aplicando TODAS las migraciones
del repo, con datos sintéticos. No toca Supabase. Necesita requirements-dev.txt (pgserver, psycopg2).

Lo que Supabase trae y un Postgres común no (roles anon/authenticated, auth.users, auth.uid(),
storage) se imita con SHIM. auth.uid() lee la sesión de 'request.jwt.claim.sub', igual que Supabase.
"""
import glob
import hashlib
import json
import os
import shutil
import tempfile

import pgserver
import psycopg2
import psycopg2.extras

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SHIM = """
create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
create schema auth;
grant usage on schema auth to anon, authenticated, service_role;
create table auth.users (id uuid primary key, email text, raw_user_meta_data jsonb default '{}'::jsonb);
create function auth.uid() returns uuid language sql stable as
  $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
grant execute on function auth.uid() to anon, authenticated, service_role;
create schema storage;
grant usage on schema storage to anon, authenticated, service_role;
create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint,
                              allowed_mime_types text[]);
create table storage.objects (id bigserial primary key, bucket_id text references storage.buckets(id), name text);
alter table storage.objects enable row level security;
grant all on storage.objects to anon, authenticated;
grant usage on sequence storage.objects_id_seq to anon, authenticated;
-- Supabase Vault (sin cifrado real): como en Supabase, anon/authenticated no tienen acceso.
create schema vault;
create table vault.secrets (id uuid primary key default gen_random_uuid(), name text unique,
                            description text, secret text not null);
create view vault.decrypted_secrets as select id, name, description, secret, secret as decrypted_secret from vault.secrets;
create function vault.create_secret(new_secret text, new_name text default null, new_description text default '',
                                    new_key_id uuid default null) returns uuid language sql as
  $$ insert into vault.secrets (name, description, secret) values (new_name, new_description, new_secret) returning id $$;
create function vault.update_secret(secret_id uuid, new_secret text default null, new_name text default null,
                                    new_description text default null, new_key_id uuid default null) returns void language sql as
  $$ update vault.secrets set secret = coalesce(new_secret, secret), name = coalesce(new_name, name),
       description = coalesce(new_description, description) where id = secret_id $$;
revoke all on schema vault from public;
"""

# Reloj simulado (solo en esta base): las funciones de facturación usan public._ahora().
RELOJ = """
create or replace function public._ahora() returns timestamptz language sql stable set search_path = '' as
  $$ select coalesce(nullif(current_setting('prueba.ahora', true), '')::timestamptz, now()) $$;
"""


def iniciar():
    """Levanta un Postgres vacío en un directorio temporal y devuelve (servidor, uri)."""
    d = tempfile.mkdtemp(prefix="jsu-base-local-")
    srv = pgserver.get_server(d, cleanup_mode="stop")
    return srv, srv.get_uri(), d


def borrar(srv, directorio):
    srv.cleanup()
    shutil.rmtree(directorio, ignore_errors=True)


def migraciones():
    return sorted(glob.glob(os.path.join(REPO, "supabase/migrations/*.sql")))


def conectar(uri, uid=None, rol=None):
    c = psycopg2.connect(uri)
    c.autocommit = True
    if rol:
        with c.cursor() as cur:
            cur.execute(f"set role {rol}")
            if uid:
                cur.execute("select set_config('request.jwt.claim.sub', %s, false)", (uid,))
    return c


def q(conn, sql, args=None, uno=False):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, args)
        if cur.description is None:
            return None
        filas = [dict(r) for r in cur.fetchall()]
        return (filas[0] if filas else None) if uno else filas


def huella(conn, tabla, columnas="*"):
    filas = q(conn, f"select {columnas} from public.{tabla} order by 1")
    return len(filas), hashlib.sha256(json.dumps(filas, sort_keys=True, default=str).encode()).hexdigest()[:12]
