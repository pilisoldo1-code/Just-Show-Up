-- Módulo Tipos de clase (Gimnasia, Pilates, Yoga, ...).
-- * tipo_clase centraliza duración, precio presencial y cupo de cada disciplina. Pensada
--   también como catálogo para On Demand: en el futuro video.id_tipo_clase reusará nombre,
--   descripción e imagen. duracion / precio_presencial / cupo_max son SOLO de lo presencial;
--   los precios de On Demand van a vivir en sus propias tablas (suscripción / pago).
-- * horario.id_tipo_clase: cada horario pertenece a un tipo. Las columnas duracion, cupo_max
--   y precio de horario NO se borran todavía (el código desplegado las lee): un trigger las
--   mantiene iguales a las del tipo. Se eliminan en una fase 2.
-- * clase guarda su propia foto: precio, duración (ya existían), cupo_max e id_tipo_clase
--   (nuevas). Cambiar un tipo nunca reescribe la historia.
-- * Las funciones de reservas pasan a leer clase.cupo_max (antes horario.cupo_max).
-- Compatible con los datos existentes: crea el tipo "Gimnasia" (60 min, $800, cupo 7) y lo
-- asigna a los 10 horarios; completa las columnas nuevas de clase con los valores que ya
-- tenían. No cambia precios, reservas, listas de espera ni asistencias.

-- ---------- tabla tipo_clase ----------
create table public.tipo_clase (
    id bigint generated always as identity primary key,
    nombre text not null check (length(trim(nombre)) between 1 and 60),
    descripcion text not null default '',
    imagen_url text,
    duracion integer not null check (duracion > 0),
    precio_presencial numeric(10,2) not null check (precio_presencial >= 0),
    cupo_max integer not null check (cupo_max > 0),
    activo boolean not null default true,
    creado_en timestamptz not null default now()
);
create unique index tipo_clase_nombre_unico on public.tipo_clase (lower(trim(nombre)));
comment on table public.tipo_clase is 'Disciplina (Gimnasia, Pilates, Yoga...). Presencial: duracion, precio_presencial y cupo_max. Futuro On Demand: video.id_tipo_clase reusa nombre, descripcion e imagen_url. No se borra: se desactiva.';

alter table public.tipo_clase enable row level security;
create policy "logueadas: leer tipos de clase" on public.tipo_clase
    for select to authenticated using (true);
-- Nadie escribe directo: crear, editar, activar/desactivar e imagen van por funciones.
revoke all on public.tipo_clase from anon;
revoke insert, update, delete, truncate on public.tipo_clase from authenticated;

insert into public.tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max)
values ('Gimnasia', 'Clases de gimnasia', 60, 800, 7);

-- ---------- horario.id_tipo_clase ----------
alter table public.horario add column id_tipo_clase bigint references public.tipo_clase(id);
update public.horario set id_tipo_clase = (select id from public.tipo_clase where nombre = 'Gimnasia');
alter table public.horario alter column id_tipo_clase set not null;
create index horario_id_tipo_clase_idx on public.horario (id_tipo_clase);

-- Mantiene duracion / cupo_max / precio de horario iguales a los del tipo (compatibilidad
-- con el código que todavía los lee). Si un código anterior crea un horario sin tipo, se le
-- asigna el primer tipo activo.
create or replace function public._horario_desde_tipo()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_t record;
begin
    if new.id_tipo_clase is null then
        select id into new.id_tipo_clase from public.tipo_clase where activo order by id limit 1;
    end if;
    select duracion, cupo_max, precio_presencial into v_t
    from public.tipo_clase where id = new.id_tipo_clase;
    new.duracion := v_t.duracion;
    new.cupo_max := v_t.cupo_max;
    new.precio := v_t.precio_presencial;
    return new;
end;
$$;
create trigger horario_desde_tipo
    before insert or update on public.horario
    for each row execute function public._horario_desde_tipo();

-- ---------- clase: foto de cupo y tipo ----------
alter table public.clase add column cupo_max integer;
alter table public.clase add column id_tipo_clase bigint references public.tipo_clase(id);
update public.clase c
set cupo_max = h.cupo_max, id_tipo_clase = h.id_tipo_clase
from public.horario h where h.id = c.id_horario;
alter table public.clase alter column cupo_max set not null;
alter table public.clase add constraint clase_cupo_max_check check (cupo_max > 0);
alter table public.clase alter column id_tipo_clase set not null;
create index clase_id_tipo_clase_idx on public.clase (id_tipo_clase);

-- Si algún código inserta una clase sin cupo o sin tipo (código anterior, pruebas), se
-- completan con los del horario, para no romper nada.
create or replace function public._clase_desde_horario()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    if new.cupo_max is null or new.id_tipo_clase is null then
        select coalesce(new.cupo_max, t.cupo_max), coalesce(new.id_tipo_clase, t.id)
        into new.cupo_max, new.id_tipo_clase
        from public.horario h join public.tipo_clase t on t.id = h.id_tipo_clase
        where h.id = new.id_horario;
    end if;
    return new;
end;
$$;
create trigger clase_desde_horario
    before insert on public.clase
    for each row execute function public._clase_desde_horario();

-- ---------- generar_clases: copia los valores del tipo ----------
create or replace function public.generar_clases(p_lunes date)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
begin
    if auth.uid() is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    if extract(isodow from p_lunes) <> 1 or p_lunes < v_hoy - 7 or p_lunes > v_hoy + 28 then
        return;
    end if;
    insert into public.clase (id_horario, fecha, hora_inicio, duracion, precio, cupo_max, id_tipo_clase, estado)
    select h.id, d.fecha, h.hora_inicio, t.duracion, t.precio_presencial, t.cupo_max, t.id,
           case when public._clase_bloqueada(d.fecha, h.id) then 'cancelada' else 'programada' end
    from public.horario h
    join public.tipo_clase t on t.id = h.id_tipo_clase
    cross join lateral (
        select p_lunes + (array_position(
            array['lunes','martes','miercoles','jueves','viernes','sabado','domingo'],
            h.dia_semana) - 1) as fecha
    ) d
    where h.activo and t.activo
    on conflict (id_horario, fecha) do nothing;
end;
$$;

-- ---------- reservas y lista de espera: cupo de la clase ----------
create or replace function public._procesar_lista_espera(p_id_clase bigint)
returns void
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_c record;
    v_inicio timestamptz;
    v_libres integer;
    v_sig bigint;
begin
    select c.estado, c.fecha, c.hora_inicio, c.cupo_max into v_c
    from public.clase c
    where c.id = p_id_clase
    for update;
    if not found or v_c.estado <> 'programada' then
        return;
    end if;
    v_inicio := public._inicio_clase(v_c.fecha, v_c.hora_inicio);
    if v_inicio <= now() then
        return;
    end if;

    update public.listadeespera set estado = 'expirado'
    where id_clase = p_id_clase and estado = 'ofrecido' and oferta_vence_en <= now();

    v_libres := v_c.cupo_max
        - (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
        - (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido');
    while v_libres > 0 loop
        v_sig := null;
        select id into v_sig from public.listadeespera
        where id_clase = p_id_clase and estado = 'en_espera'
        order by posicion, fecha_ingreso, id
        limit 1;
        exit when v_sig is null;
        update public.listadeespera
        set estado = 'ofrecido', oferta_vence_en = least(now() + interval '30 minutes', v_inicio)
        where id = v_sig;
        v_libres := v_libres - 1;
    end loop;
end;
$$;

create or replace function public.reservar(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_clase record;
    v_su_oferta boolean;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    select c.* into v_clase from public.clase c where c.id = p_id_clase for update;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return 'no_disponible';
    end if;
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return 'ya_reservada';
    end if;
    perform public._procesar_lista_espera(p_id_clase);

    v_su_oferta := exists (select 1 from public.listadeespera
                           where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido');
    if not v_su_oferta and
       (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
       + (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido')
       >= v_clase.cupo_max then
        return 'completa';
    end if;
    insert into public.reserva (id_alumna, id_clase) values (v_alumna, p_id_clase);
    perform public._sincronizar_inscriptas(p_id_clase);
    update public.listadeespera set estado = 'confirmado'
    where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido');
    return 'ok';
end;
$$;

create or replace function public.anotar_en_espera(p_id_clase bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_clase record;
    v_posicion integer;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    select c.* into v_clase from public.clase c where c.id = p_id_clase for update;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    perform public._procesar_lista_espera(p_id_clase);
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return jsonb_build_object('resultado', 'ya_reservada');
    end if;
    if exists (select 1 from public.listadeespera
               where id_alumna = v_alumna and id_clase = p_id_clase and estado in ('en_espera', 'ofrecido')) then
        return jsonb_build_object('resultado', 'ya_en_espera');
    end if;
    if (select count(*) from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
       + (select count(*) from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido')
       < v_clase.cupo_max then
        return jsonb_build_object('resultado', 'hay_lugar');
    end if;
    select coalesce(max(posicion), 0) + 1 into v_posicion
    from public.listadeespera where id_clase = p_id_clase;
    insert into public.listadeespera (id_alumna, id_clase, posicion)
    values (v_alumna, p_id_clase, v_posicion);
    return jsonb_build_object('resultado', 'ok', 'posicion',
        (select count(*) from public.listadeespera
         where id_clase = p_id_clase and estado in ('en_espera', 'ofrecido') and posicion <= v_posicion));
end;
$$;

create or replace function public.confirmar_oferta(p_id_clase bigint)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_alumna uuid := auth.uid();
    v_clase record;
begin
    if v_alumna is null then
        raise exception 'requiere sesión' using errcode = '42501';
    end if;
    select c.* into v_clase from public.clase c where c.id = p_id_clase for update;
    if not found or v_clase.estado <> 'programada'
       or public._inicio_clase(v_clase.fecha, v_clase.hora_inicio) <= now() then
        return 'no_disponible';
    end if;
    perform public._procesar_lista_espera(p_id_clase);
    if exists (select 1 from public.reserva
               where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'confirmada') then
        return 'ya_reservada';
    end if;
    if not exists (select 1 from public.listadeespera
                   where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido') then
        if exists (select 1 from public.listadeespera
                   where id_alumna = v_alumna and id_clase = p_id_clase
                     and estado = 'expirado' and oferta_vence_en is not null) then
            return 'vencida';
        end if;
        return 'sin_oferta';
    end if;
    insert into public.reserva (id_alumna, id_clase) values (v_alumna, p_id_clase);
    perform public._sincronizar_inscriptas(p_id_clase);
    update public.listadeespera set estado = 'confirmado'
    where id_alumna = v_alumna and id_clase = p_id_clase and estado = 'ofrecido';
    return 'ok';
end;
$$;

create or replace function public.quitar_bloqueo(p_id bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_hoy date := (now() at time zone 'America/Montevideo')::date;
    v_b public.bloqueo;
    v_clases bigint[];
    v_recuperadas integer;
    v_sin_cupo integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_b from public.bloqueo where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if v_b.fecha_hasta < v_hoy then
        return jsonb_build_object('resultado', 'finalizado');
    end if;
    delete from public.bloqueo where id = p_id;

    perform 1 from public.clase c
    where c.estado = 'cancelada'
      and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
      and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
      and public._inicio_clase(c.fecha, c.hora_inicio) > now()
      and not public._clase_bloqueada(c.fecha, c.id_horario)
    order by c.id
    for update;

    with r as (
        update public.clase c set estado = 'programada'
        where c.estado = 'cancelada'
          and c.fecha between v_b.fecha_desde and v_b.fecha_hasta
          and (v_b.id_horario is null or c.id_horario = v_b.id_horario)
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
          and not public._clase_bloqueada(c.fecha, c.id_horario)
        returning c.id
    )
    select coalesce(array_agg(id), '{}') into v_clases from r;

    with candidatas as (
        select r.id, r.id_clase,
               row_number() over (partition by r.id_clase order by r.fecha_reserva, r.id) as orden,
               row_number() over (partition by r.id_alumna, r.id_clase order by r.fecha_reserva desc, r.id desc) as unica,
               c.cupo_max - (select count(*) from public.reserva x
                             where x.id_clase = r.id_clase and x.estado = 'confirmada') as libres
        from public.reserva r
        join public.clase c on c.id = r.id_clase
        where r.id_bloqueo = p_id and r.estado = 'cancelada' and r.id_clase = any(v_clases)
          and not exists (select 1 from public.reserva y
                          where y.id_alumna = r.id_alumna and y.id_clase = r.id_clase
                            and y.estado = 'confirmada')
    ),
    elegidas as (
        select id, id_clase, row_number() over (partition by id_clase order by orden) as n, libres
        from candidatas where unica = 1
    ),
    ok as (
        update public.reserva r
        set estado = 'confirmada', fecha_cancelacion = null, id_bloqueo = null
        from elegidas e
        where e.id = r.id and e.n <= e.libres
        returning 1
    )
    select count(*) into v_recuperadas from ok;

    select count(*) into v_sin_cupo from public.reserva
    where id_bloqueo = p_id and estado = 'cancelada' and id_clase = any(v_clases);

    update public.reserva r
    set id_bloqueo = (
        select b.id from public.bloqueo b join public.clase c on c.id = r.id_clase
        where c.fecha between b.fecha_desde and b.fecha_hasta
          and (b.id_horario is null or b.id_horario = c.id_horario)
        order by b.id limit 1)
    where r.id_bloqueo = p_id and r.estado = 'cancelada'
      and exists (select 1 from public.clase c
                  where c.id = r.id_clase and public._clase_bloqueada(c.fecha, c.id_horario));

    update public.clase c
    set cantidad_inscriptas = (select count(*) from public.reserva x
                               where x.id_clase = c.id and x.estado = 'confirmada')
    where c.id = any(v_clases);

    return jsonb_build_object('resultado', 'ok', 'clases', coalesce(array_length(v_clases, 1), 0),
                              'reservas', v_recuperadas, 'sin_cupo', v_sin_cupo);
end;
$$;

-- ---------- gestión de tipos de clase (solo la profesora) ----------

-- Una clase futura "está tomada" si tiene reservas confirmadas u ofertas vigentes de la
-- lista de espera: a esas alumnas no se les cambia el precio ni la duración.
create or replace function public._clase_tomada(p_id_clase bigint)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
    select exists (select 1 from public.reserva where id_clase = p_id_clase and estado = 'confirmada')
        or exists (select 1 from public.listadeespera where id_clase = p_id_clase and estado = 'ofrecido'
                   and oferta_vence_en > now());
$$;

-- Devuelve {"resultado": ok | no_autorizada | datos_invalidos | nombre_repetido, "id": n}
create or replace function public.crear_tipo_clase(
    p_nombre text, p_descripcion text, p_duracion integer, p_precio numeric, p_cupo integer, p_imagen_url text
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_id bigint;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(length(trim(p_nombre)), 0) not between 1 and 60 or p_duracion is null or p_duracion <= 0
       or p_precio is null or p_precio < 0 or p_cupo is null or p_cupo <= 0 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    begin
        insert into public.tipo_clase (nombre, descripcion, duracion, precio_presencial, cupo_max, imagen_url)
        values (trim(p_nombre), coalesce(trim(p_descripcion), ''), p_duracion, p_precio, p_cupo, p_imagen_url)
        returning id into v_id;
    exception when unique_violation then
        return jsonb_build_object('resultado', 'nombre_repetido');
    end;
    return jsonb_build_object('resultado', 'ok', 'id', v_id);
end;
$$;

-- Vista previa (solo lectura) de editar duración / precio / cupo de un tipo.
create or replace function public.previsualizar_cambio_tipo(
    p_id bigint, p_duracion integer, p_precio numeric, p_cupo integer
)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
    v_t public.tipo_clase;
    v_resultado jsonb;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_t from public.tipo_clase where id = p_id;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    with futuras as (
        select c.id, c.fecha, c.hora_inicio, c.precio, c.duracion, c.cupo_max,
               (select count(*) from public.reserva r where r.id_clase = c.id and r.estado = 'confirmada') as confirmadas,
               (select count(*) from public.listadeespera e where e.id_clase = c.id and e.estado = 'ofrecido'
                  and e.oferta_vence_en > now()) as ofertas,
               (select count(*) from public.listadeespera e where e.id_clase = c.id and e.estado = 'en_espera') as en_espera,
               public._clase_tomada(c.id) as tomada
        from public.clase c
        where c.id_tipo_clase = p_id and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
    )
    select jsonb_build_object(
        'resultado', 'ok',
        'horarios', (select count(*) from public.horario where id_tipo_clase = p_id),
        'futuras', (select count(*) from futuras),
        'actualizan_todo', (select count(*) from futuras where not tomada),
        'conservan_precio', (select count(*) from futuras where tomada
                             and (precio <> p_precio or duracion <> p_duracion)),
        'cambia_cupo', p_cupo <> v_t.cupo_max,
        'excedidas', coalesce((select jsonb_agg(jsonb_build_object('fecha', fecha, 'hora', hora_inicio,
                                   'confirmadas', confirmadas, 'ofertas', ofertas) order by fecha, hora_inicio)
                               from futuras where confirmadas + ofertas > p_cupo), '[]'),
        'con_lista_espera', (select count(*) from futuras where en_espera > 0 and p_cupo > cupo_max)
    ) into v_resultado;
    return v_resultado;
end;
$$;

-- Aplica la edición de un tipo. Clases futuras SIN reservas ni ofertas: toman precio,
-- duración y cupo nuevos. Clases futuras CON reservas u ofertas: conservan precio y
-- duración; el cupo se actualiza (nunca se cancelan reservas; si quedan por encima del
-- cupo, solo dejan de aceptarse nuevas). Si sube el cupo, la lista de espera ofrece los
-- lugares en orden. Devuelve {"resultado": ok | no_autorizada | no_existe | datos_invalidos |
-- nombre_repetido, "actualizan_todo": n, "conservan_precio": n, "horarios": n}
create or replace function public.aplicar_cambio_tipo(
    p_id bigint, p_nombre text, p_descripcion text, p_duracion integer, p_precio numeric, p_cupo integer
)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_ids bigint[];
    v_tomadas bigint[];
    v_id bigint;
    v_horarios integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    if coalesce(length(trim(p_nombre)), 0) not between 1 and 60 or p_duracion is null or p_duracion <= 0
       or p_precio is null or p_precio < 0 or p_cupo is null or p_cupo <= 0 then
        return jsonb_build_object('resultado', 'datos_invalidos');
    end if;
    perform 1 from public.tipo_clase where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    begin
        update public.tipo_clase
        set nombre = trim(p_nombre), descripcion = coalesce(trim(p_descripcion), ''),
            duracion = p_duracion, precio_presencial = p_precio, cupo_max = p_cupo
        where id = p_id;
    exception when unique_violation then
        return jsonb_build_object('resultado', 'nombre_repetido');
    end;

    -- Las columnas espejo de horario se recalculan con el trigger.
    with h as (update public.horario set id_tipo_clase = id_tipo_clase where id_tipo_clase = p_id returning 1)
    select count(*) into v_horarios from h;

    -- Clases futuras del tipo, bloqueadas en orden (como reservar).
    select coalesce(array_agg(id order by id), '{}') into v_ids from (
        select c.id from public.clase c
        where c.id_tipo_clase = p_id and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        order by c.id
        for update
    ) x;
    select coalesce(array_agg(id), '{}') into v_tomadas
    from unnest(v_ids) as id where public._clase_tomada(id);

    update public.clase set precio = p_precio, duracion = p_duracion, cupo_max = p_cupo
    where id = any(v_ids) and not (id = any(v_tomadas));
    update public.clase set cupo_max = p_cupo where id = any(v_tomadas);

    foreach v_id in array v_ids loop
        perform public._procesar_lista_espera(v_id);
    end loop;

    return jsonb_build_object('resultado', 'ok', 'horarios', v_horarios,
        'actualizan_todo', coalesce(array_length(v_ids, 1), 0) - coalesce(array_length(v_tomadas, 1), 0),
        'conservan_precio', coalesce(array_length(v_tomadas, 1), 0));
end;
$$;

-- Devuelve {"resultado": ok | no_autorizada | no_existe | tiene_horarios_activos, "horarios": n}
create or replace function public.cambiar_estado_tipo_clase(p_id bigint, p_activo boolean)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_activos integer;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    perform 1 from public.tipo_clase where id = p_id for update;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not p_activo then
        select count(*) into v_activos from public.horario where id_tipo_clase = p_id and activo;
        if v_activos > 0 then
            return jsonb_build_object('resultado', 'tiene_horarios_activos', 'horarios', v_activos);
        end if;
    end if;
    update public.tipo_clase set activo = p_activo where id = p_id;
    return jsonb_build_object('resultado', 'ok');
end;
$$;

-- Imagen representativa (la URL pública del archivo en Storage; NULL = sin imagen).
create or replace function public.cambiar_imagen_tipo_clase(p_id bigint, p_imagen_url text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
begin
    if not public.es_profesora() then
        return 'no_autorizada';
    end if;
    update public.tipo_clase set imagen_url = p_imagen_url where id = p_id;
    return case when found then 'ok' else 'no_existe' end;
end;
$$;

-- Vista previa (solo lectura) de cambiar el tipo de un horario.
create or replace function public.previsualizar_cambio_tipo_horario(p_id_horario bigint, p_id_tipo bigint)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    return (
        with futuras as (
            select c.id, public._clase_tomada(c.id) as tomada
            from public.clase c
            where c.id_horario = p_id_horario and c.estado = 'programada'
              and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        )
        select jsonb_build_object('resultado', 'ok',
            'futuras', (select count(*) from futuras),
            'cambian', (select count(*) from futuras where not tomada),
            'conservan', (select count(*) from futuras where tomada))
    );
end;
$$;

-- Cambia el tipo de un horario. Clases futuras sin reservas ni ofertas pasan al tipo nuevo
-- (con su precio, duración y cupo); las que ya tienen reservas u ofertas quedan como
-- estaban (mismo tipo, precio, duración y cupo): las alumnas que reservaron no ven cambios.
-- Devuelve {"resultado": ok | no_autorizada | no_existe | tipo_inactivo, "cambian": n, "conservan": n}
create or replace function public.cambiar_tipo_horario(p_id_horario bigint, p_id_tipo bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_t public.tipo_clase;
    v_ids bigint[];
    v_tomadas bigint[];
    v_id bigint;
begin
    if not public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select * into v_t from public.tipo_clase where id = p_id_tipo;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if not v_t.activo then
        return jsonb_build_object('resultado', 'tipo_inactivo');
    end if;
    update public.horario set id_tipo_clase = p_id_tipo where id = p_id_horario;
    if not found then
        return jsonb_build_object('resultado', 'no_existe');
    end if;

    select coalesce(array_agg(id order by id), '{}') into v_ids from (
        select c.id from public.clase c
        where c.id_horario = p_id_horario and c.estado = 'programada'
          and public._inicio_clase(c.fecha, c.hora_inicio) > now()
        order by c.id
        for update
    ) x;
    select coalesce(array_agg(id), '{}') into v_tomadas
    from unnest(v_ids) as id where public._clase_tomada(id);

    update public.clase
    set id_tipo_clase = v_t.id, precio = v_t.precio_presencial, duracion = v_t.duracion, cupo_max = v_t.cupo_max
    where id = any(v_ids) and not (id = any(v_tomadas));

    foreach v_id in array v_ids loop
        perform public._procesar_lista_espera(v_id);
    end loop;

    return jsonb_build_object('resultado', 'ok',
        'cambian', coalesce(array_length(v_ids, 1), 0) - coalesce(array_length(v_tomadas, 1), 0),
        'conservan', coalesce(array_length(v_tomadas, 1), 0));
end;
$$;

revoke execute on function public._horario_desde_tipo() from public, anon, authenticated;
revoke execute on function public._clase_desde_horario() from public, anon, authenticated;
revoke execute on function public._clase_tomada(bigint) from public, anon, authenticated;
revoke execute on function public.crear_tipo_clase(text, text, integer, numeric, integer, text) from public, anon;
revoke execute on function public.previsualizar_cambio_tipo(bigint, integer, numeric, integer) from public, anon;
revoke execute on function public.aplicar_cambio_tipo(bigint, text, text, integer, numeric, integer) from public, anon;
revoke execute on function public.cambiar_estado_tipo_clase(bigint, boolean) from public, anon;
revoke execute on function public.cambiar_imagen_tipo_clase(bigint, text) from public, anon;
revoke execute on function public.previsualizar_cambio_tipo_horario(bigint, bigint) from public, anon;
revoke execute on function public.cambiar_tipo_horario(bigint, bigint) from public, anon;
grant execute on function public.crear_tipo_clase(text, text, integer, numeric, integer, text) to authenticated;
grant execute on function public.previsualizar_cambio_tipo(bigint, integer, numeric, integer) to authenticated;
grant execute on function public.aplicar_cambio_tipo(bigint, text, text, integer, numeric, integer) to authenticated;
grant execute on function public.cambiar_estado_tipo_clase(bigint, boolean) to authenticated;
grant execute on function public.cambiar_imagen_tipo_clase(bigint, text) to authenticated;
grant execute on function public.previsualizar_cambio_tipo_horario(bigint, bigint) to authenticated;
grant execute on function public.cambiar_tipo_horario(bigint, bigint) to authenticated;

-- ---------- Storage: imágenes de los tipos de clase ----------
-- Lectura pública (las imágenes se muestran en la plataforma y luego en On Demand);
-- solo la profesora puede subir, reemplazar o borrar. JPG / PNG / WebP, hasta 2 MB.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('tipos-clase', 'tipos-clase', true, 2097152, array['image/jpeg', 'image/png', 'image/webp'])
on conflict (id) do nothing;

create policy "tipos-clase: profesora sube" on storage.objects
    for insert to authenticated with check (bucket_id = 'tipos-clase' and public.es_profesora());
create policy "tipos-clase: profesora reemplaza" on storage.objects
    for update to authenticated using (bucket_id = 'tipos-clase' and public.es_profesora())
    with check (bucket_id = 'tipos-clase' and public.es_profesora());
create policy "tipos-clase: profesora borra" on storage.objects
    for delete to authenticated using (bucket_id = 'tipos-clase' and public.es_profesora());
