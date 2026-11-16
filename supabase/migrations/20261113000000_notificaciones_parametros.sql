-- Notificaciones, fase A (avisos dentro de la app): plazos configurables sin tocar el código ni desplegar.
-- - notificacion_parametros: una sola fila (id = 1) con los plazos. Nadie la lee ni la escribe directo.
-- - novedades_parametros(): la lee cualquier persona con sesión (son solo números, iguales para todas las empresas).
-- - notificaciones_configurar(clave, valor): solo la superadministradora; valida el rango de cada plazo.
-- - novedades_videos_iniciados(): ids de los videos vigentes de la empresa activa que la persona ya empezó a ver
--   (video_reproduccion no se puede leer directo). Solo lectura: no modifica nada de Videos On Demand.
-- El recordatorio de clase (1 hora) queda fijo en el código por decisión del 16/11.
-- No cambia datos existentes.

create table public.notificacion_parametros (
    id integer primary key default 1 check (id = 1),
    factura_aviso_dias integer not null default 3 check (factura_aviso_dias between 1 and 30),
    video_aviso_horas integer not null default 3 check (video_aviso_horas between 1 and 23),
    reserva_aviso_horas integer not null default 24 check (reserva_aviso_horas between 1 and 168),
    pago_aviso_dias integer not null default 3 check (pago_aviso_dias between 1 and 30),
    actualizado_en timestamptz not null default now(),
    actualizado_por uuid references public.profiles(id) on delete set null
);
insert into public.notificacion_parametros (id) values (1);

alter table public.notificacion_parametros enable row level security;
revoke all on public.notificacion_parametros from public, anon, authenticated;

create or replace function public.novedades_parametros()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce((
        select jsonb_build_object('factura_aviso_dias', p.factura_aviso_dias, 'video_aviso_horas', p.video_aviso_horas,
                                  'reserva_aviso_horas', p.reserva_aviso_horas, 'pago_aviso_dias', p.pago_aviso_dias)
        from public.notificacion_parametros p
        where p.id = 1 and (select auth.uid()) is not null
    ), '{}'::jsonb);
$$;

create or replace function public.notificaciones_configurar(p_clave text, p_valor integer)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_superadmin() then
        return 'sin_permiso';
    end if;
    if p_valor is null then
        return 'valor_invalido';
    end if;
    if p_clave = 'factura_aviso_dias' and p_valor between 1 and 30 then
        update public.notificacion_parametros set factura_aviso_dias = p_valor where id = 1;
    elsif p_clave = 'video_aviso_horas' and p_valor between 1 and 23 then
        update public.notificacion_parametros set video_aviso_horas = p_valor where id = 1;
    elsif p_clave = 'reserva_aviso_horas' and p_valor between 1 and 168 then
        update public.notificacion_parametros set reserva_aviso_horas = p_valor where id = 1;
    elsif p_clave = 'pago_aviso_dias' and p_valor between 1 and 30 then
        update public.notificacion_parametros set pago_aviso_dias = p_valor where id = 1;
    elsif p_clave in ('factura_aviso_dias', 'video_aviso_horas', 'reserva_aviso_horas', 'pago_aviso_dias') then
        return 'valor_invalido';
    else
        return 'clave_invalida';
    end if;
    update public.notificacion_parametros
       set actualizado_en = now(), actualizado_por = (select auth.uid())
     where id = 1;
    return 'ok';
end;
$$;

create or replace function public.novedades_videos_iniciados()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
    select coalesce(jsonb_agg(r.id_video order by r.id_video), '[]'::jsonb)
    from public.video_reproduccion r
    join public.video v on v.id = r.id_video
    where r.id_usuario = (select auth.uid())
      and r.id_empresa = public.empresa_activa()
      and v.estado = 'publicado' and now() < v.vence_en;
$$;

revoke execute on function public.novedades_parametros(), public.notificaciones_configurar(text, integer),
    public.novedades_videos_iniciados() from public, anon;
grant execute on function public.novedades_parametros(), public.notificaciones_configurar(text, integer),
    public.novedades_videos_iniciados() to authenticated;
