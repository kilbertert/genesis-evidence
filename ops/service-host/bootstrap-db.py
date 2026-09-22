import os
import pathlib

from genesis_evidence.core.store import Database

db = pathlib.Path(os.environ["GENESIS_EVIDENCE_DATABASE"])
Database(db).initialize()
print("db:", db)
print("tables:", len(Database(db).table_names()))
print("exists:", db.is_file())
