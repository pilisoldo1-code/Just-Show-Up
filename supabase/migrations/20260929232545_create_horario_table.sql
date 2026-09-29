create table if not exists public.horario (
    id bigint generated always as identity primary key,
    dia_semana text not null check (dia_semana in ('lunes','martes','miercoles','jueves','viernes','sabado','domingo')),
    hora_inicio time not null,
    duracion integer not null check (duracion > 0),
    cupo_max integer not null check (cupo_max > 0),
    precio numeric(10,2) not null check (precio >= 0),
    activo boolean not null default true
);

alter table public.horario enable row level security;

create policy "Allow anon read" on public.horario
    for select
    to anon
    using (true);

create policy "Allow anon insert" on public.horario
    for insert
    to anon
    with check (true);

create policy "Allow anon update" on public.horario
    for update
    to anon
    using (true)
    with check (true);

create policy "Allow anon delete" on public.horario
    for delete
    to anon
    using (true);
