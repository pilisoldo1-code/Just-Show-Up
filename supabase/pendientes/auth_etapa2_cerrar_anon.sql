-- Etapa 2 de autenticación, fase 2: quita el acceso sin sesión (anon) a las tablas.
-- APLICAR SOLO cuando TODOS los deploys (amber y theta) tengan el código que usa
-- la sesión de la usuaria; con el código viejo, los sitios dejan de funcionar.
-- Al aplicarla, renombrar el archivo con su timestamp real.

drop policy "Allow anon read" on public.horario;
drop policy "Allow anon insert" on public.horario;
drop policy "Allow anon update" on public.horario;
drop policy "Allow anon delete" on public.horario;

drop policy "anon select" on public.clase;
drop policy "anon insert" on public.clase;
drop policy "anon update" on public.clase;

drop policy "anon select" on public.reserva;
drop policy "anon insert" on public.reserva;
drop policy "anon update" on public.reserva;

drop policy "anon select" on public.listadeespera;
drop policy "anon insert" on public.listadeespera;
drop policy "anon update" on public.listadeespera;

revoke all on public.horario, public.clase, public.reserva, public.listadeespera from anon;
-- Las alumnas escriben solo a través de las funciones (reservar, cancelar_reserva,
-- anotar_en_espera); la profesora escribe horarios y clases según las políticas.
revoke insert, update, delete, truncate on public.reserva, public.listadeespera from authenticated;
