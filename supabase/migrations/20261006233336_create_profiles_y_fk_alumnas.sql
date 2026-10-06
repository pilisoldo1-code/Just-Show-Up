create table public.profiles (
    id uuid primary key references auth.users(id) on delete cascade,
    nombre text not null default '',
    apellido text not null default '',
    mail text not null,
    telefono text,
    fecha_nacimiento date,
    fecha_vencimiento_carne_salud date,
    rol text not null default 'alumna' check (rol in ('alumna','profesora'))
);

-- Datos personales: RLS activado y SIN policies => anon/authenticated no pueden leer ni escribir.
-- Solo el servidor accede, con la service key (que ignora RLS).
alter table public.profiles enable row level security;

-- El rol NUNCA se toma de lo que mande el cliente: siempre nace como alumna.
create function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
    insert into public.profiles (id, mail, nombre, apellido)
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data->>'nombre', ''),
        coalesce(new.raw_user_meta_data->>'apellido', '')
    );
    return new;
end;
$$;

revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
    after insert on auth.users
    for each row execute function public.handle_new_user();

alter table public.reserva
    add constraint reserva_id_alumna_fkey foreign key (id_alumna) references public.profiles(id);
alter table public.listadeespera
    add constraint listadeespera_id_alumna_fkey foreign key (id_alumna) references public.profiles(id);

create index reserva_id_alumna_idx on public.reserva (id_alumna);
create index listadeespera_id_alumna_idx on public.listadeespera (id_alumna);
