from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = ROOT / "scripts" / "backup_postgres.sh"
ROLE_SQL = ROOT / "scripts" / "db_001_backup_reader.sql"
DOCKERFILE = ROOT / "Dockerfile.backup"
WRITER_POLICY = ROOT / "ops" / "backup" / "aws-writer-policy.example.json"
LIFECYCLE_POLICY = ROOT / "ops" / "backup" / "aws-lifecycle.example.json"
DOCKERIGNORE = ROOT / ".dockerignore"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    capture_dir = tmp_path / "capture"
    bin_dir.mkdir()
    capture_dir.mkdir()

    _write_executable(
        bin_dir / "date",
        """#!/bin/sh
case "$2" in
  +%Y%m%dT%H%M%SZ) printf '%s\n' "${FAKE_TIMESTAMP:-20261026T200000Z}" ;;
  +%Y/%m/%d) printf '%s\n' "${FAKE_DATE_PATH:-2026/10/26}" ;;
  +%G/week-%V) printf '%s\n' "${FAKE_WEEK_PATH:-2026/week-44}" ;;
  +%Y/%m) printf '%s\n' "${FAKE_MONTH_PATH:-2026/10}" ;;
  +%u) printf '%s\n' "${FAKE_WEEKDAY:-1}" ;;
  +%d) printf '%s\n' "${FAKE_MONTHDAY:-26}" ;;
  *) exit 64 ;;
esac
""",
    )
    _write_executable(
        bin_dir / "pg_dump",
        """#!/bin/sh
printf '%s\n' "$@" > "$CAPTURE_DIR/pg_dump.args"
printf '%s\n' "$PGHOST|$PGPORT|$PGDATABASE|$PGUSER" > "$CAPTURE_DIR/pg_dump.connection"
printf '%s' 'raw-private-dump'
exit "${FAKE_PG_DUMP_EXIT:-0}"
""",
    )
    _write_executable(
        bin_dir / "age",
        """#!/bin/sh
output=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --recipient)
      printf '%s\n' "$2" > "$CAPTURE_DIR/age.recipient"
      shift 2
      ;;
    --output)
      output="$2"
      shift 2
      ;;
    *) exit 64 ;;
  esac
done
cat >/dev/null
if [ "${FAKE_AGE_EXIT:-0}" -ne 0 ]; then
  exit "$FAKE_AGE_EXIT"
fi
printf '%s' 'age-ciphertext-only' > "$output"
""",
    )
    _write_executable(
        bin_dir / "sha256sum",
        """#!/bin/sh
printf '%064d  %s\n' 0 "$1"
""",
    )
    _write_executable(
        bin_dir / "aws",
        """#!/bin/sh
count_file="$CAPTURE_DIR/aws.count"
count=0
if [ -f "$count_file" ]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
printf '%s\n' "$@" > "$CAPTURE_DIR/aws.$count.args"

body=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --body)
      body="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done

if [ "${FAKE_AWS_FAIL_ON_CALL:-0}" -eq "$count" ]; then
  exit 70
fi
cp "$body" "$CAPTURE_DIR/upload.$count"
printf '%s\n' '{}'
""",
    )
    return bin_dir, capture_dir


def _backup_env(tmp_path: Path, bin_dir: Path, capture_dir: Path) -> dict[str, str]:
    return {
        "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": str(tmp_path / "tmp"),
        "CAPTURE_DIR": str(capture_dir),
        "PGHOST": "postgres.railway.internal",
        "PGPORT": "5432",
        "PGDATABASE": "railway",
        "PGUSER": "backup_reader",
        "PGPASSWORD": "database-test-secret",
        "BACKUP_AGE_RECIPIENT": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqd3x0m",
        "BACKUP_S3_BUCKET": "fanfic-backups-test",
        "BACKUP_S3_PREFIX": "fanfic-production",
        "AWS_ACCESS_KEY_ID": "access-test-secret",
        "AWS_SECRET_ACCESS_KEY": "storage-test-secret",
        "AWS_REGION": "us-west-2",
    }


def _run_backup(tmp_path: Path, extra_env: dict[str, str] | None = None):
    bin_dir, capture_dir = _fake_tools(tmp_path)
    (tmp_path / "tmp").mkdir()
    env = _backup_env(tmp_path, bin_dir, capture_dir)
    if extra_env:
        env.update(extra_env)
    result = subprocess.run(
        ["bash", str(BACKUP_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, capture_dir, tmp_path / "tmp", env


def _captured_arguments(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_daily_backup_streams_pg_dump_into_age_and_uploads_only_ciphertext(tmp_path):
    result, capture_dir, temp_dir, env = _run_backup(tmp_path)

    assert result.returncode == 0, result.stderr
    assert _captured_arguments(capture_dir / "pg_dump.args") == [
        "--format=custom",
        "--compress=zstd:6",
        "--no-owner",
        "--no-privileges",
        "--file=-",
    ]
    assert (capture_dir / "pg_dump.connection").read_text(encoding="utf-8").strip() == (
        "postgres.railway.internal|5432|railway|backup_reader"
    )
    assert (capture_dir / "age.recipient").read_text(encoding="utf-8").strip() == env[
        "BACKUP_AGE_RECIPIENT"
    ]
    assert (capture_dir / "upload.1").read_bytes() == b"age-ciphertext-only"

    aws_args = _captured_arguments(capture_dir / "aws.1.args")
    assert aws_args[:2] == ["s3api", "put-object"]
    assert aws_args[aws_args.index("--bucket") + 1] == "fanfic-backups-test"
    assert aws_args[aws_args.index("--key") + 1] == (
        "fanfic-production/daily/2026/10/26/"
        "fanfic-postgres-20261026T200000Z.dump.age"
    )
    assert "--checksum-algorithm" in aws_args
    assert aws_args[aws_args.index("--checksum-algorithm") + 1] == "SHA256"

    combined_output = result.stdout + result.stderr
    for forbidden in (
        "raw-private-dump",
        env["PGPASSWORD"],
        env["AWS_ACCESS_KEY_ID"],
        env["AWS_SECRET_ACCESS_KEY"],
    ):
        assert forbidden not in combined_output
        assert forbidden not in "\n".join(aws_args)
    assert not list(temp_dir.iterdir())


def test_sunday_month_start_also_writes_weekly_and_monthly_copies(tmp_path):
    result, capture_dir, _temp_dir, _env = _run_backup(
        tmp_path,
        {
            "FAKE_TIMESTAMP": "20230101T090000Z",
            "FAKE_DATE_PATH": "2023/01/01",
            "FAKE_WEEK_PATH": "2022/week-52",
            "FAKE_MONTH_PATH": "2023/01",
            "FAKE_WEEKDAY": "7",
            "FAKE_MONTHDAY": "01",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (capture_dir / "aws.count").read_text(encoding="utf-8") == "3"
    keys = []
    for index in (1, 2, 3):
        args = _captured_arguments(capture_dir / f"aws.{index}.args")
        keys.append(args[args.index("--key") + 1])
    assert keys == [
        "fanfic-production/daily/2023/01/01/fanfic-postgres-20230101T090000Z.dump.age",
        "fanfic-production/weekly/2022/week-52/fanfic-postgres-20230101T090000Z.dump.age",
        "fanfic-production/monthly/2023/01/fanfic-postgres-20230101T090000Z.dump.age",
    ]


def test_pg_dump_failure_never_uploads_and_cleans_temporary_ciphertext(tmp_path):
    result, capture_dir, temp_dir, _env = _run_backup(
        tmp_path,
        {"FAKE_PG_DUMP_EXIT": "9"},
    )

    assert result.returncode != 0
    assert not list(capture_dir.glob("aws.*.args"))
    assert not list(temp_dir.iterdir())
    assert "backup_completed" not in result.stdout


def test_missing_or_invalid_configuration_fails_before_dump(tmp_path):
    result, capture_dir, _temp_dir, env = _run_backup(
        tmp_path,
        {"BACKUP_AGE_RECIPIENT": "not-an-age-recipient"},
    )

    assert result.returncode != 0
    assert not (capture_dir / "pg_dump.args").exists()
    combined_output = result.stdout + result.stderr
    assert "BACKUP_AGE_RECIPIENT" in combined_output
    assert env["PGPASSWORD"] not in combined_output
    assert env["AWS_SECRET_ACCESS_KEY"] not in combined_output


def test_public_database_host_or_non_backup_role_is_rejected(tmp_path):
    for unsafe_configuration in (
        {"PGHOST": "public.proxy.example", "PGUSER": "backup_reader"},
        {"PGHOST": "postgres.railway.internal", "PGUSER": "postgres"},
    ):
        case_path = tmp_path / unsafe_configuration["PGUSER"]
        case_path.mkdir()
        result, capture_dir, _temp_dir, _env = _run_backup(
            case_path,
            unsafe_configuration,
        )

        assert result.returncode != 0
        assert not (capture_dir / "pg_dump.args").exists()


def test_upload_failure_is_not_reported_as_success_and_cleans_ciphertext(tmp_path):
    result, _capture_dir, temp_dir, _env = _run_backup(
        tmp_path,
        {"FAKE_AWS_FAIL_ON_CALL": "1"},
    )

    assert result.returncode != 0
    assert "backup_completed" not in result.stdout
    assert not list(temp_dir.iterdir())


def test_backup_reader_sql_is_read_only_and_loads_password_from_environment():
    sql = ROLE_SQL.read_text(encoding="utf-8")

    assert r"\getenv backup_password BACKUP_READER_PASSWORD" in sql
    assert "GRANT pg_read_all_data TO backup_reader" in sql
    assert "default_transaction_read_only = on" in sql
    assert r"\quit 64" in sql
    for restriction in (
        "NOSUPERUSER",
        "NOCREATEDB",
        "NOCREATEROLE",
        "NOREPLICATION",
        "NOBYPASSRLS",
    ):
        assert restriction in sql
    assert "BACKUP_READER_PASSWORD=" not in sql


def test_backup_image_pins_postgres_major_and_runs_as_non_root():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")

    assert "FROM postgres:18.6-bookworm@sha256:" in dockerfile
    assert "age" in dockerfile
    assert "awscli" in dockerfile
    assert "USER postgres" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/backup-postgres"]' in dockerfile

    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert dockerignore[0] == "**"
    assert "!Dockerfile.backup" in dockerignore
    assert "!scripts/backup_postgres.sh" in dockerignore
    assert not any(".env" in line and line.startswith("!") for line in dockerignore)


def test_storage_policy_is_put_only_and_lifecycle_owns_retention():
    writer_policy = json.loads(WRITER_POLICY.read_text(encoding="utf-8"))
    statement = writer_policy["Statement"]

    assert len(statement) == 1
    assert statement[0]["Action"] == ["s3:PutObject"]
    resources = statement[0]["Resource"]
    assert len(resources) == 3
    assert any("/daily/*" in resource for resource in resources)
    assert any("/weekly/*" in resource for resource in resources)
    assert any("/monthly/*" in resource for resource in resources)
    assert not any(
        forbidden in json.dumps(writer_policy)
        for forbidden in ("GetObject", "ListBucket", "DeleteObject")
    )

    lifecycle = json.loads(LIFECYCLE_POLICY.read_text(encoding="utf-8"))
    retention = {
        rule["Filter"]["Prefix"]: rule["Expiration"]["Days"]
        for rule in lifecycle["Rules"]
    }
    assert retention == {
        "fanfic-production/daily/": 14,
        "fanfic-production/weekly/": 56,
        "fanfic-production/monthly/": 183,
    }
