-- Mi perfil editable: cada persona corrige sus propios datos y su correo.
-- - editar_mis_datos: nombre, apellido, teléfono, fecha de nacimiento y vencimiento del carné de la persona con sesión
--   (nunca de otra: no recibe id). Mismas reglas que el alta y la edición de la administradora. La cédula NO está acá:
--   la sigue cargando solo la administradora (actualizar_cedula).
-- - El correo lo cambia Supabase Auth con su doble confirmación (dirección actual y nueva). Cuando el cambio se completa,
--   el trigger copia el correo nuevo a profiles.mail: cuenta, perfil, membresías y todo lo demás siguen siendo los mismos
--   (el id no cambia).
-- No cambia datos existentes.

create or replace function public.editar_mis_datos(
    p_nombre text, p_apellido text, p_telefono text, p_fecha_nacimiento date, p_fecha_vencimiento_carne_salud date
)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id uuid := (select auth.uid());
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_tel text := nullif(btrim(coalesce(p_telefono, '')), '');
    v_carne_actual date;
begin
    if v_id is null then
        return 'sin_sesion';
    end if;
    select p.fecha_vencimiento_carne_salud into v_carne_actual from public.profiles p where p.id = v_id;
    if not found then
        return 'sin_sesion';
    end if;
    if coalesce(btrim(p_nombre), '') = '' or coalesce(btrim(p_apellido), '') = ''
       or length(btrim(p_nombre)) > 60 or length(btrim(p_apellido)) > 60
       or (v_tel is not null and v_tel !~ '^09[0-9]{7}$')
       or (p_fecha_nacimiento is not null and p_fecha_nacimiento >= v_hoy)
       -- un carné ya vencido que no se toca no impide guardar lo demás
       or (p_fecha_vencimiento_carne_salud is not null and p_fecha_vencimiento_carne_salud <= v_hoy
           and p_fecha_vencimiento_carne_salud is distinct from v_carne_actual) then
        return 'datos_invalidos';
    end if;
    update public.profiles
    set nombre = btrim(p_nombre), apellido = btrim(p_apellido), telefono = v_tel,
        fecha_nacimiento = p_fecha_nacimiento, fecha_vencimiento_carne_salud = p_fecha_vencimiento_carne_salud
    where id = v_id;
    return 'ok';
end;
$$;

revoke all on function public.editar_mis_datos(text, text, text, date, date) from public, anon;
grant execute on function public.editar_mis_datos(text, text, text, date, date) to authenticated;

create or replace function public._sincronizar_mail_perfil()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    update public.profiles set mail = lower(new.email) where id = new.id and mail is distinct from lower(new.email);
    return new;
end;
$$;

revoke all on function public._sincronizar_mail_perfil() from public, anon, authenticated;

create trigger on_auth_user_email_changed
    after update of email on auth.users
    for each row
    when (old.email is distinct from new.email and new.email is not null)
    execute function public._sincronizar_mail_perfil();
