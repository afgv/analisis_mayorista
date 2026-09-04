# Análisis Mayorista

Clasifica a los clientes de Ahorrazo según cuánto se parece su forma de comprar a
la de alguien que revende, para que el equipo comercial pueda tratar distinto a
quien compra distinto: precios diferenciados, condiciones de crédito y atención
dedicada.

Es además el paso previo a los dos análisis que siguen. La segmentación RFM y la
segmentación cualitativa se hacen por separado para mayoristas y para el resto,
porque medirlos con la misma vara deforma las dos: un mayorista que viene una vez
por mes es un cliente frecuente, y un vecino que viene una vez por mes es un
cliente esporádico.

## Cómo clasifica

Tres criterios sobre una ventana móvil de doce meses cerrados, un punto cada uno.
La suma da el nivel: 3 es Alto, 2 Medio, 1 Potencial y 0 Minorista.

| Criterio | Pregunta | Cumple si |
|---|---|---|
| C1, volumen por visita | ¿Se lleva mucho cada vez que viene? | Su promedio de unidades por factura está en el 25% más alto, en alguna unidad de medida |
| C2, repetición del volumen | ¿La compra grande la repite seguido? | Al menos 4 de cada 10 de sus facturas superaron el corte de factura grande, en alguna unidad de medida |
| C3, valor por visita | ¿Cuánta plata deja cada vez que viene? | Su venta promedio por factura está en el 25% más alto |

C1 y C2 se calculan por separado en cada unidad de medida y solo sobre las cuatro
con volumen real: unidades, kilos, litros y packs. Un cliente que se lleva 130
kilos de carne y 697 unidades sueltas en la misma factura no compró 827 de nada.
C3 toma las siete unidades de medida juntas, porque ahí todo está en guaraníes.

Los diagramas que explican esto sin tecnicismos, pensados para el equipo
comercial y para la gerencia, están en
[`tablero_powerbi/diagramas/`](tablero_powerbi/diagramas/).

## Dónde vive cada cosa

El cálculo corre en dbt, en `dbt_ahorrazo/models/marts/mayoristas/`. Se agenda
solo, en el DAG mensual de Airflow, el día 1 a las 02:00. Ninguna de las tablas
se escribe desde este repo.

El tablero de Power BI y su documentación están en
[`tablero_powerbi/`](tablero_powerbi/): el tema, las medidas DAX, los diagramas y
el documento del modelo semántico.

En esta carpeta quedan los notebooks. `analisis_mayoristas_v3.ipynb` es el de
desarrollo, con la exploración que motivó la metodología actual: los boxplots y
las medidas de forma que mostraron que sumar unidades de medida distintas no
tenía sentido. `validacion_mayoristas_v3.ipynb` lee las tablas que produce dbt y
verifica los resultados. Los de v1 y v2 quedan como registro de las metodologías
anteriores.

`conexion_bd.py` arma la conexión a SQL Server leyendo el `.env`, y lo comparten
los notebooks de este repo.

## Para trabajar acá

```
python -m venv .venv_mayoristas
source .venv_mayoristas/Scripts/activate
pip install -r requirements.txt
```

Hace falta un `.env` en la raíz con `DB_SERVER`, `DB_DATABASE`, `DB_USER` y
`DB_PASSWORD`.
