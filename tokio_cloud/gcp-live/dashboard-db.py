import os, psycopg2
from psycopg2.extras import RealDictCursor
PG=dict(host=os.getenv("POSTGRES_HOST","postgres"),port=int(os.getenv("POSTGRES_PORT","5432")),dbname=os.getenv("POSTGRES_DB","tokio"),user=os.getenv("POSTGRES_USER","tokio"),password=os.getenv("POSTGRES_PASSWORD","changeme"))
def _get_postgres_conn(): return psycopg2.connect(**PG)
def _return_postgres_conn(c): c.close()
