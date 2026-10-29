-- Mercado Pago (solo prueba): únicamente alumna@prueba.invalid puede iniciar un pago.
-- Antes podía cualquier alumna @demo.invalid o @prueba.invalid. Pedido de Julieta (29/10): la
-- demostración de la defensa usa solo esa alumna ficticia; las alumnas demo no deben poder pagar
-- sus facturas normales. Solo cambia esta condición de crear_intento_mp (el resto es idéntico a
-- 20261029120000_mercado_pago_prueba.sql). No toca datos, tablas, facturación ni pagos.
-- Vuelta atrás: supabase/pendientes/vuelta_atras_solo_alumna_demo.sql

create or replace function public.crear_intento_mp(p_id_resumen bigint)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
    v_uid uuid := auth.uid();
    v_mail text;
    f public.resumen_mensual;
    v_ref text;
    v_id bigint;
begin
    if not coalesce((select habilitado from public.mp_configuracion where id = 1), false) then
        return jsonb_build_object('resultado', 'deshabilitado');
    end if;
    if v_uid is null or public.es_profesora() then
        return jsonb_build_object('resultado', 'no_autorizada');
    end if;
    select lower(mail) into v_mail from public.profiles where id = v_uid;
    if v_mail is distinct from 'alumna@prueba.invalid' then
        return jsonb_build_object('resultado', 'alumna_no_habilitada');
    end if;
    select * into f from public.resumen_mensual where id = p_id_resumen for update;
    if not found or f.id_alumna <> v_uid then
        return jsonb_build_object('resultado', 'no_existe');
    end if;
    if f.estado_factura <> 'confirmada' or public._ahora() < public._inicio_publicacion(f.mes, f.anio) then
        return jsonb_build_object('resultado', 'no_disponible');
    end if;
    if f.estado_pago = 'sin_cargo' or f.total <= 0 then
        return jsonb_build_object('resultado', 'sin_cargo');
    end if;
    if f.estado_pago <> 'pendiente'
       or exists (select 1 from public.pago where id_resumen_mensual = f.id and estado = 'vigente') then
        return jsonb_build_object('resultado', 'ya_pagada');
    end if;
    v_ref := 'JSU-' || f.id || '-' || replace(gen_random_uuid()::text, '-', '');
    insert into public.mp_intento (id_resumen_mensual, id_alumna, referencia, monto)
    values (f.id, v_uid, v_ref, f.total)
    returning id into v_id;
    return jsonb_build_object('resultado', 'ok', 'id_intento', v_id, 'referencia', v_ref,
                              'monto', f.total, 'numero', f.numero, 'mes', f.mes, 'anio', f.anio);
end;
$$;

revoke execute on function public.crear_intento_mp(bigint) from public, anon, authenticated;
grant execute on function public.crear_intento_mp(bigint) to authenticated;
