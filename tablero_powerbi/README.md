# Radar Mayorista — tablero de Power BI (Mayoristas v3)

Diseño del tablero que consume `marts_mayoristas.mayoristas_v3_*`. El diseño visual
completo, con los mockups de las seis páginas y la justificación de cada visual, está
en el artefacto **Radar Mayorista**:
https://claude.ai/code/artifact/5b2ad429-e6dc-49ad-97a7-375b6d7562ed

Acá viven las dos piezas que se cargan en el archivo `.pbix` y las cosas que hay que
resolver del lado de SQL antes de construirlo.

## Qué hay en esta carpeta

`tema_radar_mayorista.json` es el tema de Power BI con la paleta de marca, la rampa de
`nivel_mayorista` y los estilos por defecto de tablas, ejes y tarjetas. Se importa desde
Ver → Temas → Buscar temas. Usa Segoe UI en sus tres pesos porque es la única familia que
Power BI garantiza en cualquier máquina y en el servicio; si algún día se instala Anton
en los equipos, cambiar los `fontFace` de `callout` y `title` a `Anton` acerca bastante el
informe a la tipografía del cartel de la tienda.

`medidas_radar_mayorista.dax` son todas las medidas, agrupadas por página y comentadas.
Se pegan en una tabla vacía llamada `_Medidas`. Las secciones 9 y 10 dependen de cosas
opcionales (el mart de evolución y la comparación contra v2) — si no se hacen, se borran.

## El modelo

Es un modelo compuesto, no todo importado. `mayoristas_v3_tickets_unidad` tiene alrededor
de 62 millones de filas (Unid ≈ 42,8 M, KILOS ≈ 19,5 M, LITROS ≈ 190 K, Pack ≈ 58 K), así
que va en DirectQuery y solo se consulta con un cliente ya filtrado. Todo lo demás entra
importado y no llega al millón de filas.

| Tabla en el modelo | Origen | Modo |
|---|---|---|
| `Clientes` | `mayoristas_v3_resumen` + `intermediate.int_clientes_limpio` | Import |
| `Métricas por unidad` | `mayoristas_v3_metricas_unidad` | Import |
| `Unidades` | `mayoristas_v3_umbrales` | Import |
| `Facturas` | `mayoristas_v3_tickets_unidad` | **DirectQuery** |
| `Líneas de venta` | `intermediate.int_ventas_12m` | **DirectQuery** |
| `Sucursales` | seed `dim_sucursal_mapeo` | Import |
| `Niveles` | tabla escrita a mano en Power Query | Import |
| `Calendario` | generado en Power Query | Import |
| `Actualización` | generado en Power Query | Import |

`Unidades` es a la vez la tabla de referencia de metodología y la dimensión de
`unidad_medida`: tiene una fila por unidad con clave única, así que filtra tanto a
`Métricas por unidad` como a `Facturas`. No hace falta una dimensión aparte.

`Clientes` sale de un merge en Power Query entre `mayoristas_v3_resumen` y
`int_clientes_limpio` por `cliente_id_limpio`. Es un join, no un cálculo: deja el nombre
real, el RUC y `tipo_persona` en la misma tabla que el nivel, y evita una relación 1:1 de
400 mil filas que después obliga a filtrado bidireccional para que la tabla de ranking no
muestre filas en blanco.

`Niveles` existe por tres razones que no se resuelven de otra forma: ordena Alto → Medio →
Potencial → Minorista (alfabéticamente Power BI los pone Alto, Medio, Minorista,
Potencial), guarda el color de cada nivel para el formato condicional por campo, y guarda
la frase que explica qué significa cada uno.

| nivel_mayorista | orden | puntaje | color_hex | que_significa |
|---|---|---|---|---|
| Alto | 1 | 3 | `#8C0D08` | Cumple los tres criterios |
| Medio | 2 | 2 | `#C8341F` | Cumple dos de los tres |
| Potencial | 3 | 1 | `#E4715C` | Cumple uno solo |
| Minorista | 4 | 0 | `#EFA091` | No cumple ninguno |

Se relaciona 1:* contra `Clientes[nivel_mayorista]` y se marca *Ordenar por columna* →
`orden`. En los visuales se usa siempre `Niveles[nivel_mayorista]`, nunca la columna de
`Clientes`.

Las relaciones entre las tablas importadas y las de DirectQuery son limitadas: Power BI
materializa los valores del filtro y los manda en un `IN` al servidor. Con un cliente
seleccionado eso es un `IN` de un elemento, que es exactamente el patrón que se quiere.
`Facturas` y `Líneas de venta` se relacionan entre sí por `ticket_id` con cardinalidad
muchos a muchos y dirección `Facturas → Líneas de venta`, porque `ticket_id` se repite en
ambas (una factura con kilos y unidades ocupa dos filas en `Facturas`).

## Lo que falta del lado de SQL

**Índices.** El drill-down por cliente es una consulta DirectQuery contra 62 millones de
filas. Sin un índice sobre `cliente_id_limpio` cada clic es un scan completo y la página
de detalle no sirve. En `mayoristas_v3_tickets_unidad.sql`:

```jinja
{{ config(
    materialized = 'table',
    post_hook = "create nonclustered index ix_{{ this.identifier }}__cliente
                 on {{ this }} (cliente_id_limpio) include (ticket_id, unidad_medida, ticket_grande)"
) }}
```

Para la página de detalle de factura hace falta lo mismo sobre `ticket_id` en la tabla que
está debajo de `int_ventas_elegibles` — `int_ventas_12m` es una vista, así que el índice va
en la tabla base, no en la vista. Conviene confirmar si ya existe antes de agregarlo.

**Tendencia.** Ninguna tabla de v3 tiene historia: son una foto sobre la ventana móvil de
12 meses. Se puede graficar cómo compraron mes a mes los clientes de cada nivel, que es
distinto de cuántos clientes había en cada nivel cada mes. Para lo primero alcanza un mart
chico, 48 filas, `models/marts/mayoristas/mayoristas_v3_evolucion_nivel.sql`:

```sql
{{ config(materialized='table') }}

select
    r.nivel_mayorista,
    datefromparts(year(v.fecha_venta), month(v.fecha_venta), 1) as mes,
    count(distinct v.ticket_id)         as tickets,
    count(distinct v.cliente_id_limpio) as clientes_activos,
    sum(v.venta_gs)                     as venta_gs
from {{ ref('int_ventas_12m') }} v
inner join {{ ref('mayoristas_v3_resumen') }} r
    on v.cliente_id_limpio = r.cliente_id_limpio
group by r.nivel_mayorista, datefromparts(year(v.fecha_venta), month(v.fecha_venta), 1)
```

Lee de `int_ventas_12m` y no de `mayoristas_v3_tickets_unidad` a propósito: la venta tiene
que cuadrar con `ventas_totales` de `mayoristas_v3_resumen`, y la tabla de tickets deja
afuera Blis, Metros y Ramo. Los dos `count(distinct)` sobre 62 millones de filas no son
gratis; si pesa en la corrida diaria, se puede sacar `clientes_activos` sin perder el
gráfico principal.

Lo segundo — cuántos clientes había en cada nivel en cada mes — necesita versionar la
clasificación, igual que `dim_clientes_mayoristas_snapshot` hace con v2. No tiene sentido
hacerlo mientras v3 siga moviéndose: se snapshotearía una metodología que todavía puede
cambiar. Queda para después del cutover.

## Power Query

Además del merge de `Clientes` y de la tabla `Niveles` escrita a mano, hacen falta dos
consultas mínimas.

El calendario sale de una consulta escalar contra la tabla de facturas y se expande en M,
para no traer 62 millones de filas solo para saber el rango:

```sql
select min(fecha_venta) as fecha_min, max(fecha_venta) as fecha_max
from marts_mayoristas.mayoristas_v3_tickets_unidad
```

La hora de actualización no puede salir de `NOW()` en DAX: en el servicio devuelve UTC.
Se genera una tabla `Actualización` de una fila con `DateTime.LocalNow()` en Power Query,
que sí se congela en el momento del refresco.

`celular` y `mail` vienen en `int_clientes_limpio` y son datos personales. Sirven en la
ficha del cliente, donde el vendedor tiene que llamar a alguien; no tienen nada que hacer
en la tabla de ranking ni en un export. Conviene dejarlos solo en la ficha.

El modelo compuesto con tablas en DirectQuery necesita un gateway de datos local para
poder publicarse en el servicio.

## Todavía abierto

El pipeline de v3 no corrió contra la base real después del rediseño de la consulta, así
que los umbrales y la distribución que muestre el tablero la primera vez son lo primero a
verificar. Los números de referencia del notebook de Python — Minorista 238.235, Medio
74.010, Potencial 69.016, Alto 17.172, y el cliente 2504462 en Alto con puntaje 3 — sirven
para el chequeo visual, no para cablearlos en ningún lado.

Mientras `dim_clientes_mayoristas` siga siendo la fuente de producción, el aviso de la
medida `Estado de la metodología` va en las seis páginas. Cuando se decida el cutover,
cambiar esa medida apaga el aviso en todo el informe de una sola vez.
