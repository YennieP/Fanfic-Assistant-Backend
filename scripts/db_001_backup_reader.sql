\set ON_ERROR_STOP on
\getenv backup_password BACKUP_READER_PASSWORD

\if :{?backup_password}
\else
  \echo 'BACKUP_READER_PASSWORD must be set in the psql process environment.'
  \quit 64
\endif

SELECT :'backup_password' <> '' AS backup_password_present \gset
\if :backup_password_present
\else
  \echo 'BACKUP_READER_PASSWORD must not be empty.'
  \quit 64
\endif

SELECT CASE
    WHEN EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'backup_reader')
        THEN format(
            'ALTER ROLE backup_reader WITH LOGIN INHERIT PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
            :'backup_password'
        )
    ELSE format(
        'CREATE ROLE backup_reader WITH LOGIN INHERIT PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
        :'backup_password'
    )
END \gexec

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO backup_reader',
    current_database()
) \gexec

GRANT pg_read_all_data TO backup_reader;
ALTER ROLE backup_reader SET default_transaction_read_only = on;
ALTER ROLE backup_reader SET statement_timeout = '30min';
ALTER ROLE backup_reader SET lock_timeout = '5s';
ALTER ROLE backup_reader SET idle_in_transaction_session_timeout = '60s';
