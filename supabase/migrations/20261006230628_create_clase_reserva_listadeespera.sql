create table public.clase (
    id bigint generated always as identity primary key,
    id_horario bigint not null references public.horario(id),
    fecha date not null,
    hora_inicio time not null,
    cantidad_inscriptas integer not null default 0 check (cantidad_inscriptas >= 0),
    estado text not null default 'programada' check (estado in ('programada','cancelada','finalizada')),
    duracion integer not null check (duracion > 0),
    precio numeric(10,2) not null check (precio >= 0),
    unique (id_horario, fecha)
);

create table public.reserva (
    id bigint generated always as identity primary key,
    id_alumna uuid not null,
    id_clase bigint not null references public.clase(id),
    fecha_reserva timestamptz not null default now(),
    estado text not null default 'confirmada' check (estado in ('confirmada','cancelada')),
    fecha_cancelacion timestamptz
);

create unique index reserva_activa_unica on public.reserva (id_alumna, id_clase) where estado = 'confirmada';
create index reserva_id_clase_idx on public.reserva (id_clase);

create table public.listadeespera (
    id bigint generated always as identity primary key,
    id_alumna uuid not null,
    id_clase bigint not null references public.clase(id),
    posicion integer not null check (posicion > 0),
    fecha_ingreso timestamptz not null default now(),
    estado text not null default 'en_espera' check (estado in ('en_espera','ofrecido','confirmado','expirado')),
    oferta_vence_en timestamptz
);

create unique index listadeespera_activa_unica on public.listadeespera (id_alumna, id_clase) where estado in ('en_espera','ofrecido');
create index listadeespera_id_clase_idx on public.listadeespera (id_clase);

-- PROVISORIO: politicas abiertas a anon (igual que horario) hasta que exista Auth.
alter table public.clase enable row level security;
alter table public.reserva enable row level security;
alter table public.listadeespera enable row level security;

create policy "anon select" on public.clase for select to anon using (true);
create policy "anon insert" on public.clase for insert to anon with check (true);
create policy "anon update" on public.clase for update to anon using (true) with check (true);

create policy "anon select" on public.reserva for select to anon using (true);
create policy "anon insert" on public.reserva for insert to anon with check (true);
create policy "anon update" on public.reserva for update to anon using (true) with check (true);

create policy "anon select" on public.listadeespera for select to anon using (true);
create policy "anon insert" on public.listadeespera for insert to anon with check (true);
create policy "anon update" on public.listadeespera for update to anon using (true) with check (true);
