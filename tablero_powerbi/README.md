# Análisis Mayorista

El tablero de Power BI que muestra la clasificación de clientes mayoristas.
Consume las tablas `marts_mayoristas.mayoristas_v3_*` y la línea de venta de
`intermediate.int_ventas_12m_tabla`.

`modelo_sobre_linea.html` documenta cómo quedó armado el modelo semántico después
de importar la línea de venta, con el paso a paso del modelado, las medidas y las
páginas maquetadas. Se abre en el navegador.

## Qué hay en esta carpeta

`tema_radar_mayorista.json` es el tema de Power BI con la paleta de la marca, la
rampa de colores por nivel y los estilos de tablas, ejes y tarjetas. Se importa
desde Ver → Temas → Buscar temas. Usa Segoe UI en sus tres pesos, la única
familia que Power BI garantiza en cualquier máquina y en el servicio.

`validar_tema.js` compara el tema contra el esquema JSON oficial de Microsoft
antes de importarlo. Conviene correrlo cada vez que se lo toca: la raíz del
esquema tiene `additionalProperties: false`, así que una sola clave mal escrita
rechaza el archivo entero, y el mensaje de error de Power BI no dice cuál es.

```
npm install
npm run validar
```

Prueba contra cuatro versiones del esquema, que cubren unos dos años de Power BI
Desktop, y se cachean en `.schemas/`. Detecta tres cosas: propiedades que no
existen, valores del tipo equivocado (`outlineStyle` es un entero y no un texto)
y nombres inventados dentro del bloque `visualStyles["*"]`, que no rompen la
importación pero Power BI ignora en silencio.

`medidas_radar_ventas.dax` tiene las medidas del modelo actual, agrupadas por
página y comentadas. `medidas_radar_ventas_bulk.tsv` es el mismo contenido en el
formato que Tabular Editor 2 acepta pegado en bloque, con nombre, expresión y
carpeta.

El `.dax` es la fuente y el TSV se genera desde él:

```
python generar_tsv.py medidas_radar_ventas.dax
```

Hay que regenerarlo cada vez que se edita una medida. Si los dos archivos se
desincronizan, alguien va a pegar la versión vieja sin darse cuenta.

`medidas_radar_mayorista.dax` es la versión anterior, la del modelo sin la línea
de venta. Queda como referencia.

## Los tres criterios, explicados sin tecnicismos

`diagramas/` tiene siete diagramas pensados para alguien que no va a leer una
línea de SQL: el equipo comercial, la gerencia, o el propio cliente cuando
pregunte por qué su cuenta quedó en un nivel y no en otro.

| Diagrama | Contesta |
|---|---|
| [C1, volumen por visita](diagramas/c1_volumen_por_visita.svg) | ¿Se lleva mucho cada vez que viene? |
| [C2, repetición del volumen](diagramas/c2_repeticion_del_volumen.svg) | ¿La compra grande la repite seguido? |
| [C3, valor por visita](diagramas/c3_valor_por_visita.svg) | ¿Cuánta plata deja cada vez que viene? |
| [El corte de factura grande](diagramas/corte_factura_grande.svg) | ¿De dónde sale el número a partir del cual una factura es grande? |
| [C1 con números reales](diagramas/c1_ejemplo_unidades.svg) | El criterio recorrido con un cliente y el corte vigente, en unidades |
| [C2 con números reales](diagramas/c2_ejemplo_kilos.svg) | Lo mismo en kilos, encadenado con el diagrama del corte |
| [C3 con números reales](diagramas/c3_ejemplo.svg) | Lo mismo en guaraníes, con un cliente real de la base |

Están en SVG para que escalen sin perder nitidez al proyectarlos.

**No se editan a mano.** Salen de `diagramas/generar_diagramas.py`, y una corrida
del script pisa cualquier cambio hecho sobre el archivo. El texto vive en ese
script, y los cortes que aparecen en los ejemplos están como constantes al
principio: después de cada `dbt build` de la cadena de mayoristas hay que
releerlos con `analyses/verificar_umbral_ticket_grande.sql` y volver a generar.

```
python diagramas/generar_diagramas.py
```

Un punto a sostener al presentarlos: el corte de cada criterio sale de comparar
al cliente contra el resto de la base durante un año. Es la primera objeción que
aparece cuando alguien no está de acuerdo con su nivel.

## El modelo

Todo importado. Sin modelo compuesto, sin DirectQuery y sin gateway.

| Tabla | Origen | Filas |
|---|---|---|
| `Ventas` | `intermediate.int_ventas_12m_tabla` | 62 M |
| `Clientes` | `mayoristas_v3_resumen`, con merge de `int_clientes_limpio` y de `mayoristas_v3_frecuencia_compra` | 385 K |
| `Facturas` | `mayoristas_v3_tickets_unidad` | 7,4 M |
| `Métricas por unidad` | `mayoristas_v3_metricas_unidad` | 775 K |
| `Unidades` | `mayoristas_v3_umbrales` | 4 |
| `Productos` | `int_productos_limpio` | 40 K |
| `Sucursales` | seed `dim_sucursal_mapeo` | 3 |
| `Niveles`, `Criterios`, `Frecuencias` | escritas a mano en Power Query | 4, 3 y 6 |
| `Calendario` | generado en Power Query | ~365 |
| `Actualización` | `DateTime.LocalNow()` en Power Query | 1 |

El grano de cada tabla es la decisión que más cambia el modelo, y los nombres se
parecen entre sí. Una fila de `Ventas` es una línea de producto de una factura.
Una de `Facturas` es una factura dentro de una unidad de medida, así que una
compra con kilos y unidades ocupa dos filas. Una de `Métricas por unidad` es un
cliente dentro de una unidad de medida.

### Qué se importa de `Ventas`

De las nueve columnas que devuelve la vista van siete: `cliente_id_limpio`,
`producto_id`, `pdv_id`, `fecha_venta`, `venta_gs`, `unidades` y `ticket_id`.
Quedan afuera `cliente_id`, que es el identificador previo al colapso, y
`tipo_persona`, que ya está en `Clientes` a grano cliente.

`venta_gs` va como **Fixed decimal number**. En el contrato de `stg_ventas` es
`float`, y sumar millones de floats en paralelo no da el mismo resultado entre
corridas: el orden en que el motor combina los parciales cambia los últimos
dígitos. El tipo decimal lo hace reproducible y además comprime mejor, porque
VertiPaq lo guarda como entero escalado.

La consulta se acota a la ventana que tienen los marts, leyendo el rango de
ellos:

```sql
select v.cliente_id_limpio, v.producto_id, v.pdv_id, v.fecha_venta,
       v.venta_gs, v.unidades, v.ticket_id
from intermediate.int_ventas_12m_tabla v
where v.fecha_venta >= (select min(fecha_venta) from marts_mayoristas.mayoristas_v3_tickets_unidad)
  and v.fecha_venta <= (select max(fecha_venta) from marts_mayoristas.mayoristas_v3_tickets_unidad)
```

El motivo: las tablas de `marts_mayoristas` guardan la ventana del día en que
corrió dbt, y el DAG semanal de Top 300 y el mensual de Mayoristas reconstruyen
`int_ventas_12m_tabla` con cadencias distintas. Acotar al rango de los marts
mantiene el hecho y la clasificación mirando el mismo período. El síntoma de no
hacerlo aparece en tres lugares a la vez: un nivel sin nombre en los
segmentadores, un día de la semana en blanco, y `[Clientes activos]` devolviendo
un número donde `[Cantidad de clientes]` no devuelve nada.

### Las relaciones

Todas uno a muchos, dirección única, desde la dimensión hacia el hecho.

```
Clientes    → Ventas, Facturas, Métricas por unidad
Calendario  → Ventas, Facturas
Productos   → Ventas
Sucursales  → Ventas
Unidades    → Facturas, Métricas por unidad
Niveles     → Clientes
Frecuencias → Clientes
```

Dos relaciones que el modelo no admite y conviene conocer antes de intentarlas.

`Unidades` no llega a `Ventas`. La línea de producto no tiene `unidad_medida`,
la hereda de `Productos`, y relacionar `Unidades` con `Productos` crearía un
segundo camino hacia los hechos. En las páginas de metodología se usa
`Unidades[unidad_medida]`, que filtra `Facturas` y `Métricas por unidad`; en las
de canasta se usa `Productos[unidad_medida]`, que filtra `Ventas`. Hay que
ocultar una de las dos en cada contexto.

`Facturas` no se relaciona con `Ventas` por `ticket_id`. Las dos cuelgan de
`Clientes`, así que unirlas crearía dos caminos desde `Clientes` hasta `Ventas` y
Power BI rechaza la relación. Para bajar de una factura a sus líneas se usa
`TREATAS`, que aplica los valores de una columna como filtro sobre otra sin
relación de por medio.

### Las tablas escritas a mano

`Niveles`, `Criterios` y `Frecuencias` existen por tres motivos que no se
resuelven de otra forma: ordenar (alfabéticamente Power BI pone Alto, Medio,
Minorista, Potencial), guardar el color de cada valor para el formato condicional
por campo, y guardar la frase que explica qué significa cada uno en lenguaje de
negocio.

| nivel_mayorista | orden_nivel | puntaje | color_hex | color_texto |
|---|---|---|---|---|
| Alto | 1 | 3 | `#8C0D08` | `#FFFFFF` |
| Medio | 2 | 2 | `#C8341F` | `#FFFFFF` |
| Potencial | 3 | 1 | `#E4715C` | `#1A1112` |
| Minorista | 4 | 0 | `#EFA091` | `#1A1112` |

`Criterios` tiene tres filas y no lleva ninguna relación. Sirve para que el panel
de qué criterio le falta a un cliente sea un visual de tres filas en lugar de
tres visuales gemelos que después hay que mantener en paralelo.

Las columnas de orden se llaman `orden_nivel`, `orden_criterio` y
`orden_frecuencia`. Con el nombre `orden` a secas y el mismo tipo, Power BI las
empareja solo si la detección automática de relaciones está encendida, y ahí
poner `Criterios[criterio]` en un eje filtra `Niveles` por una relación
inventada: cada barra se calcula sobre un nivel distinto y los números salen
plausibles. Hay que apagar esa opción en Opciones → Archivo actual → Carga de
datos.

## Power Query

Además de los merges de `Clientes`, hacen falta cuatro consultas cortas: las
tablas `Niveles`, `Criterios` y `Frecuencias` escritas a mano, el `Calendario` y
la tabla `Actualización`. El código M del calendario está en
`modelo_sobre_linea.html`, con las cinco columnas de corte de tiempo y sus
columnas de orden.

El rango del calendario sale de los dos hechos, `Ventas` y `Facturas`, y se
redondea a meses enteros. Si cubre menos que alguno de ellos, las fechas que
sobran caen en una fila en blanco y aparecen como una categoría sin etiqueta en
cualquier eje de tiempo.

Los nombres de día y de mes van escritos como lista en el código M, no con
`Date.DayOfWeekName` ni `Date.MonthName`. Esas dos funciones dependen de la
configuración regional del archivo y de la máquina, así que el mismo informe
puede mostrar "Monday" en un equipo y "lunes" en otro.

La hora de actualización no puede salir de `NOW()` en DAX, que en el servicio
devuelve UTC. Se genera una tabla `Actualización` de una fila con
`DateTime.LocalNow()`, que se congela en el momento del refresco.

Conviene apagar `Auto date/time` y poner la configuración regional del archivo en
Spanish (Paraguay). Eso es lo que hace que `#,##0` se muestre como `853.047` y no
como `853,047`.

`celular` y `mail` vienen en `int_clientes_limpio` y son datos personales. Sirven
en la ficha del cliente, donde el vendedor tiene que llamar a alguien. No van en
la tabla de ranking ni en un export.
