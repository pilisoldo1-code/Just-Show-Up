-- Módulo 5: asistencia. Una fila por reserva con un único estado: una alumna no
-- puede quedar presente y ausente a la vez. Sin fila = todavía sin marcar.
create table public.asistencia (
    id bigint generated always as identity primary key,
    id_reserva bigint not null unique references public.reserva(id),
    estado text not null check (estado in ('presente', 'ausente'))
);

alter table public.asistencia enable row level security;

-- Lectura: la profesora todas; cada alumna solo la de sus reservas.
create policy "asistencia: leer propias o profesora" on public.asistencia
    for select to authenticated
    using (
        public.es_profesora()
        or exists (select 1 from public.reserva r
                   where r.id = id_reserva and r.id_alumna = (select auth.uid()))
    );

-- Nadie escribe directo: se marca solo con marcar_asistencia().
revoke all on public.asistencia from anon;
revoke insert, update, delete, truncate on public.asistencia from authenticated;

-- Solo la profesora, solo reservas confirmadas y solo mientras dura la clase
-- (de la hora de inicio a inicio + duración, hora de Montevideo).
-- Devuelve: ok | no_autorizada | sin_reserva | fuera_de_horario
create or replace function public.marcar_asistencia(p_id_reserva bigint, p_estado text)
returns text
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_inicio timestamptz;
    v_duracion integer;
begin
    if not public.es_profesora() then
        return 'no_autorizada';
    end if;
    if p_estado not in ('presente', 'ausente') then
        raise exception 'estado inválido' using errcode = '22023';
    end if;
    select public._inicio_clase(c.fecha, c.hora_inicio), c.duracion into v_inicio, v_duracion
    from public.reserva r join public.clase c on c.id = r.id_clase
    where r.id = p_id_reserva and r.estado = 'confirmada';
    if v_inicio is null then
        return 'sin_reserva';
    end if;
    if now() < v_inicio or now() > v_inicio + make_interval(mins => v_duracion) then
        return 'fuera_de_horario';
    end if;
    insert into public.asistencia (id_reserva, estado) values (p_id_reserva, p_estado)
    on conflict (id_reserva) do update set estado = excluded.estado;
    return 'ok';
end;
$$;

revoke execute on function public.marcar_asistencia(bigint, text) from public, anon;
grant execute on function public.marcar_asistencia(bigint, text) to authenticated;
