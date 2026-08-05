"""Identificación de clientes mayoristas -- metodología v2 (reglas Q3 + 1.5xIQR).

Clasifica a cada cliente en un nivel (Alto / Medio / Potencial / Minorista)
según 3 señales de comportamiento de compra: unidades por ticket, proporción
de tickets grandes, y gasto total. Los umbrales de cada señal son dinámicos
(percentil 75 del universo analizado), no valores fijos.

Metodología ya validada como canónica frente a v1 (`analisis_mayoristas.py`,
score con ML/KMeans/IsolationForest) -- v1 agrupaba bien pero era difícil de
explicar a negocio; v2 es más simple y auditable.

Estado: la misma clasificación ya corre en SQL Server vía dbt
(`dbt_ahorrazo/models/marts/mayoristas/dim_clientes_mayoristas.sql`), que es
la fuente de verdad para producción una vez validada. Este script queda
como herramienta de exploración/auditoría ad-hoc y como referencia legible
de la metodología -- no escribe a una tabla que otro proceso consuma como
autoritativa sin que alguien lo decida explícitamente (ver `main()`).

Reemplaza a `analisis_mayoristas_v2.ipynb`.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.dialects.mssql import FLOAT, INTEGER, NVARCHAR

from conexion_bd import conexion_bd, engine_sqlalchemy_bd, escribir_tabla_segura

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# Configuración
# ============================================================

@dataclass(frozen=True)
class ConfiguracionAnalisis:
    """Todo lo ajustable del análisis, en un solo lugar."""

    fecha_inicio: str = "2025-01-01"
    fecha_fin: str = "2026-01-01"  # exclusivo (< esta fecha)

    # Piso mínimo para el criterio "% tickets grandes" (C2). El cliente
    # necesita al menos esta fracción de tickets grandes para cumplirlo,
    # incluso si el Q3 del universo da un número más bajo.
    pct_grandes_minimo: float = 0.40

    # Cliente genérico / sin identificar.
    excluir_clientes: tuple[str, ...] = ("44444401-7",)

    # Bolsas, servicios y productos sin valor analítico -- misma lista
    # que usa top_300_productos y dbt_ahorrazo/seeds/productos_excluidos.csv.
    excluir_productos: tuple[str, ...] = (
        "442", "999994", "31852", "999992", "90311",
        "999998", "901762", "9809999", "51457", "77190",
        "8925", "91988", "999995",
    )

    nombre_sucursal: dict[str, str] = field(default_factory=lambda: {
        "3": "Ahorrazo SL",
        "5": "Ahorrazo R1",
        "6": "Ahorrazo R2",
    })


NIVELES_ORDENADOS = ["Alto", "Medio", "Potencial", "Minorista"]


# ============================================================
# 1. Carga y limpieza
# ============================================================

def cargar_ventas(config: ConfiguracionAnalisis) -> pd.DataFrame:
    """Lee dbo.view_ventas_ahorrazo_filtradas_12m para el período configurado."""
    sql = "SELECT * FROM dbo.view_ventas_ahorrazo_filtradas_12m;"
    with conexion_bd() as conn:
        df = pd.read_sql_query(
            sql, conn, params=[config.fecha_inicio, config.fecha_fin]
        )
    logger.info("Filas cargadas: %s", f"{len(df):,}")
    return df


def limpiar_ventas(df: pd.DataFrame, config: ConfiguracionAnalisis) -> pd.DataFrame:
    """Tipa columnas y aplica las exclusiones de cliente/producto."""
    ventas = df.copy()
    ventas["fecha_venta"] = pd.to_datetime(ventas["fecha_venta"])
    for col in ("cliente_id", "cliente_id_limpio", "producto_id", "pdv_id"):
        ventas[col] = ventas[col].astype(str)

    ventas = ventas[~ventas["cliente_id"].isin(config.excluir_clientes)]
    ventas = ventas[~ventas["producto_id"].isin(config.excluir_productos)]

    logger.info("Filas después de limpieza: %s", f"{len(ventas):,}")
    logger.info("Clientes únicos: %s", f"{ventas['cliente_id_limpio'].nunique():,}")
    return ventas


# ============================================================
# 2. Umbral de "ticket grande" (boxplot: Q3 + 1.5xIQR)
# ============================================================

def calcular_umbral_ticket_grande(ventas: pd.DataFrame) -> float:
    """
    Umbral de "ticket grande" = Q3 + 1.5*IQR sobre la distribución de
    unidades por ticket de TODO el universo (no por cliente), excluyendo
    tickets con unidades <= 0 (devoluciones).

    Se calcula una sola vez sobre el universo completo y se reutiliza en
    todos los scopes (global + cada sucursal) para que los resultados
    sean comparables entre sí.
    """
    unidades_por_ticket = ventas.groupby("ticket_id", dropna=False)["unidades"].sum()
    unidades_por_ticket = unidades_por_ticket[unidades_por_ticket > 0]

    q1 = unidades_por_ticket.quantile(0.25)
    q3 = unidades_por_ticket.quantile(0.75)
    iqr = q3 - q1
    umbral = q3 + 1.5 * iqr

    logger.info("Distribución unidades/ticket -> Q1=%.1f Q3=%.1f IQR=%.1f", q1, q3, iqr)
    logger.info("Umbral ticket grande (Q3 + 1.5xIQR) = %.1f unidades", umbral)
    return float(umbral)


# ============================================================
# 3. Métricas base por cliente
# ============================================================

def calcular_metricas(
    ventas: pd.DataFrame, group_cols: list[str], umbral_ticket_grande: float
) -> pd.DataFrame:
    """
    Métricas de comportamiento por cliente (o cliente x sucursal):
      - tickets_unicos, dias_con_compra
      - upt: unidades promedio por ticket
      - pct_tickets_grandes: fracción de tickets que superan el umbral
      - ventas_totales
    """
    upt_por_ticket = (
        ventas.groupby(group_cols + ["ticket_id"], dropna=False)["unidades"]
        .sum()
        .reset_index()
        .rename(columns={"unidades": "upt"})
    )
    upt_por_ticket = upt_por_ticket[upt_por_ticket["upt"] > 0]

    metricas_ticket = (
        upt_por_ticket.groupby(group_cols, dropna=False)
        .agg(
            tickets_unicos=("ticket_id", "count"),
            upt_suma=("upt", "sum"),
            tickets_grandes=("upt", lambda x: (x > umbral_ticket_grande).sum()),
        )
        .reset_index()
    )
    metricas_ticket["upt"] = (
        metricas_ticket["upt_suma"] / metricas_ticket["tickets_unicos"]
    ).fillna(0.0)
    metricas_ticket["pct_tickets_grandes"] = (
        metricas_ticket["tickets_grandes"] / metricas_ticket["tickets_unicos"]
    ).fillna(0.0)

    otras = (
        ventas.groupby(group_cols, dropna=False)
        .agg(
            dias_con_compra=("fecha_venta", "nunique"),
            ventas_totales=("venta_gs", "sum"),
        )
        .reset_index()
    )

    out = metricas_ticket.merge(otras, on=group_cols, how="left")
    columnas_finales = group_cols + [
        "tickets_unicos", "dias_con_compra",
        "upt", "pct_tickets_grandes", "ventas_totales",
    ]
    return out[columnas_finales]


# ============================================================
# 4. Clasificación por nivel mayorista
# ============================================================

def clasificar_nivel(metricas: pd.DataFrame, config: ConfiguracionAnalisis) -> pd.DataFrame:
    """
    Asigna puntaje (0-3, un punto por criterio) y nivel mayorista:
      C1 - UPT alto            : upt              >= Q3_upt del universo
      C2 - % tickets grandes   : pct_tickets_grandes >= max(pct_grandes_minimo, Q3_pct)
      C3 - Ventas totales alto : ventas_totales   >= Q3_ventas del universo

    Puntaje -> nivel: 3=Alto, 2=Medio, 1=Potencial, 0=Minorista.
    Los umbrales usados quedan guardados en el resultado (auditoría).
    """
    out = metricas.copy()

    q3_upt = out["upt"].quantile(0.75)
    q3_pct = out["pct_tickets_grandes"].quantile(0.75)
    q3_ventas = out["ventas_totales"].quantile(0.75)

    umbral_upt = q3_upt
    umbral_pct = max(config.pct_grandes_minimo, q3_pct)
    umbral_ventas = q3_ventas

    out["c1_upt_alto"] = out["upt"] >= umbral_upt
    out["c2_pct_grandes_alto"] = out["pct_tickets_grandes"] >= umbral_pct
    out["c3_ventas_alto"] = out["ventas_totales"] >= umbral_ventas

    out["puntaje"] = (
        out["c1_upt_alto"].astype(int)
        + out["c2_pct_grandes_alto"].astype(int)
        + out["c3_ventas_alto"].astype(int)
    )

    niveles_por_puntaje = {3: "Alto", 2: "Medio", 1: "Potencial", 0: "Minorista"}
    out["nivel_mayorista"] = pd.Categorical(
        out["puntaje"].map(niveles_por_puntaje),
        categories=NIVELES_ORDENADOS,
        ordered=True,
    )

    out["umbral_upt_usado"] = round(umbral_upt, 2)
    out["umbral_pct_usado"] = round(umbral_pct, 4)
    out["umbral_ventas_usado"] = round(umbral_ventas, 0)

    return out


# ============================================================
# 5. Pipeline completo: global + por sucursal
# ============================================================

def correr_analisis(ventas: pd.DataFrame, config: ConfiguracionAnalisis) -> pd.DataFrame:
    """
    Corre el análisis global y por cada sucursal, usando el mismo umbral
    de "ticket grande" (calculado una sola vez) en todos los ámbitos para
    que los resultados sean comparables entre sí.
    """
    logger.info("Calculando umbral de ticket grande sobre el universo completo...")
    umbral_tg = calcular_umbral_ticket_grande(ventas)

    resultados = []

    logger.info("Procesando: GLOBAL")
    metricas_global = calcular_metricas(ventas, ["cliente_id_limpio"], umbral_tg)
    clasificado_global = clasificar_nivel(metricas_global, config)
    clasificado_global.insert(0, "scope", "GLOBAL")
    clasificado_global["umbral_ticket_grande"] = round(umbral_tg, 1)
    resultados.append(clasificado_global)

    for pdv_id, nombre in config.nombre_sucursal.items():
        ventas_sucursal = ventas[ventas["pdv_id"] == pdv_id]
        if ventas_sucursal.empty:
            logger.warning("Sin datos para sucursal %s (pdv_id=%s)", nombre, pdv_id)
            continue
        logger.info("Procesando: %s", nombre)
        metricas_suc = calcular_metricas(ventas_sucursal, ["cliente_id_limpio"], umbral_tg)
        clasificado_suc = clasificar_nivel(metricas_suc, config)
        clasificado_suc.insert(0, "scope", nombre)
        clasificado_suc["umbral_ticket_grande"] = round(umbral_tg, 1)
        resultados.append(clasificado_suc)

    out = pd.concat(resultados, ignore_index=True)

    if "nombre_cliente" in ventas.columns:
        nombres = ventas[["cliente_id_limpio", "nombre_cliente"]].drop_duplicates(
            subset="cliente_id_limpio"
        )
        out = out.merge(nombres, on="cliente_id_limpio", how="left")

    columnas_primeras = [
        "scope", "cliente_id_limpio", "nombre_cliente",
        "nivel_mayorista", "puntaje",
        "c1_upt_alto", "c2_pct_grandes_alto", "c3_ventas_alto",
        "upt", "pct_tickets_grandes", "ventas_totales",
        "tickets_unicos", "dias_con_compra",
        "umbral_ticket_grande",
        "umbral_upt_usado", "umbral_pct_usado", "umbral_ventas_usado",
    ]
    columnas_primeras = [c for c in columnas_primeras if c in out.columns]
    out = out[columnas_primeras]

    return out.sort_values(
        ["scope", "nivel_mayorista", "ventas_totales"], ascending=[True, True, False]
    ).reset_index(drop=True)


def resumen_por_nivel(resultado: pd.DataFrame) -> pd.DataFrame:
    """Cantidad de clientes y ventas por (scope, nivel_mayorista) -- para reporte rápido."""
    resumen = (
        resultado.groupby(["scope", "nivel_mayorista"], observed=True)
        .agg(clientes=("cliente_id_limpio", "nunique"), ventas_gs=("ventas_totales", "sum"))
        .reset_index()
    )
    resumen["pct_clientes"] = (
        resumen.groupby("scope")["clientes"].transform(lambda x: x / x.sum() * 100).round(1)
    )
    return resumen


# ============================================================
# 6. Entrypoint
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exportar-csv", type=Path, default=None,
        help="Ruta donde exportar el resultado como CSV (opcional).",
    )
    parser.add_argument(
        "--escribir-sql", action="store_true",
        help=(
            "Escribe el resultado a dbo.clientes_mayoristas_v2 con swap "
            "atómico (ver conexion_bd.escribir_tabla_segura). Por default "
            "NO escribe -- dbt_ahorrazo/dim_clientes_mayoristas.sql es la "
            "fuente de verdad de producción; usar solo para comparar."
        ),
    )
    args = parser.parse_args()

    config = ConfiguracionAnalisis()

    ventas_crudas = cargar_ventas(config)
    ventas = limpiar_ventas(ventas_crudas, config)
    resultado = correr_analisis(ventas, config)

    logger.info("Total de registros (clientes x scope): %s", f"{len(resultado):,}")
    logger.info("\n%s", resumen_por_nivel(resultado).to_string(index=False))

    if args.exportar_csv:
        resultado.to_csv(args.exportar_csv, index=False, encoding="utf-8")
        logger.info("Exportado a %s", args.exportar_csv)

    if args.escribir_sql:
        engine = engine_sqlalchemy_bd()
        escribir_tabla_segura(
            resultado,
            "clientes_mayoristas_v2",
            engine,
            dtype={
                "cliente_id_limpio": NVARCHAR(length=255),
                "nombre_cliente": NVARCHAR(length=255),
                "nivel_mayorista": NVARCHAR(length=20),
                "puntaje": INTEGER(),
                "upt": FLOAT(precision=53),
                "pct_tickets_grandes": FLOAT(precision=53),
                "ventas_totales": FLOAT(precision=53),
            },
        )
        logger.info("Escrito en dbo.clientes_mayoristas_v2 (swap atómico)")


if __name__ == "__main__":
    main()
