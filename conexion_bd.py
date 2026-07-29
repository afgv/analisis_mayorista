import os
from pathlib import Path
from typing import Optional

import pyodbc
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

load_dotenv(dotenv_path=ENV_PATH)


def obtener_variable(nombre: str, valor_default: Optional[str] = None) -> str:
    """
    Obtiene una variable de entorno.

    Si la variable no existe y no tiene valor por defecto,
    genera un error descriptivo.
    """
    valor = os.getenv(nombre, valor_default)

    if valor is None or not valor.strip():
        raise RuntimeError(
            f"No se encontró la variable requerida '{nombre}' "
            f"en el archivo {ENV_PATH}"
        )

    return valor.strip()


def detectar_driver_sql_server() -> str:
    """
    Detecta automáticamente el mejor driver ODBC disponible
    para conectarse a SQL Server.
    """
    drivers_disponibles = pyodbc.drivers()

    drivers_preferidos = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]

    driver = next(
        (
            driver_preferido
            for driver_preferido in drivers_preferidos
            if driver_preferido in drivers_disponibles
        ),
        None,
    )

    if driver is None:
        raise RuntimeError(
            "No se encontró un driver ODBC compatible con SQL Server. "
            f"Drivers detectados: {drivers_disponibles}"
        )

    return driver


def conexion_bd() -> pyodbc.Connection:
    """
    Abre y devuelve una conexión a SQL Server usando variables
    cargadas desde el archivo .env.
    """
    driver = detectar_driver_sql_server()

    server = obtener_variable("DB_SERVER")
    database = obtener_variable("DB_DATABASE")
    user = obtener_variable("DB_USER")
    password = obtener_variable("DB_PASSWORD")

    encrypt = obtener_variable("DB_ENCRYPT", "yes")
    trust_server_certificate = obtener_variable(
        "DB_TRUST_SERVER_CERTIFICATE",
        "yes",
    )

    timeout = int(
        obtener_variable(
            "DB_CONNECTION_TIMEOUT",
            "15",
        )
    )

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={server};"
        f"DATABASE={database};"
        f"UID={user};"
        f"PWD={password};"
        f"Encrypt={encrypt};"
        f"TrustServerCertificate={trust_server_certificate};"
    )

    return pyodbc.connect(
        conn_str,
        timeout=timeout,
    )


def probar_conexion() -> None:
    """
    Prueba la conexión e imprime información básica del servidor.
    """
    with conexion_bd() as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                DB_NAME() AS base_datos,
                @@SERVERNAME AS servidor,
                GETDATE() AS fecha_servidor
            """
        )

        base_datos, servidor, fecha_servidor = cursor.fetchone()

        print("Conexión exitosa")
        print(f"Servidor: {servidor}")
        print(f"Base de datos: {base_datos}")
        print(f"Fecha del servidor: {fecha_servidor}")


if __name__ == "__main__":
    probar_conexion()