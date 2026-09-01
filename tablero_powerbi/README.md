# Radar Mayorista — tablero de Power BI (Mayoristas v3)

Diseño del tablero que consume `marts_mayoristas.mayoristas_v3_*`. La guía completa —las seis
páginas maquetadas a tamaño real y, debajo de cada una, qué visual insertar, qué campo va en qué
pozo y qué tocar en el panel de formato— está en el artefacto **Radar Mayorista**:
https://claude.ai/code/artifact/5b2ad429-e6dc-49ad-97a7-375b6d7562ed

Acá viven las piezas que se cargan en el archivo `.pbix` y las cosas que hay que resolver del lado
de SQL antes de construirlo.

## Qué hay en esta carpeta

`tema_radar_mayorista.json` es el tema de Power BI con la paleta de marca, la rampa de
`nivel_mayorista` y los estilos por defecto de tablas, ejes y tarjetas. Se importa desde
Ver → Temas → Buscar temas. Usa Segoe UI en sus tres pesos porque es la única familia que Power BI
garantiza en cualquier máquina y en el servicio; si algún día se instala Anton en los equipos,
cambiar los `fontFace` de `callout` y `title` acerca bastante el informe a la tipografía del cartel
de la tienda.

`validar_tema.js` valida el tema contra el esquema JSON oficial de Microsoft antes de importarlo.
Vale la pena correrlo siempre que se toque el tema: la raíz del esquema tiene
`additionalProperties: false`, así que una sola clave mal escrita rechaza el archivo entero, y el
mensaje de error de Power BI no dice cuál es.

```
npm install          # una sola vez, baja ajv
npm run validar
```

Sin argumentos prueba contra cuatro versiones del esquema (2.128, 2.137, 2.148 y 2.156), que cubren
unos dos años de Power BI Desktop. Los esquemas se cachean en `.schemas/`. Chequea tres cosas
distintas: propiedades que no existen, valores del tipo equivocado —`outlineStyle` es un entero, no
un string— y nombres de propiedad inventados dentro del bloque `visualStyles["*"]`, que no rompen la
importación pero Power BI ignora en silencio, que es peor.

`medidas_radar_mayorista.dax` son las 87 medidas, agrupadas por página y comentadas. Se pegan de a
una con Nueva medida, o —más rápido— se pega `medidas_radar_mayorista_bulk.tsv` entero en la carpeta
Measures de una tabla `_Medidas` desde Tabular Editor 2. Ese TSV se genera desde el `.dax`: si se
edita una medida, hay que regenerarlo para que no se desincronicen.

## El modelo

Todo importado, salvo la tabla de líneas de producto que necesita la página de detalle de factura.

El grano de cada tabla es la decisión que más cambia el modelo, y es fácil equivocarse porque los
nombres se parecen. Una fila de `int_ventas_12m` es una **línea de producto**; una de
`mayoristas_v3_tickets_unidad` es una **factura × unidad de medida**. El `GROUP BY` que las separa
colapsa dos órdenes de magnitud:

| Tabla | Qué es una fila | Filas |
|---|---|---|
| `int_ventas_12m` | una línea de producto de una factura | ~62 M |
| `int_mayoristas_v3_tickets_por_unidad` | una factura × unidad de medida | 8,0 M |
| `mayoristas_v3_tickets_unidad` | lo mismo, con los umbrales pegados | 8,0 M |

Ocho millones de filas y siete columnas entran importadas sin problema — en VertiPaq comprimen a
unos cientos de megas. Los 62 millones son solo de la tabla de líneas. Conviene verificar los
conteos contra la base antes de elegir modos de almacenamiento:

```sql
select count(*) from marts_mayoristas.mayoristas_v3_tickets_unidad;
select count(*) from intermediate.int_ventas_12m;
```

| Tabla en el modelo | Origen | Modo |
|---|---|---|
| `Clientes` | `mayoristas_v3_resumen` + `int_clientes_limpio` + `mayoristas_v3_frecuencia_compra` | Import (398 K) |
| `Métricas por unidad` | `mayoristas_v3_metricas_unidad` | Import |
| `Unidades` | `mayoristas_v3_umbrales` | Import |
| `Facturas` | `mayoristas_v3_tickets_unidad` | Import (8,0 M) |
| `Canasta` | `mayoristas_v3_canasta` | Import (28,1 M) |
| `Líneas de venta` (solo pág. 5) | mart nuevo a grano de línea | **DirectQuery** |
| `Productos` | `intermediate.int_productos_limpio` | Import (~40 K) |
| `Sucursales` | seed `dim_sucursal_mapeo` | Import (3) |
| `Niveles` | tabla escrita a mano en Power Query | Import |
| `Criterios` | tabla escrita a mano en Power Query | Import (3) |
| `Frecuencias` | tabla escrita a mano en Power Query | Import (6) |
| `Calendario` | generado en Power Query | Import |
| `Actualización` | generado en Power Query | Import |

### `mayoristas_v3_canasta` — qué, dónde y cuándo compra cada segmento

Grano **nivel × frecuencia × sucursal × producto × día**, fuente de la página 6. Conteos verificados contra la
base antes de fijar el grano:

| Grano | Filas | Tiempo de cómputo |
|---|---|---|
| nivel × sucursal × producto | 482.238 | instantáneo |
| + mes | 2.708.594 | instantáneo |
| + semana | 6.990.766 | instantáneo |
| nivel × sucursal × categoria_4_limpio × día | 3.095.524 | 4m 13s |
| nivel × sucursal × producto × día | 18.242.878 | instantáneo |
| **+ frecuencia_compra** — el elegido | **28.097.539** | instantáneo |

**El día y no la semana** porque la pregunta incluye *qué compran los viernes* y *qué compran a fin
de mes*, y la semana borra las dos. Del día suben el día de la semana, el día del mes, la quincena,
la semana y el mes, todos desde el calendario; al revés no sube ninguno.

**Producto y no `categoria_4_limpio`**, aunque sean seis veces más filas: agrupar por la clave del
join es instantáneo y agrupar por una cadena de texto ancha sobre 62 M de filas tardó 4m 13s — costo
que se pagaría en cada corrida diaria, y encima perdiendo el detalle de producto. Con `producto_id`
en el grano, las tres categorías salen gratis desde `Productos`.

**`frecuencia_compra` sí vive en el mart**, al revés que las categorías y los cortes de tiempo. No es
una inconsistencia: este mart está pre-agregado y no tiene `cliente_id_limpio`, así que **ningún
atributo de cliente puede filtrarlo por relación**. Sin la columna en el `GROUP BY`, un slicer de
frecuencia sobre la página 6 no filtra nada, y no hay forma de arreglarlo desde Power BI — no existe
clave por dónde relacionar. Cuesta 1,54× las filas (28.097.539 contra 18.242.878; no el 5× nominal,
porque la combinación es dispersa) y habilita cruzar canasta con "cada cuánto viene".

La regla general que sale de ahí: **un mart pre-agregado solo se puede cortar por lo que está en su
grano.** Las categorías y las fechas son la excepción porque el mart guarda sus claves
(`producto_id`, `fecha_venta`) y las dimensiones hacen el resto; un atributo de cliente no tiene
clave que guardar.

Ni las categorías ni los cortes de tiempo viven en el mart. Tampoco `unidades` (sumarlas entre
productos mezcla unidades de medida) ni conteos distintos (dejan de ser correctos al sumar filas).

`venta_gs` se suma casteada a `decimal(19,4)`. Es `float` en el contrato de `stg_ventas` y trae
decimales reales (los valores vienen en cuartos). El cast comprime —VertiPaq guarda decimal como
entero escalado y float como 8 bytes que no comprime bien, y es la columna más pesada de la tabla— y
sobre todo **hace la suma determinística**: sumar floats en paralelo da resultados distintos entre
corridas según cómo el motor combine los parciales.

Queda una consecuencia abierta: `mayoristas_v3_resumen` hace `sum` y `avg` sobre esos mismos floats,
así que `ventas_totales`, `venta_promedio_ticket` y `umbral_c3` pueden variar en los últimos dígitos
entre corridas. El efecto no mueve el nivel de nadie salvo un empate improbable, pero conviene saber
que la clasificación descansa sobre sumas no reproducibles.

### `mayoristas_v3_frecuencia_compra` — cada cuánto viene

Una fila por cliente, con `visitas`, `dias_entre_visitas` y `frecuencia_compra` en Semanal /
Quincenal / Mensual / Esporádico / Visita única / Sin datos. Se une a `Clientes` con un merge en
Power Query, igual que `int_clientes_limpio` — no como tabla aparte. Es grano cliente, el mismo que
`Clientes`, así que como columna suya queda disponible en **todas** las páginas sin una relación más.

Con eso `frecuencia_compra` deja de ser el tema de una página y pasa a ser una segunda dimensión de
segmentación cruzable con el nivel: un Alto que viene todas las semanas y uno que viene una vez al
mes son dos carteras distintas, y el ranking hoy las mezcla.

La tabla de presentación `Frecuencias` se relaciona con **dos** tablas —`Clientes` y `Canasta`— así
que el mismo slicer funciona en las cinco páginas visibles. En `Clientes` llega por relación; en
`Canasta`, porque la columna está en el grano.

Lee de `mayoristas_v3_tickets_unidad` (8,0 M ya materializadas) y no de `int_ventas_12m` (62 M). La
brecha medida entre las dos es de 2.477 tickets sobre 4.681.477 — un **0,05%** — y son facturas
compuestas enteramente por Blis, Metros o Ramo. Para clasificar cada cuánto viene un cliente, una
visita es una visita sin importar la unidad de medida de la canasta.

**Cómo se calcula, en criollo.** Si un cliente compró por primera vez el 1 de enero, por última el 31
de diciembre, y en el medio vino 25 veces, entre esas 25 visitas hubo **24 intervalos**:
365 ÷ 24 = 15 días entre visita y visita. Ese cliente es quincenal.

```
dias_entre_visitas = (última compra − primera compra) / (visitas − 1)
```

El "menos uno" es lo único que tiene truco, y es el problema de los postes del alambrado: con 25
postes hay 24 tramos de alambre.

**No es una aproximación.** Medir cada intervalo por separado y promediarlos da exactamente el mismo
número: la suma `(d2−d1) + (d3−d2) + … + (dn−dn₋₁)` telescopa y todo lo del medio se cancela,
quedando `dn − d1`. Es la misma fórmula escrita de forma barata, sin `LAG` y sin ordenar 8 M de filas
particionadas por cliente — justo la clase de operación que ya colgó esta instancia dos veces.

**Lo que la cuenta no ve:** un promedio suaviza las ráfagas. Un cliente que vino 20 veces en una
semana y una sola vez 11 meses después da ~18 días y sale "Quincenal", cuando fue una ráfaga más un
rezagado. Para la pregunta del negocio —¿este cliente vuelve seguido?— el promedio alcanza.
Distinguir ráfaga de hábito sería la mediana de los intervalos, y eso sí necesita `LAG`.

**Casos borde:** una sola visita, o varias facturas del mismo día (facturas partidas de una misma
visita), dan `Visita única` — no hay intervalo que medir. Un cliente sin ninguna factura en las
cuatro unidades de medida da `Sin datos`.

Los cortes (10 / 20 / 45 días) viven en `dbt_project.yml`. A diferencia de los umbrales de la
clasificación **no son percentiles**: semana, quincena y mes son conceptos del negocio, se confirman
con el equipo comercial.

La contracara de pre-agregar la canasta: la página 6 **no responde al cliente individual** — la
búsqueda por nombre y el drill-through a la ficha no llegan ahí, y eso no se resuelve metiendo una
columna más, porque haría falta `cliente_id_limpio` en el grano y ahí el mart deja de ser un mart.

`Unidades` es a la vez la tabla de referencia de metodología y la dimensión de `unidad_medida`:
tiene una fila por unidad con clave única, así que filtra tanto a `Métricas por unidad` como a
`Facturas`. No hace falta una dimensión aparte.

`Clientes` sale de un merge en Power Query entre `mayoristas_v3_resumen` y `int_clientes_limpio` por
`cliente_id_limpio`. Es un join, no un cálculo: deja el nombre real, el RUC y `tipo_persona` en la
misma tabla que el nivel, y evita una relación 1:1 de 400 mil filas que después obligaría a filtrado
bidireccional para que la tabla de ranking no muestre filas en blanco.

`Productos` no es opcional para la página de detalle de factura. `int_ventas_12m` tiene
`producto_id` pero no el nombre del producto ni la unidad de medida: las dos salen de
`int_productos_limpio`. Sin esa tabla, esa página muestra códigos.

`Niveles` existe por cuatro razones que no se resuelven de otra forma: ordena Alto → Medio →
Potencial → Minorista (alfabéticamente Power BI los pone Alto, Medio, Minorista, Potencial), guarda
el color de fondo de cada nivel para el formato condicional por campo, guarda el color de texto —que
se da vuelta a la mitad de la rampa para que la ficha se lea en los cuatro niveles— y guarda la frase
que explica qué significa cada uno.

| nivel_mayorista | orden_nivel | puntaje | color_hex | color_texto | que_significa |
|---|---|---|---|---|---|
| Alto | 1 | 3 | `#8C0D08` | `#FFFFFF` | Cumple los tres criterios |
| Medio | 2 | 2 | `#C8341F` | `#FFFFFF` | Cumple dos de los tres |
| Potencial | 3 | 1 | `#E4715C` | `#1A1112` | Cumple uno solo |
| Minorista | 4 | 0 | `#EFA091` | `#1A1112` | No cumple ninguno |

`Criterios` son tres filas, **completamente desconectada** del resto del modelo. Sirve para que el panel de "qué
criterio les falta" del ranking y el bloque de "por qué quedó en este nivel" de la ficha sean **un**
visual de tres filas en vez de tres visuales gemelos que después hay que mantener en paralelo.

| criterio | orden_criterio | etiqueta | en_una_palabra | que_mide |
|---|---|---|---|---|
| C1 | 1 | Volumen por visita | Cuánto se lleva | Promedio de unidades por factura, por unidad de medida |
| C2 | 2 | Repetición del volumen | Cuántas veces lo repite | % de sus facturas que superan, cada una por su cuenta, el corte de factura grande |
| C3 | 3 | Valor por visita | Cuánta plata deja | Venta promedio por factura en guaraníes, todas las unidades juntas |

La columna `en_una_palabra` no es decorativa. Sin ella C1 y C2 se leen los dos como "compra grande" y
el segundo parece redundante.

**`Criterios` no lleva ninguna relación, y hay que apagar la autodetección para que Power BI no le
invente una** (`Opciones → Archivo actual → Carga de datos → Detectar automáticamente nuevas
relaciones`). Por eso las columnas de orden se llaman `orden_nivel` y `orden_criterio` en vez de
`orden` en las dos: con el mismo nombre y el mismo tipo, Power BI las empareja solo. Cuando eso pasa,
poner `Criterios[criterio]` en el eje de un gráfico filtra `Niveles` a través de la relación fantasma
y cada barra se calcula sobre un nivel distinto — la de C1 sobre los Alto, la de C2 sobre los Medio,
la de C3 sobre los Potencial. Los números salen plausibles y están mal; el síntoma es C1 clavado en
100%. Hay otro par con el mismo riesgo: `Niveles[puntaje]` y `Clientes[puntaje]`.

### C1 y C2 no miden lo mismo, y hay una asimetría entre ellos

**C1 es un promedio** (`upt`): suma las unidades y divide por la cantidad de facturas. Dos compras
enormes entre veinte chicas alcanzan para levantarlo. Se compara contra el percentil 75 *entre
clientes* de esa unidad.

**C2 es una proporción** (`pct_tickets_grandes`): cuántas de sus facturas superan, cada una por su
cuenta, el corte `Q3 + 3×IQR` de esa unidad — un corte de outlier, mucho más exigente. Se compara
contra el percentil 75 entre clientes con piso de 0,40.

C1 pregunta cuánto se lleva; C2, cuántas veces lo repite. Un cliente cumple C1 y no C2 cuando su
volumen viene de picos.

Al revés casi no puede pasar, y conviene verificarlo antes de contar la metodología como "tres
criterios independientes": si el 40% de sus facturas superan `umbral_ticket_grande`, su promedio ya
vale como mínimo `0,40 × umbral_ticket_grande`.

```sql
select unidad_medida,
       umbral_ticket_grande,
       umbral_upt,
       0.40 * umbral_ticket_grande as piso_de_upt_que_implica_c2
from marts_mayoristas.mayoristas_v3_umbrales
```

Donde `piso_de_upt_que_implica_c2 >= umbral_upt`, la combinación (C1 = 0, C2 = 1) no puede existir y
el puntaje es una escalera —volumen, después repetición— con el valor por visita en paralelo. No
cambia ningún número; cambia cómo conviene explicarlo, y es la razón por la que la página 6 muestra
la barra de "volumen por visita" en cero.

Con `Facturas` importada, todas las relaciones del modelo son normales: los filtros propagan como
uno espera y `RELATED()` funciona. El modo Dual no hace falta en ningún caso.

La única relación limitada aparece si se arma la página 5: `Facturas` (Import) con `Líneas de venta`
(DirectQuery), por `ticket_id`, cardinalidad muchos a muchos y dirección `Facturas → Líneas de
venta` — `ticket_id` se repite en ambas, porque una factura con kilos y unidades ocupa dos filas en
`Facturas`. Ahí sí, `RELATED()` no funciona a través de una relación limitada: la medida `Cómo se
suma` usa `CALCULATETABLE` con un filtro sobre `Productos` en vez de `FILTER` + `RELATED` justamente
por eso.

## Lo que falta del lado de SQL

**El mart de la página 5.** `int_ventas_12m` no se puede consultar de a una factura, y no es cuestión
de agregarle un índice. No es una tabla: es una vista sobre `int_ventas_elegibles`, que es una vista
sobre `stg_ventas`, que es una vista sobre `dbo.Ventas_Ahorrazo` (238M+ filas). Una vista indexada
está descartada por tres motivos independientes — `SCHEMABINDING` prohíbe referenciar otras vistas,
hay un `LEFT JOIN` contra `productos_excluidos`, y la ventana usa `fecha_corte_mes_cerrado()`, que
expande a `GETDATE()` y no es determinista.

Y hay algo peor que el índice: `ticket_id` no es una columna, se calcula en `int_ventas_elegibles`
como `concat(timbrado, '_', factura_nro)`. Un `WHERE ticket_id = '…'` contra la vista se traduce a
`WHERE concat(…) = '…'` sobre la fact, que no puede usar ningún índice ni aunque `Ventas_Ahorrazo`
tuviera uno sobre esas dos columnas.

La salida es un mart propio, a grano de línea, con `ticket_id` ya resuelto e indexado. En
`models/marts/mayoristas/mayoristas_v3_lineas_factura.sql`:

```jinja
{{ config(
    materialized = 'table',
    indexes = [
        {'columns': ['ticket_id'], 'type': 'nonclustered'}
    ]
) }}

select
    v.cliente_id_limpio,
    v.ticket_id,
    v.fecha_venta,
    v.pdv_id,
    v.producto_id,
    p.unidad_medida,
    v.unidades,
    v.venta_gs
from {{ ref('int_ventas_12m') }} v
inner join {{ ref('int_productos_limpio') }} p
    on v.producto_id = p.producto_id
```

Los índices van con la opción `indexes` del adaptador, no con un `post_hook`: esta versión de
`dbt-sqlserver` los valida, les da un nombre determinista y los reconcilia entre corridas. El
adaptador además crea las tablas como *clustered columnstore* por defecto (`as_columnstore`), así
que 62 millones de filas y ocho columnas comprimen mucho más de lo que sugiere el número.

Vale la pena la pregunta previa: la página 4 ya contesta *"¿por qué esta factura contó como
grande?"* sin bajar a producto. Si nadie está preguntando todavía *"¿de dónde salieron esas 697
unidades?"*, dejar la página 5 afuera deja el tablero 100% importado, sin modelo compuesto y sin
gateway.

**Tendencia.** Resuelta por `mayoristas_v3_canasta` (arriba). La lectura que sigue sin poder hacerse
es cuántos clientes había en cada nivel en cada mes: la clasificación se recalcula entera en cada
corrida y no deja historia. Para eso habría que guardar una foto mensual de `mayoristas_v3_resumen`
con un snapshot de dbt, y conviene arrancarlo cuando los cortes ya no se toquen más.

**Una vista desactualizada, detectada de paso.** `int_ventas_elegibles.sql` tiene una columna
`tipo_persona` que el objeto en la base **no tiene**: el modelo se editó el 24/08 y el último build
compilado es del 20/08. Cualquier consulta a `intermediate.int_ventas_12m` hoy ve la forma vieja. No
bloquea nada —ningún modelo aguas abajo usa esa columna— pero un `dbt build --select
int_ventas_elegibles+` va a agregarle una columna a dos vistas que Power BI ya consulta.

## Power Query

Además del merge de `Clientes`, hacen falta cuatro consultas cortas: las tablas `Niveles` y
`Criterios` escritas a mano, el `Calendario` y la tabla `Actualización`. El código M de las cuatro
está en el artefacto, paso 1.

El calendario sale de una consulta escalar contra la tabla de facturas y se expande en M, para no
recorrer la tabla entera solo para saber el rango:

```sql
select min(fecha_venta) as fecha_min, max(fecha_venta) as fecha_max
from marts_mayoristas.mayoristas_v3_tickets_unidad
```

La hora de actualización no puede salir de `NOW()` en DAX: en el servicio devuelve UTC. Se genera una
tabla `Actualización` de una fila con `DateTime.LocalNow()` en Power Query, que sí se congela en el
momento del refresco.

Conviene apagar `Auto date/time` en Opciones → Archivo actual → Carga de datos, y poner la
configuración regional en Spanish (Paraguay): eso es lo que hace que `#,##0` se muestre como
`853.047` y no como `853,047`.

`celular` y `mail` vienen en `int_clientes_limpio` y son datos personales. Sirven en la ficha del
cliente, donde el vendedor tiene que llamar a alguien; no tienen nada que hacer en la tabla de
ranking ni en un export. Conviene dejarlos solo en la ficha.

Solo si se arma la página 5 el modelo pasa a ser compuesto, y ahí sí necesita un gateway de datos
local para publicarse en el servicio. Sin ella, no hace falta ninguno.

## Tabla legacy `mayoristas_v3_detalle_unidad`

`marts_mayoristas.mayoristas_v3_detalle_unidad` (774.619 filas) sigue
existiendo físicamente en la base -- la escribía
`analisis_mayoristas_v3.ipynb` directo por pandas antes del port a dbt.
dbt no la toca ni la actualiza, así que va quedando cada vez más vieja.
Mismas 9 columnas, mismo orden, que `mayoristas_v3_metricas_unidad`: es
su predecesora directa. Si algún tablero viejo la consulta directo
(`Item="mayoristas_v3_detalle_unidad"` en Power Query), repuntarlo a
`mayoristas_v3_metricas_unidad` antes de dar de baja la tabla legacy.

## Todavía abierto

El pipeline no corrió contra la base real después del rediseño de la consulta, así que los umbrales y
la distribución que muestre el tablero la primera vez son lo primero a verificar. Los números de
referencia de la validación en Python — Minorista 238.235, Medio 74.010, Potencial 69.016, Alto
17.172, y el cliente 2504462 en Alto con puntaje 3 — sirven para el chequeo visual, no para
cablearlos en ningún lado.

El estado del pipeline del lado de dbt (qué modelos son fuente de producción y cuáles todavía no)
vive en `dbt_ahorrazo/PENDIENTES.md`, que es donde corresponde. El tablero no lo refleja: para quien
lo abre, hay una sola clasificación.
