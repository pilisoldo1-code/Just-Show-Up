create table if not exists public.items (
    id bigint generated always as identity primary key,
    title text not null,
    created_at timestamptz not null default now()
);

alter table public.items enable row level security;

create policy "Allow anon read" on public.items
    for select
    to anon
    using (true);

create policy "Allow anon insert" on public.items
    for insert
    to anon
    with check (true);

create policy "Allow anon delete" on public.items
    for delete
    to anon
    using (true);
