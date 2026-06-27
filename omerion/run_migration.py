import os
import psycopg
from dotenv import load_dotenv

load_dotenv()
db_url = os.environ["DATABASE_URL"]

seed_sql = """
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'saas_founder';
ALTER TYPE persona ADD VALUE IF NOT EXISTS 'ops_leader';
COMMIT;

INSERT INTO markets (name, metadata) VALUES ('General B2B SaaS', '{"region": "North America"}') ON CONFLICT (name) DO NOTHING;
INSERT INTO accounts (name, domain, market_id, persona, score, status)
SELECT 'Stripe', 'stripe.com', m.market_id, 'saas_founder', 0.85, 'new' FROM markets m WHERE m.name = 'General B2B SaaS' ON CONFLICT DO NOTHING;
INSERT INTO accounts (name, domain, market_id, persona, score, status)
SELECT 'Linear', 'linear.app', m.market_id, 'saas_founder', 0.80, 'new' FROM markets m WHERE m.name = 'General B2B SaaS' ON CONFLICT DO NOTHING;
INSERT INTO accounts (name, domain, market_id, persona, score, status)
SELECT 'Retool', 'retool.com', m.market_id, 'ops_leader', 0.78, 'new' FROM markets m WHERE m.name = 'General B2B SaaS' ON CONFLICT DO NOTHING;
"""

def main():
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            print("Executing fixed seed SQL with new enums...")
            cur.execute(seed_sql)
            print("Seed SQL complete!")
        conn.commit()

if __name__ == "__main__":
    main()
