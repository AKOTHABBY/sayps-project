import os
from datetime import datetime, date
from decimal import Decimal
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, ".env"))
BACKUP_DIR = os.path.join(BASE, "backups")
KEEP_LAST = 14


def _literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, (datetime, date)):
        return "'" + str(v) + "'"
    if isinstance(v, bytes):
        v = v.decode("utf-8", "replace")
    return "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"


def make_backup():
    engine = create_engine(
        f"mysql+pymysql://{os.getenv('DB_USER')}:{os.getenv('DB_PASSWORD')}"
        f"@{os.getenv('DB_HOST')}/{os.getenv('DB_NAME')}"
    )
    os.makedirs(BACKUP_DIR, exist_ok=True)
    name = "sayps_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".sql"
    path = os.path.join(BACKUP_DIR, name)

    with engine.connect() as conn, open(path, "w", encoding="utf-8") as out:
        out.write("SET FOREIGN_KEY_CHECKS=0;\n")
        for (table,) in conn.execute(text("SHOW TABLES")).fetchall():
            create = conn.execute(text(f"SHOW CREATE TABLE `{table}`")).fetchone()[1]
            out.write(f"\nDROP TABLE IF EXISTS `{table}`;\n{create};\n")
            result = conn.execute(text(f"SELECT * FROM `{table}`"))
            cols = ", ".join(f"`{c}`" for c in result.keys())
            for row in result:
                vals = ", ".join(_literal(v) for v in row)
                out.write(f"INSERT INTO `{table}` ({cols}) VALUES ({vals});\n")
        out.write("\nSET FOREIGN_KEY_CHECKS=1;\n")

    files = sorted(f for f in os.listdir(BACKUP_DIR) if f.endswith(".sql"))
    for old in files[:-KEEP_LAST]:
        os.remove(os.path.join(BACKUP_DIR, old))
    return name


if __name__ == "__main__":
    print("Backup saved:", make_backup())