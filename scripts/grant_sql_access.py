"""Grants a managed identity read access to the Azure SQL database (contained user + db_datareader).

Must be run by a Microsoft Entra admin of the SQL server (uses the Azure CLI sign-in).
Usage: python scripts/grant_sql_access.py <server>.database.windows.net <database> <identity-name> <principal-object-id>
"""

import struct
import sys
from contextlib import closing

import pyodbc
from azure.identity import AzureCliCredential

SQL_COPT_SS_ACCESS_TOKEN = 1256


def main(server: str, database: str, identity_name: str, object_id: str):
    token = AzureCliCredential().get_token("https://database.windows.net/.default").token.encode("utf-16-le")
    attrs = {SQL_COPT_SS_ACCESS_TOKEN: struct.pack(f"<I{len(token)}s", len(token), token)}
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;Database={database};Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    safe_name = identity_name.replace("]", "]]")
    safe_oid = object_id.replace("'", "")
    with closing(pyodbc.connect(conn_str, attrs_before=attrs, autocommit=True)) as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM sys.database_principals WHERE name = ?", identity_name)
        if cur.fetchone()[0] == 0:
            # OBJECT_ID avoids ambiguity when several Entra principals share the same display name
            cur.execute(f"CREATE USER [{safe_name}] FROM EXTERNAL PROVIDER WITH OBJECT_ID = '{safe_oid}'")
            print(f"Created database user [{identity_name}]")
        else:
            print(f"Database user [{identity_name}] already exists")
        cur.execute(
            "SELECT COUNT(*) FROM sys.database_role_members rm "
            "JOIN sys.database_principals r ON rm.role_principal_id = r.principal_id "
            "JOIN sys.database_principals m ON rm.member_principal_id = m.principal_id "
            "WHERE r.name = 'db_datareader' AND m.name = ?",
            identity_name,
        )
        if cur.fetchone()[0] == 0:
            cur.execute(f"ALTER ROLE db_datareader ADD MEMBER [{safe_name}]")
            print(f"Added [{identity_name}] to db_datareader")
        else:
            print(f"[{identity_name}] is already in db_datareader")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
