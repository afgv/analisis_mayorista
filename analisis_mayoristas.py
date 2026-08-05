"""Identificación de clientes mayoristas -- metodología v1 (score ML).

Combina un score continuo (percentil-rank ponderado sobre 5 señales de
volumen/gasto) con IsolationForest + KMeans no supervisados. Agrupaba bien
en la práctica, pero es más difícil de explicar a negocio que la metodología
de reglas simples (v2, ver `analisis_mayoristas_v2.py`).

Estado: v2 ya está validada como metodología canónica y migrada a SQL vía
dbt (`dbt_ahorrazo/models/marts/mayoristas/`), que es la fuente de verdad
de producción. Este script (v1) se conserva ACTIVO pero como variante
archivada/histórica -- sirve de evidencia comparativa, no se lo reemplaza
ni se lo borra. No está pensado para migrarse a dbt (usa IsolationForest/
KMeans de scikit-learn, sin equivalente directo en SQL Server).

Este archivo es la versión script de `analisis_mayoristas_usado.ipynb` (el
notebook que corre hoy en producción). La versión anterior de este archivo
NO reflejaba esa lógica y estaba desactualizada -- entre otras diferencias,
agrupaba por `cliente_id` crudo en vez de `cliente_id_limpio` (perdiendo la
deduplicación de RUC/cédula del mismo cliente), usaba otros rangos de
unidades por ticket, y no escribía a ninguna tabla. Se reescribió para que
sea un espejo fiel y ejecutable de "usado" sin un notebook de por medio.

Inconsistencias encontradas en el notebook original, resueltas acá
portando el comportamiento REAL (el código), no los comentarios/valores
sueltos que quedaron desactualizados:
  - El docstring de `etiquetar_mayoristas()` describía una fórmula de
    score con pesos 0.35/0.20/0.20/0.15/0.10 sobre
    (pct_ge_1000, upt_mean, unidades_por_dia, pct_101_999, unidades_tot);
    el código real usa 0.30/0.25/0.20/0.15/0.10 sobre
    (pct_ge_1000, upt_mean, VENTAS_TOTALES, pct_101_999, unidades_tot).
    Se porta la fórmula real.
  - La corrida final (la que efectivamente se escribe a SQL) usa
    `umbral_score=75`, no el default de la función (70.0) ni el 0.75
    usado en una celda de prueba manual intermedia (dos órdenes de
    magnitud distinto del resto -- el score es 0-100, no 0-1). Se porta
    75, el valor que de verdad se usó para producir la tabla vigente.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sqlalchemy.dialects.mssql import BIT, FLOAT, INTEGER, NVARCHAR

from conexion_bd import conexion_bd, engine_sqlalchemy_bd, escribir_tabla_segura

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ============================================================
# Configuración
# ============================================================

@dataclass(frozen=True)
class ConfiguracionAnalisis:
    fecha_inicio: str = "2025-01-01"
    fecha_fin: str = "2026-01-01"  # exclusivo

    excluir_clientes: tuple[str, ...] = ("44444401-7",)
    excluir_productos: tuple[str, ...] = (
        "442", "999994", "31852", "999992", "90311",
        "999998", "901762", "9809999", "51457", "77190",
        "8925", "91988", "999995",
    )

    # Umbral del score combinado (0-100) que se usó para producir la
    # tabla vigente hoy en dbo.clientes_mayoristas -- ver nota al
    # principio del archivo sobre por qué es 75 y no el default (70).
    umbral_score: float = 75.0


# Rangos de unidades por ticket para el histograma de comportamiento.
BINS = [0, 10, 100, 1000, np.inf]
LABELS = ["1-10", "11-100", "101-999", ">=1000"]

NOMBRE_SUCURSAL = {
    "3": "Ahorrazo SL",
    "5": "Ahorrazo R1",
    "6": "Ahorrazo R2",
}


# ============================================================
# Helpers
# ============================================================

def _asegurar_tipos(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ("producto_id", "pdv_id", "cliente_id", "cliente_id_limpio"):
        if col in out.columns:
            out[col] = out[col].astype(str)
    if not np.issubdtype(out["fecha_venta"].dtype, np.datetime64):
        out["fecha_venta"] = pd.to_datetime(out["fecha_venta"])
    return out


def _percentil_rank(s: pd.Series) -> pd.Series:
    """Percentil-rank en [0,1], robusto a empates/NaN."""
    return s.astype(float).rank(pct=True, method="average").fillna(0.0).clip(0, 1)


def nombre_sucursal(pdv_id: str) -> str:
    return NOMBRE_SUCURSAL.get(pdv_id, "General")


# ============================================================
# 1. Carga y limpieza
# ============================================================

def cargar_ventas(config: ConfiguracionAnalisis) -> pd.DataFrame:
    sql = "SELECT * FROM dbo.view_ventas_ahorrazo_filtradas_12m;"
    with conexion_bd() as conn:
        df = pd.read_sql_query(
            sql, conn, params=[config.fecha_inicio, config.fecha_fin]
        )
    logger.info("Filas cargadas: %s", f"{len(df):,}")
    return df


def limpiar_ventas(df: pd.DataFrame, config: ConfiguracionAnalisis) -> pd.DataFrame:
    ventas = _asegurar_tipos(df)
    ventas = ventas[~ventas["cliente_id"].isin(config.excluir_clientes)]
    ventas = ventas[~ventas["producto_id"].isin(config.excluir_productos)]
    logger.info("Filas después de limpieza: %s", f"{len(ventas):,}")
    return ventas


# ============================================================
# 2. Features por cliente
# ============================================================

def features_clientes(df: pd.DataFrame, group_cols: tuple[str, ...]) -> pd.DataFrame:
    """
    Features por cliente (o cliente x sucursal si `pdv_id` está en group_cols):
      - tickets únicos, unidades totales, ventas totales, días con compra
      - unidades promedio por ticket y por día
      - distribución de unidades por ticket (histograma 1-10/11-100/101-999/>=1000)
      - p95 de unidades por ticket
      - diversidad de SKU: cantidad, HHI (concentración), share del SKU top
    """
    tmp = _asegurar_tipos(df)
    cols = list(group_cols)
    g = tmp.groupby(cols, dropna=False)

    base = pd.concat(
        [
            g["ticket_id"].nunique().rename("tickets_unicos"),
            g["unidades"].sum(min_count=1).rename("unidades_totales"),
            g["venta_gs"].sum(min_count=1).rename("ventas_totales"),
            g["fecha_venta"].nunique().rename("dias_con_compra"),
        ],
        axis=1,
    ).reset_index()

    base["unidades_por_ticket"] = (
        base["unidades_totales"] / base["tickets_unicos"].replace({0: pd.NA})
    ).fillna(0.0)
    base["unidades_por_dia"] = (
        base["unidades_totales"] / base["dias_con_compra"].replace({0: pd.NA})
    ).fillna(0.0)

    # -- Distribución de unidades por ticket --
    upt = (
        tmp.groupby(cols + ["ticket_id"], dropna=False)["unidades"]
        .sum(min_count=1)
        .rename("upt")
        .reset_index()
    )
    upt_positivo = upt[upt["upt"] > 0].copy()  # excluye devoluciones del histograma
    upt_positivo["rango"] = pd.cut(
        upt_positivo["upt"].astype(float), bins=BINS, labels=LABELS,
        right=True, include_lowest=False,
    )

    conteos = (
        upt_positivo.dropna(subset=["rango"])
        .groupby(cols + ["rango"], observed=True)
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )
    for etiqueta in LABELS:
        if etiqueta not in conteos.columns:
            conteos[etiqueta] = 0
    conteos = conteos.rename(columns={
        "1-10": "tickets_1_10",
        "11-100": "tickets_11_100",
        "101-999": "tickets_101_999",
        ">=1000": "tickets_ge_1000",
    })

    totales_upt = upt.groupby(cols, dropna=False).size().rename("tickets_total_upt").reset_index()
    conteos = totales_upt.merge(conteos, on=cols, how="left")
    columnas_rango = ["tickets_1_10", "tickets_11_100", "tickets_101_999", "tickets_ge_1000"]
    conteos[columnas_rango] = conteos[columnas_rango].fillna(0).astype(int)
    for col in columnas_rango:
        conteos[f"pct_{col}"] = (
            conteos[col] / conteos["tickets_total_upt"].replace({0: pd.NA})
        ).fillna(0.0)

    p95 = upt.groupby(cols, dropna=False)["upt"].quantile(0.95).rename("upt_p95").reset_index()

    # -- Diversidad y concentración de SKU (por unidades) --
    unidades_sku = (
        tmp.groupby(cols + ["producto_id"], dropna=False)["unidades"]
        .sum(min_count=1)
        .rename("unidades_sku")
        .reset_index()
    )
    total_cliente = (
        unidades_sku.groupby(cols, dropna=False)["unidades_sku"]
        .sum(min_count=1)
        .rename("unidades_cliente")
        .reset_index()
    )
    unidades_sku = unidades_sku.merge(total_cliente, on=cols, how="left")
    unidades_sku["share_sku"] = (
        unidades_sku["unidades_sku"] / unidades_sku["unidades_cliente"].replace({0: pd.NA})
    ).fillna(0.0)

    hhi = (
        unidades_sku.groupby(cols, dropna=False)["share_sku"]
        .apply(lambda s: np.square(s.astype(float)).sum())
        .rename("hhi_sku")
        .reset_index()
    )
    top_share = unidades_sku.groupby(cols, dropna=False)["share_sku"].max().rename("top_share_sku").reset_index()
    n_skus = unidades_sku.groupby(cols, dropna=False)["producto_id"].nunique().rename("n_skus").reset_index()

    out = (
        base.merge(conteos, on=cols, how="left")
        .merge(p95, on=cols, how="left")
        .merge(hhi, on=cols, how="left")
        .merge(top_share, on=cols, how="left")
        .merge(n_skus, on=cols, how="left")
        .fillna(0.0)
    )
    return out


# ============================================================
# 3. Score + heurística por reglas
# ============================================================

def etiquetar_mayoristas(features: pd.DataFrame, umbral_score: float) -> pd.DataFrame:
    """
    mayorista_score (0-100): percentil-rank ponderado, énfasis en la cola
    alta de unidades por ticket:

        score = 100 * (
            0.30 * pr(pct_tickets_ge_1000) +
            0.25 * pr(unidades_por_ticket) +
            0.20 * pr(ventas_totales) +
            0.15 * pr(pct_tickets_101_999) +
            0.10 * pr(unidades_totales)
        )

    heuristicas_flag: regla booleana independiente del score, con pisos
    mínimos absolutos para estabilidad en universos chicos.
    """
    out = features.copy()

    pr_pct_ge1000 = _percentil_rank(out["pct_tickets_ge_1000"])
    pr_upt_mean = _percentil_rank(out["unidades_por_ticket"])
    pr_ventas_tot = _percentil_rank(out["ventas_totales"])
    pr_pct_101_999 = _percentil_rank(out["pct_tickets_101_999"])
    pr_units_tot = _percentil_rank(out["unidades_totales"])

    out["mayorista_score"] = 100.0 * (
        0.30 * pr_pct_ge1000
        + 0.25 * pr_upt_mean
        + 0.20 * pr_ventas_tot
        + 0.15 * pr_pct_101_999
        + 0.10 * pr_units_tot
    )

    q_upt_mean = out["unidades_por_ticket"].quantile(0.75)
    q_ventas_totales = out["ventas_totales"].quantile(0.75)

    regla_alta_upt = out["unidades_por_ticket"] >= max(10.0, q_upt_mean)
    regla_alta_ventas_tot = out["ventas_totales"] >= q_ventas_totales
    regla_pct_ge1000 = out["pct_tickets_ge_1000"] >= 0.25

    out["heuristicas_flag"] = (
        (regla_alta_upt & regla_alta_ventas_tot)
        | regla_pct_ge1000
        | (out["mayorista_score"] >= umbral_score)
    )
    out["mayorista_reglas"] = out["heuristicas_flag"]
    return out


# ============================================================
# 4. ML no supervisado (opcional, degrada con gracia sin sklearn)
# ============================================================

_COLUMNAS_ML = [
    "unidades_por_ticket",
    "pct_tickets_ge_1000",
    "pct_tickets_101_999",
    "ventas_totales",
    "unidades_totales",
]


def ml_no_supervisado_mayoristas(features: pd.DataFrame) -> pd.DataFrame:
    """IsolationForest (outliers "altos") + KMeans (2 clusters: bajo/alto volumen)."""
    out = features.copy()
    try:
        x = out[_COLUMNAS_ML].astype(float).fillna(0.0).values
        x_escalado = StandardScaler().fit_transform(x)

        iso = IsolationForest(n_estimators=300, contamination="auto", random_state=42).fit(x_escalado)
        scores_iso = -iso.score_samples(x_escalado)  # mayor = más anómalo
        rango = scores_iso.max() - scores_iso.min() + 1e-9
        out["iforest_score"] = (scores_iso - scores_iso.min()) / rango * 100.0
        out["iforest_flag"] = out["iforest_score"] >= out["iforest_score"].quantile(0.90)

        km = KMeans(n_clusters=2, random_state=42, n_init=10).fit(x_escalado)
        out["kmeans_cluster"] = km.labels_
        cluster_mayorista = int(
            out.groupby("kmeans_cluster")["unidades_por_ticket"].mean().idxmax()
        )
        out["kmeans_flag"] = out["kmeans_cluster"] == cluster_mayorista

    except Exception:
        logger.warning("ML no supervisado no disponible/falló -- se continúa sin esas señales.", exc_info=True)
        out["iforest_score"] = np.nan
        out["iforest_flag"] = False
        out["kmeans_cluster"] = -1
        out["kmeans_flag"] = False

    return out


# ============================================================
# 5. Ensamblado: global + por sucursal
# ============================================================

def clasificar_mayoristas(
    ventas: pd.DataFrame,
    umbral_score: float,
    por_sucursal: bool = True,
    incluir_global: bool = True,
) -> pd.DataFrame:
    """
    Corre features + etiquetado + ML para cada ámbito solicitado y devuelve
    un único DataFrame largo con columna `scope_pdv_id` ('GLOBAL' o el
    pdv_id de la sucursal).
    """
    df = _asegurar_tipos(ventas)
    resultados = []

    if incluir_global:
        feats = features_clientes(df, ("cliente_id_limpio",))
        etiquetado = etiquetar_mayoristas(feats, umbral_score)
        etiquetado = ml_no_supervisado_mayoristas(etiquetado)
        etiquetado.insert(0, "scope_pdv_id", "GLOBAL")
        resultados.append(etiquetado)

    if por_sucursal and "pdv_id" in df.columns:
        feats = features_clientes(df, ("pdv_id", "cliente_id_limpio"))
        etiquetado = etiquetar_mayoristas(feats, umbral_score)
        etiquetado = ml_no_supervisado_mayoristas(etiquetado)
        etiquetado = etiquetado.rename(columns={"pdv_id": "scope_pdv_id"})
        resultados.append(etiquetado)

    if not resultados:
        return pd.DataFrame()

    out = pd.concat(resultados, ignore_index=True)

    bonus_ml = (out["iforest_flag"].astype(int) + out["kmeans_flag"].astype(int)) * 5.0
    out["mayorista_score_ajuste_ml"] = (out["mayorista_score"] + bonus_ml).clip(0, 100)
    out["mayorista_final"] = (
        out["mayorista_reglas"]
        | (out["iforest_flag"] & out["kmeans_flag"])
        | (out["mayorista_score_ajuste_ml"] >= umbral_score)
    )

    columnas_orden = [
        "scope_pdv_id", "cliente_id", "cliente_id_limpio",
        "mayorista_final", "mayorista_reglas",
        "mayorista_score", "mayorista_score_ajuste_ml",
        "iforest_flag", "kmeans_flag", "iforest_score", "kmeans_cluster",
        "tickets_unicos", "unidades_totales", "ventas_totales", "dias_con_compra",
        "unidades_por_ticket", "unidades_por_dia", "upt_p95",
        "tickets_1_10", "tickets_11_100", "tickets_101_999", "tickets_ge_1000", "tickets_total_upt",
        "pct_tickets_1_10", "pct_tickets_11_100", "pct_tickets_101_999", "pct_tickets_ge_1000",
        "n_skus", "hhi_sku", "top_share_sku",
    ]
    columnas_orden = [c for c in columnas_orden if c in out.columns]
    return out[columnas_orden + [c for c in out.columns if c not in columnas_orden]]


def enriquecer_con_datos_cliente(clasificados: pd.DataFrame) -> pd.DataFrame:
    """Suma nombre/ci_ruc (dbo.clientes_limpio) y el nombre legible de sucursal."""
    with conexion_bd() as conn:
        clientes_limpio = pd.read_sql("SELECT * FROM dbo.clientes_limpio", conn)
    clientes_limpio = clientes_limpio.rename(columns={"nombre": "Nombre"})

    out = clasificados.merge(clientes_limpio, on="cliente_id_limpio", how="left")
    out["pdv_nombre"] = out["scope_pdv_id"].apply(nombre_sucursal)
    return out


# ============================================================
# 6. Entrypoint
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exportar-csv", type=Path, default=None)
    parser.add_argument(
        "--escribir-sql", action="store_true",
        help="Escribe a dbo.clientes_mayoristas con swap atómico (tabla vigente en producción).",
    )
    args = parser.parse_args()

    config = ConfiguracionAnalisis()

    ventas_crudas = cargar_ventas(config)
    ventas = limpiar_ventas(ventas_crudas, config)

    clasificados = clasificar_mayoristas(
        ventas, umbral_score=config.umbral_score, por_sucursal=True, incluir_global=True
    )
    logger.info("Total de registros (clientes x scope): %s", f"{len(clasificados):,}")

    resultado = enriquecer_con_datos_cliente(clasificados)

    if args.exportar_csv:
        resultado.to_csv(args.exportar_csv, index=False, encoding="utf-8")
        logger.info("Exportado a %s", args.exportar_csv)

    if args.escribir_sql:
        engine = engine_sqlalchemy_bd()
        escribir_tabla_segura(
            resultado,
            "clientes_mayoristas",
            engine,
            dtype={
                "scope_pdv_id": NVARCHAR(length=50),
                "cliente_id": NVARCHAR(length=255),
                "cliente_id_limpio": NVARCHAR(length=255),
                "mayorista_final": BIT(),
                "mayorista_reglas": BIT(),
                "mayorista_score": FLOAT(precision=53),
                "mayorista_score_ajuste_ml": FLOAT(precision=53),
                "ventas_totales": FLOAT(precision=53),
                "Nombre": NVARCHAR(length=255),
                "ci_ruc": NVARCHAR(length=255),
                "pdv_nombre": NVARCHAR(length=50),
            },
        )
        logger.info("Escrito en dbo.clientes_mayoristas (swap atómico)")


if __name__ == "__main__":
    main()
