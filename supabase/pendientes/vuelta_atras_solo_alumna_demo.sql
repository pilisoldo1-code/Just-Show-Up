-- VUELTA ATRÁS de 20261029180000_mercado_pago_solo_alumna_demo.sql (NO es una migración).
-- Deja crear_intento_mp exactamente como estaba en producción antes de la restricción (todas las
-- alumnas @demo.invalid / @prueba.invalid podían iniciar un pago). Copia byte a byte de
-- pg_get_functiondef tomada el 29/10 (huella md5 e6afed1eddf2e5edb1bf5d031aaa49fd).
-- No toca datos. Correr en el SQL Editor de Supabase solo si Julieta lo autoriza.

begin;

CREATE OR REPLACE FUNCTION public.crear_intento_mp(p_id_resumen bigint)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO ''
AS $function$
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
    if v_mail is null or not (v_mail like '%@demo.invalid' or v_mail like '%@prueba.invalid') then
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
$function$
;

revoke execute on function public.crear_intento_mp(bigint) from public, anon, authenticated;
grant execute on function public.crear_intento_mp(bigint) to authenticated;

delete from supabase_migrations.schema_migrations where name = 'mercado_pago_solo_alumna_demo';

commit;
