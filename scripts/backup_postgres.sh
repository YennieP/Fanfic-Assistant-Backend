#!/usr/bin/env bash
set -euo pipefail

umask 077

require_variable() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    printf 'Required configuration is missing: %s\n' "$name" >&2
    exit 64
  fi
}

for name in \
  PGHOST \
  PGPORT \
  PGDATABASE \
  PGUSER \
  PGPASSWORD \
  BACKUP_AGE_RECIPIENT \
  BACKUP_S3_BUCKET \
  BACKUP_S3_PREFIX \
  AWS_ACCESS_KEY_ID \
  AWS_SECRET_ACCESS_KEY \
  AWS_REGION
do
  require_variable "$name"
done

if [[ ! "$PGPORT" =~ ^[0-9]+$ ]]; then
  printf 'PGPORT must contain only digits.\n' >&2
  exit 64
fi

if [[ "$PGUSER" != "backup_reader" ]]; then
  printf 'PGUSER must be the dedicated backup_reader role.\n' >&2
  exit 64
fi

if [[ "$PGHOST" != *.railway.internal ]]; then
  printf 'PGHOST must use the Railway private network.\n' >&2
  exit 64
fi

if [[ ! "$BACKUP_AGE_RECIPIENT" =~ ^age1[023456789acdefghjklmnpqrstuvwxyz]+$ ]]; then
  printf 'BACKUP_AGE_RECIPIENT must be an age X25519 public recipient.\n' >&2
  exit 64
fi

if [[ ! "$BACKUP_S3_BUCKET" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ ]]; then
  printf 'BACKUP_S3_BUCKET must be a normalized bucket name without a path.\n' >&2
  exit 64
fi

if [[ \
  "$BACKUP_S3_PREFIX" == /* || \
  "$BACKUP_S3_PREFIX" == */ || \
  "$BACKUP_S3_PREFIX" == *..* || \
  "$BACKUP_S3_PREFIX" == *//* || \
  ! "$BACKUP_S3_PREFIX" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$
]]; then
  printf 'BACKUP_S3_PREFIX must be a relative, normalized object prefix.\n' >&2
  exit 64
fi

if [[ -n "${BACKUP_S3_ENDPOINT_URL:-}" && "$BACKUP_S3_ENDPOINT_URL" != https://* ]]; then
  printf 'BACKUP_S3_ENDPOINT_URL must use HTTPS when it is set.\n' >&2
  exit 64
fi

for command in pg_dump age aws sha256sum; do
  if ! command -v "$command" >/dev/null 2>&1; then
    printf 'Required command is unavailable: %s\n' "$command" >&2
    exit 69
  fi
done

export PGAPPNAME="fanfic-logical-backup"
export PGCONNECT_TIMEOUT="${PGCONNECT_TIMEOUT:-15}"
export AWS_PAGER=""
export AWS_EC2_METADATA_DISABLED="true"

work_dir="$(mktemp -d "${TMPDIR:-/tmp}/fanfic-backup.XXXXXX")"
cipher_path="$work_dir/backup.dump.age"
cleanup() {
  if [[ -n "${cipher_path:-}" && -f "$cipher_path" ]]; then
    rm -f -- "$cipher_path"
  fi
  if [[ -n "${work_dir:-}" && -d "$work_dir" ]]; then
    rmdir -- "$work_dir"
  fi
}
trap cleanup EXIT HUP INT TERM

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
date_path="$(date -u +%Y/%m/%d)"
week_path="$(date -u +%G/week-%V)"
month_path="$(date -u +%Y/%m)"
weekday="$(date -u +%u)"
month_day="$(date -u +%d)"
filename="fanfic-postgres-${timestamp}.dump.age"

pg_dump \
  --format=custom \
  --compress=zstd:6 \
  --no-owner \
  --no-privileges \
  --file=- \
  | age \
      --recipient "$BACKUP_AGE_RECIPIENT" \
      --output "$cipher_path"

cipher_bytes="$(wc -c < "$cipher_path" | tr -d '[:space:]')"
cipher_sha256="$(sha256sum "$cipher_path" | awk '{print $1}')"

aws_base=(aws)
if [[ -n "${BACKUP_S3_ENDPOINT_URL:-}" ]]; then
  aws_base+=(--endpoint-url "$BACKUP_S3_ENDPOINT_URL")
fi

upload_object() {
  local tier="$1"
  local partition="$2"
  local object_key="${BACKUP_S3_PREFIX}/${tier}/${partition}/${filename}"

  "${aws_base[@]}" s3api put-object \
    --bucket "$BACKUP_S3_BUCKET" \
    --key "$object_key" \
    --body "$cipher_path" \
    --content-type "application/octet-stream" \
    --checksum-algorithm SHA256 \
    --metadata "format=pg_dump_custom,encryption=age-x25519,cipher_sha256=${cipher_sha256}" \
    >/dev/null

  printf 'backup_uploaded tier=%s key=%s bytes=%s cipher_sha256=%s\n' \
    "$tier" "$object_key" "$cipher_bytes" "$cipher_sha256"
}

copy_count=1
upload_object "daily" "$date_path"

if [[ "$weekday" == "7" ]]; then
  upload_object "weekly" "$week_path"
  copy_count=$((copy_count + 1))
fi

if [[ "$month_day" == "01" ]]; then
  upload_object "monthly" "$month_path"
  copy_count=$((copy_count + 1))
fi

printf 'backup_completed timestamp=%s copies=%s bytes=%s cipher_sha256=%s\n' \
  "$timestamp" "$copy_count" "$cipher_bytes" "$cipher_sha256"
