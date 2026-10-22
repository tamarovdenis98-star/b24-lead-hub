-- 010: read-only role for dashboards (Grafana). No access to tables with tokens or settings.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_user') \gexec

GRANT CONNECT ON DATABASE :"DBNAME" TO :"ro_user";
GRANT USAGE ON SCHEMA public TO :"ro_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
REVOKE ALL ON b24_auth, b24_app, settings FROM :"ro_user";
