# Tablero Análisis Mayorista

Documentación del informe de Power BI que muestra la clasificación de clientes
mayoristas: de dónde salen los datos, cómo está armado el modelo, qué hace cada
medida y dónde está cada archivo.

Actualizado en septiembre de 2026.

## Qué muestra el informe

El informe clasifica a cada cliente de Ahorrazo en uno de cuatro niveles, según
cuánto se parece su forma de comprar a la de alguien que revende: Alto, Medio,
Potencial o Minorista. Sobre esa clasificación permite explorar quiénes son esos
clientes, qué compran, dónde y cuándo, y bajar hasta una factura puntual para
entender por qué un cliente quedó en un nivel y no en otro.

Lo usa el equipo comercial para tratar distinto a quien compra distinto, con
precios diferenciados, condiciones de crédito y atención dedicada. Y es el paso
previo a los análisis de segmentación que vienen después, que se hacen por
separado para mayoristas y para el resto de los clientes.

La metodología de clasificación está documentada aparte, con un diagrama por
criterio, en la carpeta de diagramas que se detalla más abajo.

## Dónde está cada archivo

Todo vive en el repositorio `analisis_mayorista`, dentro de la carpeta
`tablero_powerbi/`. Las rutas de la tabla son relativas a esa carpeta.

| Archivo | Qué es |
|---|---|
| `Analisis Mayorista.pbix` | El informe de Power BI. Vive en la carpeta de tableros del equipo, fuera de este repositorio. |
| `tema_radar_mayorista.json` | El tema de Power BI con la paleta de la marca, la escala de colores por nivel y los estilos de tablas, ejes y tarjetas. Se importa desde Ver → Temas → Buscar temas. |
| `medidas_radar_ventas.dax` | Las 91 medidas del informe, agrupadas por página y con un comentario que explica cada decisión. Es la referencia de lo que hay en el modelo. |
| `validar_tema.js` y `package.json` | Verifican el tema contra el esquema oficial de Microsoft antes de importarlo. Se ejecutan con `npm install` una vez y después `npm run validar`. |
| `diagramas/` | Siete diagramas que explican la metodología sin tecnicismos, pensados para presentar al equipo comercial o a la gerencia. Uno por criterio, uno del corte de factura grande, y tres con números reales. |
| `diagramas/generar_diagramas.py` | Genera esos diagramas. Los archivos no se editan a mano: el texto y los cortes viven en este script. |
| `generar_tsv.py` | Convierte el archivo de medidas a un formato de carga en bloque. Queda como utilidad; las medidas del informe actual se cargaron una por una. |
| `README.md` | La versión corta de este documento, para quien trabaje sobre el repositorio. |

Los modelos que producen las tablas que consume el informe están en otro
repositorio, `dbt_ahorrazo`, en la carpeta `models/marts/mayoristas/`. Ahí no hay
nada que tocar para trabajar en el informe, pero es donde se define la
clasificación.

## De dónde salen los datos

Todas las tablas se importan desde SQL Server, base `DATO_SOLUTIONS`. El informe
no consulta la base mientras alguien lo usa: trae una copia de los datos en cada
actualización y trabaja sobre esa copia. Eso lo hace rápido y quita la necesidad
de una puerta de enlace de datos.

| Tabla en el informe | Tabla de origen | Filas |
|---|---|---|
| Ventas | `intermediate.int_ventas_12m_tabla` | 62 M |
| Clientes | `marts_mayoristas.mayoristas_v3_resumen` | 385 K |
| Facturas | `marts_mayoristas.mayoristas_v3_tickets_unidad` | 7,4 M |
| Métricas por unidad | `marts_mayoristas.mayoristas_v3_metricas_unidad` | 775 K |
| Unidades | `marts_mayoristas.mayoristas_v3_umbrales` | 4 |
| Productos | `intermediate.int_productos_limpio` | 40 K |
| Sucursales | `seeds.dim_sucursal_mapeo` | 3 |

A la tabla `Clientes` se le agregan dos cruces más en la carga: el nombre, el
documento y el contacto de cada cliente desde `int_clientes_limpio`, y cada
cuánto viene desde `mayoristas_v3_frecuencia_compra`. Las dos son a nivel
cliente, igual que `Clientes`, así que se suman como columnas en lugar de entrar
como tablas aparte.

Hay además cinco tablas que no vienen de la base y se escriben dentro del propio
informe: `Niveles`, `Criterios` y `Frecuencias`, que guardan el orden, el color y
la descripción de cada valor; `Calendario`, que se genera a partir del rango de
fechas de los datos; y `Actualización`, con una sola fila que registra la hora
del último refresco.

## Qué representa una fila de cada tabla

Los nombres de las tablas se parecen y el nivel de detalle de cada una es lo que
más confunde al principio. Conviene tenerlo claro antes de armar cualquier
visual, porque determina qué se puede contar y qué no.

| Tabla | Una fila es |
|---|---|
| **Ventas** | Una línea de producto dentro de una factura. Si una factura tiene ocho productos, ocupa ocho filas. |
| **Facturas** | Una factura dentro de una unidad de medida. Una compra con kilos y unidades ocupa dos filas, porque cada parte se mide contra un corte distinto. |
| **Métricas por unidad** | Un cliente dentro de una unidad de medida, con su promedio y su porcentaje de facturas grandes en esa unidad. |
| **Clientes** | Un cliente, con su nivel, su puntaje y sus datos de contacto. |
| **Productos** | Un producto del catálogo, con sus categorías y su unidad de medida. |
| **Unidades** | Una unidad de medida, con los cortes de la metodología. |
| **Sucursales** | Una sucursal. |
| **Calendario** | Un día. |

## Cómo se conectan las tablas

Una relación es un camino por el que viaja un filtro. Cuando alguien elige *Alto*
en un segmentador, ese filtro sale de la tabla donde está la palabra y recorre
las relaciones hasta llegar a las tablas que tienen los números. Todo lo que
muestra el informe depende de que ese camino exista.

El modelo tiene dos clases de tabla. Las que describen algo, como clientes,
productos, sucursales, fechas y unidades de medida, y las que miden algo: ventas,
facturas y métricas. Los filtros viajan siempre en la misma dirección, desde las
que describen hacia las que miden, y nunca al revés.

| Desde | Hacia | Por qué columna |
|---|---|---|
| Clientes | Ventas, Facturas, Métricas por unidad | `cliente_id_limpio` |
| Calendario | Ventas, Facturas | `fecha` |
| Productos | Ventas | `producto_id` |
| Sucursales | Ventas | `pdv_id` |
| Unidades | Facturas, Métricas por unidad | `unidad_medida` |
| Niveles | Clientes | `nivel_mayorista` |
| Frecuencias | Clientes | `frecuencia_compra` |

Leído de corrido: elegir un nivel filtra `Clientes`, y desde ahí el filtro llega
a las tres tablas que miden. Elegir una categoría filtra `Productos`, y desde ahí
llega a `Ventas`. Elegir un mes filtra `Calendario`, y llega a `Ventas` y a
`Facturas`. Las tres cosas se combinan sin que haya que hacer nada más.

### Dos conexiones que el modelo no tiene, y no es un olvido

**`Unidades` no llega a `Ventas`.** La línea de producto no guarda la unidad de
medida: la hereda del producto. Conectar `Unidades` con `Productos` crearía dos
caminos posibles hacia los mismos números, y ante eso Power BI desactiva uno por
su cuenta sin avisar cuál.

La consecuencia práctica es que hay dos campos con el mismo nombre y usos
distintos. En las páginas que hablan de la metodología va
`Unidades[unidad_medida]`, que filtra `Facturas` y `Métricas por unidad`. En las
que hablan de qué compra cada segmento va `Productos[unidad_medida]`, que filtra
`Ventas`. Usar el equivocado da un visual que no responde al segmentador.

**`Facturas` no se conecta con `Ventas`.** Las dos cuelgan de `Clientes`, así que
unirlas por el número de factura crearía otra vez dos caminos hacia los mismos
números. Para bajar de una factura a sus líneas de producto, las medidas de la
página de detalle usan una función de DAX llamada `TREATAS`, que aplica el valor
de una columna como filtro sobre otra tabla sin necesidad de una relación.

## Cómo están organizadas las 91 medidas

Están en catorce carpetas dentro del modelo, numeradas para que el orden sea el
mismo que el de las páginas del informe. La numeración tiene dos saltos, de
grupos que se descartaron al armar el informe. El archivo
`medidas_radar_ventas.dax` tiene la definición de cada una con un comentario que
explica por qué está escrita así.

| Carpeta | Medidas | Para qué |
|---|---|---|
| 01. Base | 10 | Los totales de los que dependen casi todas las demás: venta, unidades, clientes, facturas. |
| 02. Ticket promedio | 4 | Cuánto deja una factura, medido de tres formas distintas. |
| 03. Criterios | 6 | Cuántos clientes cumplen cada criterio y qué porcentaje representan. |
| 04. Métricas por unidad de medida | 10 | El promedio y el porcentaje de facturas grandes de un cliente en cada unidad, contra su corte. |
| 05. Facturas | 12 | Todo lo de una factura puntual, incluida la página de detalle. |
| 06. Explicación | 10 | Las frases en castellano que explican por qué un cliente quedó en su nivel. |
| 07. Página de metodología | 6 | Los cortes vigentes, escritos como texto que se arma solo leyendo la tabla de umbrales. |
| 08. Cabecera | 1 | Hasta cuándo llegan los datos y cuándo fue el último refresco. |
| 09. Tendencia | 4 | La evolución mensual. |
| 11. Criterios como dimensión | 6 | Permiten mostrar los tres criterios como filas de un mismo visual. |
| 12. Textos de encabezado | 6 | Los títulos que cambian según lo que esté seleccionado. |
| 13. Colores condicionales | 1 | El color de fondo de la tarjeta de factura grande. |
| 15. Canasta | 9 | Qué compra cada segmento y cuánto se despega del promedio de la cadena. |
| 16. Frecuencia de compra | 6 | Cada cuánto viene cada cliente. |

### Las que conviene entender antes de tocar nada

**`Venta`** es la única medida de venta del informe. Cualquier visual que muestre
guaraníes sale de ella. Responde a todos los filtros: fecha, sucursal, producto,
categoría y cliente.

La tabla `Clientes` tiene además una columna con la venta anual de cada cliente.
Esa columna sirve para explicar por qué un cliente quedó en su nivel, y no se
mueve con ningún segmentador porque está calculada sobre los doce meses
completos. No hay que usarla para totalizar en un visual.

**`Clientes activos`** cuenta los clientes con al menos una compra en lo que esté
filtrado. Es el denominador que va con `Venta`. Existe una segunda medida,
`Cantidad de clientes`, que cuenta las filas de la tabla `Clientes` y no responde
al filtro de fecha. Dividir la venta de un mes por ese conteo da la venta de un
mes repartida entre los clientes de doce, y el resultado parece razonable aunque
esté mal.

**Los dos índices de afinidad** comparan cuánto pesa una categoría dentro de un
segmento contra cuánto pesa en toda la cadena. Un valor de 1,0 significa que ese
segmento compra esa categoría en la misma proporción que el resto. El de venta
mide dónde deja la plata y el de unidades dónde se lleva el volumen; cuando el
segundo es más alto que el primero, el segmento compra barato y por bulto en esa
categoría, que es el perfil mayorista.

El de unidades queda vacío mientras haya más de una unidad de medida
seleccionada. Sumar 130 kilos con 697 unidades no da 827 de nada, y la medida se
niega a mostrar ese número en lugar de mostrarlo mal.

**Las medidas de porcentaje están atadas a un eje.** Un porcentaje del total
necesita que su denominador quite el filtro que pone el eje del visual. Hay una
por eje: una para categorías, una para períodos y una para sucursales. Usar la de
categorías sobre un eje de tiempo devuelve 100% en todas las filas.

## Las páginas del informe

| Página | Qué responde |
|---|---|
| **Resumen ejecutivo** | Cuántos clientes hay en cada nivel, cuánta venta explica cada uno, y cuánto vale un cliente de cada nivel comparado con el promedio de la cadena. |
| **Cómo se define un mayorista** | La metodología en castellano, con los cortes vigentes tomados de la base. Sin gráficos, a propósito. |
| **Ranking y exploración** | La lista de clientes, filtrable por nivel, tipo de persona y frecuencia, y buscable por nombre o documento. Es el punto de entrada para llegar a un cliente puntual. |
| **Ficha del cliente** | Por qué ese cliente quedó en ese nivel, su detalle por unidad de medida contra cada corte, y el historial de sus facturas. |
| **Detalle de factura** | Qué había dentro de una factura, contra qué corte se midió cada parte y qué producto explica su volumen. Es una página oculta: se llega desde el historial de la ficha. |
| **Qué y dónde compra** | Qué categorías compra de más cada segmento, en qué sucursal, y la lista de clientes del segmento, exportable. |
| **Cuándo compra** | El ritmo de compra: día de la semana, día del mes, quincena y evolución mensual. |

Las dos últimas están separadas por un motivo concreto. Los índices de afinidad
solo tienen sentido sobre un eje de categorías: puestos sobre un eje de fechas
devuelven 1,0 en todas las filas, y el gráfico resultante parece correcto.
Tenerlas en páginas distintas evita que alguien arrastre esa medida a un visual
de tiempo.

## Cómo se actualiza

Las tablas de origen las reconstruye un proceso automático el día 1 de cada mes a
las 02:00. El informe se actualiza después de eso, y trae los datos de los doce
meses cerrados más recientes.

La consulta que trae las ventas se acota al mismo período que tienen las tablas
de clasificación, leyendo ese rango de ellas:

```sql
where v.fecha_venta >= (select min(fecha_venta) from marts_mayoristas.mayoristas_v3_tickets_unidad)
  and v.fecha_venta <= (select max(fecha_venta) from marts_mayoristas.mayoristas_v3_tickets_unidad)
```

Sin ese recorte, el informe puede traer ventas de un período que la clasificación
todavía no cubre, y aparecen clientes sin nivel asignado.

### Si algo no cuadra, empezar por acá

Tres síntomas aparecen juntos cuando el informe trae un período distinto al de la
clasificación: un nivel sin nombre en los segmentadores, un día de la semana en
blanco en los gráficos de tiempo, y clientes que aparecen en las ventas pero no
en la lista de clientes.

Los tres se resuelven igual: verificar que las tablas de origen cubran el mismo
rango de fechas, y volver a actualizar el informe después de que el proceso
mensual haya terminado.

### Al agregar una columna nueva en origen

Cuando una tabla de la base gana una columna, esa columna no aparece sola al
actualizar si la consulta del informe tiene una selección explícita de columnas o
un cruce con otra tabla. Hay que editar ese paso en el editor de consultas y
marcar la columna nueva. Es la causa más habitual de que un campo esperado no
aparezca después de un cambio en la base.
