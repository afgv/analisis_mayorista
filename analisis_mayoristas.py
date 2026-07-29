# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import pyodbc

# ============================================
# 0) Limpieza y helpers básicos
# ============================================
def _asegurar_tipos_clientes(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    # Tipos
    if "producto_id" in out.columns:
        out["producto_id"] = out["producto_id"].astype(str)
    if "pdv_id" in out.columns:
        out["pdv_id"] = out["pdv_id"].astype(str)
    if "cliente_id" in out.columns:
        out["cliente_id"] = out["cliente_id"].astype(str)
    # Fecha
    if not np.issubdtype(out["fecha_venta"].dtype, np.datetime64):
        out["fecha_venta"] = pd.to_datetime(out["fecha_venta"])
    return out

def _percentil_rank(s: pd.Series) -> pd.Series:
    """Percentil rank [0,1] robusto (maneja empates/NaN)."""
    s = s.astype(float)
    return s.rank(pct=True, method="average").fillna(0.0).clip(0, 1)

# Rangos para UNIDADES por ticket (por cliente)
BINS = [0, 2, 5, np.inf]
LABELS = ["1-2", "3-5", ">=6"]

# ============================================
# 1) Features por cliente (global o por sucursal)
# ============================================
def features_clientes(
    df: pd.DataFrame,
    group_cols=("cliente_id",),   # global por cliente; para sucursal usar ("pdv_id","cliente_id")
    excluir_unidades_no_positivas_en_rangos=True
) -> pd.DataFrame:
    """
    Calcula features por cliente (y por sucursal si pdv_id está en group_cols):
      - Tickets únicos, Unidades totales, Ventas totales, Días con compras
      - Unidades promedio por ticket, Unidades por día
      - Distribución de unidades por ticket: # y % en 1–2, 3–5, >=6
      - P95 de unidades por ticket (cola de volumen)
      - Diversidad SKU: #SKU, HHI (concentración), share del SKU top por unidades
    """

    tmp = _asegurar_tipos_clientes(df)

    # -----------------------------
    # Agregados simples por cliente
    # -----------------------------
    g = tmp.groupby(list(group_cols), dropna=False)

    tickets_unicos  = g["ticket_id"].nunique().rename("tickets_unicos")
    unidades_tot    = g["unidades"].sum(min_count=1).rename("unidades_totales")
    ventas_tot      = g["venta_gs"].sum(min_count=1).rename("ventas_totales")
    dias_con_compra = g["fecha_venta"].nunique().rename("dias_con_compra")

    out = pd.concat(
        [tickets_unicos, unidades_tot, ventas_tot, dias_con_compra],
        axis=1
    ).reset_index()

    # Derivados
    out["unidades_por_ticket"] = (
        out["unidades_totales"] / out["tickets_unicos"].replace({0: pd.NA})
    ).fillna(0.0)
    out["unidades_por_dia"] = (
        out["unidades_totales"] / out["dias_con_compra"].replace({0: pd.NA})
    ).fillna(0.0)

    # ----------------------------------------
    # Distribución de unidades por ticket (UPT)
    # ----------------------------------------
    # Sumar UNIDADES por (group_cols + ticket) para construir la distribución de cada cliente
    upt = (
        tmp.groupby(list(group_cols) + ["ticket_id"], dropna=False)["unidades"]
           .sum(min_count=1)
           .rename("upt")  # unidades del cliente en ese ticket
           .reset_index()
    )

    if excluir_unidades_no_positivas_en_rangos:
        # Excluir devoluciones o tickets sin unidades en los rangos (pero cuentan para totales generales si quisieras)
        upt_rangos = upt.loc[upt["upt"] > 0].copy()
    else:
        upt_rangos = upt.copy()

    upt_rangos["rango_upt"] = pd.cut(
        upt_rangos["upt"].astype(float),
        bins=BINS,                # ej.: [0, 2, 5, np.inf]
        labels=LABELS,            # ej.: ["1-2","3-5",">=6"]
        right=True,
        include_lowest=False
    )

    # Conteos por rango (solo tickets con rango válido)
    counts_rangos = (
        upt_rangos.dropna(subset=["rango_upt"])
                  .groupby(list(group_cols) + ["rango_upt"])
                  .size()
                  .unstack(fill_value=0)
                  .reset_index()
    )

    # Asegurar columnas de rangos
    for c in LABELS:
        if c not in counts_rangos.columns:
            counts_rangos[c] = 0

    # Renombrar columnas a nombres finales
    counts_rangos = counts_rangos.rename(columns={
        "1-2": "tickets_1_2",
        "3-5": "tickets_3_5",
        ">=6": "tickets_ge_6",
    })

    # Total de tickets por cliente (incluye 0/negativos si existían)
    totals = (
        upt.groupby(list(group_cols), dropna=False)
           .size()
           .rename("tickets_total_upt")
           .reset_index()
    )

    # Alinear por claves: merge (NO asignar .values)
    counts = totals.merge(counts_rangos, on=list(group_cols), how="left")

    # Rellenar 0s en conteos de rangos
    for c in ["tickets_1_2", "tickets_3_5", "tickets_ge_6"]:
        if c not in counts.columns:
            counts[c] = 0
    counts[["tickets_1_2", "tickets_3_5", "tickets_ge_6"]] = (
        counts[["tickets_1_2", "tickets_3_5", "tickets_ge_6"]]
        .fillna(0)
        .astype(int)
    )

    # Porcentajes (maneja división por 0)
    counts["pct_tickets_1_2"] = (
        counts["tickets_1_2"] / counts["tickets_total_upt"].replace({0: pd.NA})
    ).fillna(0.0)
    counts["pct_tickets_3_5"] = (
        counts["tickets_3_5"] / counts["tickets_total_upt"].replace({0: pd.NA})
    ).fillna(0.0)
    counts["pct_tickets_ge_6"] = (
        counts["tickets_ge_6"] / counts["tickets_total_upt"].replace({0: pd.NA})
    ).fillna(0.0)

    # ----------------------------------------
    # P95 de UPT (cola de volumen por cliente)
    # ----------------------------------------
    p95 = (
        upt.groupby(list(group_cols), dropna=False)["upt"]
           .quantile(0.95)
           .rename("upt_p95")
           .reset_index()
    )

    # ----------------------------------------
    # Diversidad y concentración de SKU (por unidades)
    # ----------------------------------------
    sku_units = (
        tmp.groupby(list(group_cols) + ["producto_id"], dropna=False)["unidades"]
           .sum(min_count=1)
           .rename("unidades_sku")
           .reset_index()
    )

    # Total por cliente
    total_por_cliente = (
        sku_units.groupby(list(group_cols), dropna=False)["unidades_sku"]
                 .sum(min_count=1)
                 .rename("unidades_cliente")
                 .reset_index()
    )

    # Share por SKU
    sku_units = sku_units.merge(total_por_cliente, on=list(group_cols), how="left")
    sku_units["share_sku"] = (
        sku_units["unidades_sku"] / sku_units["unidades_cliente"].replace({0: pd.NA})
    ).fillna(0.0)

    # HHI y top share
    hhi = (
        sku_units.groupby(list(group_cols), dropna=False)["share_sku"]
                 .apply(lambda s: np.square(s.astype(float)).sum())
                 .rename("hhi_sku")
                 .reset_index()
    )
    top_share = (
        sku_units.groupby(list(group_cols), dropna=False)["share_sku"]
                 .max()
                 .rename("top_share_sku")
                 .reset_index()
    )
    n_skus = (
        sku_units.groupby(list(group_cols), dropna=False)["producto_id"]
                 .nunique()
                 .rename("n_skus")
                 .reset_index()
    )

    # ----------------------------------------
    # Ensamble final de features
    # ----------------------------------------
    out = (
        out
        .merge(counts, on=list(group_cols), how="left")
        .merge(p95, on=list(group_cols), how="left")
        .merge(hhi, on=list(group_cols), how="left")
        .merge(top_share, on=list(group_cols), how="left")
        .merge(n_skus, on=list(group_cols), how="left")
        .fillna(0.0)
    )

    return out

# ============================================
# 2) Reglas + Score continuo (enfoque Unidades)
# ============================================
def etiquetar_mayoristas(features: pd.DataFrame, group_cols=("cliente_id",), umbral_score=70.0) -> pd.DataFrame:
    """
    Crea:
      - mayorista_score (0-100): énfasis en UNIDADES
      - heuristicas_flag: regla basada en límites dinámicos
      - mayorista_reglas: booleano final por reglas/score (sin ML)
    """

    out = features.copy()

    # Percentil-rank [0,1] para features clave (énfasis unidades)
    pr_upt_mean   = _percentil_rank(out["unidades_por_ticket"])
    pr_p95        = _percentil_rank(out["upt_p95"])
    pr_pct_ge6    = _percentil_rank(out["pct_tickets_ge_6"])
    pr_pct_3_5    = _percentil_rank(out["pct_tickets_3_5"])
    pr_u_per_day  = _percentil_rank(out["unidades_por_dia"])
    pr_units_tot  = _percentil_rank(out["unidades_totales"])

    # Score 0-100 (pesos centrados en unidades)
    score = (
        0.35 * pr_upt_mean +
        0.20 * pr_pct_ge6 +
        0.10 * pr_pct_3_5 +
        0.20 * pr_u_per_day +
        0.10 * pr_p95 +
        0.05 * pr_units_tot
    ) * 100.0

    out["mayorista_score"] = score

    # Heurística dinámica usando cuantiles globales
    q_upt_mean  = out["unidades_por_ticket"].quantile(0.75)
    q_pct_ge6   = out["pct_tickets_ge_6"].quantile(0.75)
    q_u_per_day = out["unidades_por_dia"].quantile(0.75)

    regla_alta_upt    = out["unidades_por_ticket"] >= max(4.0, q_upt_mean)   # mínimo absoluto + umbral relativo
    regla_alta_pct6   = out["pct_tickets_ge_6"]   >= max(0.30, q_pct_ge6)
    regla_alta_u_dia  = out["unidades_por_dia"]   >= q_u_per_day

    out["heuristicas_flag"] = (regla_alta_upt & (regla_alta_pct6 | regla_alta_u_dia)) | (out["mayorista_score"] >= umbral_score)
    out["mayorista_reglas"] = out["heuristicas_flag"]

    return out


# ============================================
# 3) (Opcional) ML no supervisado
# ============================================
def ml_no_supervisado_mayoristas(features: pd.DataFrame) -> pd.DataFrame:
    """
    Intenta IsolationForest + KMeans. Si sklearn no está disponible, devuelve columnas vacías.
    """
    out = features.copy()
    try:
        from sklearn.preprocessing import StandardScaler
        from sklearn.ensemble import IsolationForest
        from sklearn.cluster import KMeans

        X = out[[
            "unidades_por_ticket",
            "pct_tickets_ge_6",
            "pct_tickets_3_5",
            "unidades_por_dia",
            "upt_p95",
            "unidades_totales",
            "tickets_unicos"
        ]].astype(float).fillna(0.0).values

        scaler = StandardScaler()
        Xs = scaler.fit_transform(X)

        # Isolation Forest (anomalías "altas")
        iso = IsolationForest(
            n_estimators=300,
            contamination="auto",
            random_state=42
        ).fit(Xs)
        scores_iso = -iso.score_samples(Xs)  # mayor = más anómalo
        out["iforest_score"] = (scores_iso - scores_iso.min()) / (scores_iso.max() - scores_iso.min() + 1e-9) * 100.0
        out["iforest_flag"]  = out["iforest_score"] >= out["iforest_score"].quantile(0.90)  # top 10% como outliers “altos”

        # KMeans (2 clusters: bajo/alto volumen)
        km = KMeans(n_clusters=2, random_state=42, n_init=10).fit(Xs)
        out["kmeans_cluster"] = km.labels_

        # Etiquetar cluster mayorista: el que tenga mayor media en unidades_por_ticket
        cluster_stats = out.groupby("kmeans_cluster")["unidades_por_ticket"].mean()
        cluster_mayorista = int(cluster_stats.idxmax())
        out["kmeans_flag"] = (out["kmeans_cluster"] == cluster_mayorista)

    except Exception:
        # Si no hay sklearn o falla, devolvemos columnas nulas
        out["iforest_score"] = np.nan
        out["iforest_flag"]  = False
        out["kmeans_cluster"] = -1
        out["kmeans_flag"] = False

    return out


# ============================================
# 4) Ensamblado final: GLOBAL y/o POR SUCURSAL
# ============================================
def clasificar_mayoristas(
    ventas_df: pd.DataFrame,
    por_sucursal: bool = True,          # calcula también por ("pdv_id","cliente_id")
    incluir_global: bool = True,        # calcula también por ("cliente_id")
    umbral_score: float = 70.0
) -> pd.DataFrame:
    """
    Devuelve un DataFrame largo con una columna 'scope_pdv_id' que vale:
      - 'GLOBAL' para el análisis global por cliente
      - el código de sucursal para el análisis por sucursal

    Incluye features, score, flags heurísticos y (si sklearn presente) flags de ML.
    """
    df = _asegurar_tipos_clientes(ventas_df)

    resultados = []

    if incluir_global:
        feats_g = features_clientes(df, group_cols=("cliente_id",))
        lab_g   = etiquetar_mayoristas(feats_g, group_cols=("cliente_id",), umbral_score=umbral_score)
        lab_g   = ml_no_supervisado_mayoristas(lab_g)
        lab_g.insert(0, "scope_pdv_id", "GLOBAL")
        resultados.append(lab_g)

    if por_sucursal and "pdv_id" in df.columns:
        feats_p = features_clientes(df, group_cols=("pdv_id","cliente_id"))
        lab_p   = etiquetar_mayoristas(feats_p, group_cols=("pdv_id","cliente_id"), umbral_score=umbral_score)
        lab_p   = ml_no_supervisado_mayoristas(lab_p)
        # Renombrar pdv_id a scope y mantener columna explícita
        lab_p = lab_p.rename(columns={"pdv_id": "scope_pdv_id"})
        resultados.append(lab_p)

    if not resultados:
        return pd.DataFrame()

    out = pd.concat(resultados, ignore_index=True)

    # Score combinado (opcional): sube si coincide ML
    bonus = (out["iforest_flag"].astype(int) + out["kmeans_flag"].astype(int)) * 5.0
    out["mayorista_score_ajuste_ml"] = (out["mayorista_score"] + bonus).clip(0, 100)

    # Etiqueta final (conservadora): reglas o ambos ML
    out["mayorista_final"] = (
        out["mayorista_reglas"] |
        (out["iforest_flag"] & out["kmeans_flag"]) |
        (out["mayorista_score_ajuste_ml"] >= umbral_score)
    )

    # Orden amigable
    col_orden = [
        "scope_pdv_id", "cliente_id",
        # Señales/etiquetas
        "mayorista_final", "mayorista_reglas",
        "mayorista_score", "mayorista_score_ajuste_ml",
        "iforest_flag", "kmeans_flag", "iforest_score", "kmeans_cluster",
        # Métricas base
        "tickets_unicos", "unidades_totales", "ventas_totales", "dias_con_compra",
        "unidades_por_ticket", "unidades_por_dia", "upt_p95",
        # Rangos de UPT
        "tickets_1_2", "tickets_3_5", "tickets_ge_6", "tickets_total_upt",
        "pct_tickets_1_2", "pct_tickets_3_5", "pct_tickets_ge_6",
        # Mix de SKU
        "n_skus", "hhi_sku", "top_share_sku",
    ]
    col_orden = [c for c in col_orden if c in out.columns]
    out = out[col_orden + [c for c in out.columns if c not in col_orden]]

    return out

# --- conexión (usá la tuya si ya funciona) ---
def conexion_bd():
    # Detecta driver 18/17 o cambialo por el que tengas
    drivers = pyodbc.drivers()
    preferidos = ["ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server"]
    driver = next((d for d in preferidos if d in drivers), None)
    if not driver:
        raise RuntimeError(f"No se encontró driver ODBC SQL Server. Detectados: {drivers}")
    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER=192.168.5.231;"
        f"DATABASE=DATO_SOLUTIONS;"
        f"UID=sa;"
        f"PWD=Server_123456;"
        f"Encrypt=yes;TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)

# --- rango sargable para el año 2025 ---
START_2025 = "2025-01-01"
END_2026   = "2026-01-01"

# --- SOLO lo necesario desde la BD; se construye ticket_id en SQL ---
SQL_2025 = """
SELECT
    fecha_venta,
    cliente_id,
    CAST(producto_id AS varchar(100)) AS producto_id,
    CAST(pdv_id      AS varchar(50))  AS pdv_id,
    CONCAT(CAST(timbrado AS varchar(50)), '_', CAST(factura_nro AS varchar(50))) AS ticket_id,
    unidades,
    venta_gs
FROM dbo.Ventas_Ahorrazo
WHERE fecha_venta >= ? AND fecha_venta < ?;
"""

with conexion_bd() as conn:
    ventas_2025 = pd.read_sql_query(SQL_2025, conn, params=[START_2025, END_2026])

ventas_2025.head()

excluir_producto_id = ["442", "999994","31852", "999992", "90311", "999998", "901762", "9809999", "51457", "77190", "8925", "91988", "999995"]

ventas_2025_sin_bolsa = ventas_2025[~ventas_2025.producto_id.isin(excluir_producto_id)]

# ============================================
# 5) Ejemplo de uso con tu dataframe ventas_2025
# ============================================
# ventas_2025 = ...  # ya cargado según tu SQL (incluye: fecha_venta, cliente_id, producto_id, pdv_id, ticket_id, unidades, venta_gs)

# A) Solo GLOBAL
# clientes_global = clasificar_mayoristas(ventas_2025, por_sucursal=False, incluir_global=True, umbral_score=70)

# B) Solo POR SUCURSAL
# clientes_por_sucursal = clasificar_mayoristas(ventas_2025, por_sucursal=True, incluir_global=False, umbral_score=70)

# C) GLOBAL + POR SUCURSAL (recomendado)
clientes_all = clasificar_mayoristas(ventas_2025_sin_bolsa, por_sucursal=True, incluir_global=True, umbral_score=70)
clientes_all.head()
clientes_all.to_csv("clientes_mayoristas_2025.csv", index=False, encoding="utf-8")
