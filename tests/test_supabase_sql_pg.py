"""마이그레이션을 실제 PostgreSQL 에 적용해 권한·RLS·제약이 의도대로 동작하는지 확인한다.

Supabase 의 역할(anon·authenticated·service_role)·기본 권한·storage.buckets 를 흉내 낸 빈 DB 를 임시로 띄운다.
PostgreSQL 서버 프로그램(initdb·pg_ctl·psql)이 없으면 건너뛴다(GitHub ubuntu 러너에는 기본으로 있다).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from studio import paths

MIGRATION = paths.ROOT / "supabase" / "migrations" / "20261010000000_init.sql"

# Supabase 프로젝트가 처음부터 갖고 있는 것(마이그레이션이 기대하는 환경)
BOOTSTRAP = """
create role anon nologin noinherit;
create role authenticated nologin noinherit;
create role service_role nologin noinherit bypassrls;
grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
create schema storage;
create table storage.buckets (id text primary key, name text not null, public boolean default false);
"""


def _pg_bin() -> Path | None:
    cands: list[Path] = []
    if shutil.which("pg_config"):
        out = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, check=False).stdout.strip()
        if out:
            cands.append(Path(out))
    cands += sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)
    for name in ("initdb",):
        if shutil.which(name):
            cands.append(Path(shutil.which(name)).parent)  # type: ignore[arg-type]
    for d in cands:
        if all((d / b).exists() for b in ("initdb", "pg_ctl", "postgres")):
            return d
    return None


@dataclass
class Pg:
    bindir: Path
    sockdir: Path
    port: int
    as_user: list[str]

    def psql(self, sql: str, *, role: str | None = None) -> subprocess.CompletedProcess:
        psql = self.bindir / "psql" if (self.bindir / "psql").exists() else Path(shutil.which("psql") or "psql")
        if role:
            sql = f"set role {role};\n{sql}"
        return subprocess.run(
            [str(psql), "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", str(self.sockdir), "-p", str(self.port),
             "-U", "postgres", "-d", "postgres", "-c", sql],
            capture_output=True, text=True, timeout=60, check=False,
        )

    def ok(self, sql: str, *, role: str | None = None) -> str:
        r = self.psql(sql, role=role)
        assert r.returncode == 0, f"실패해야 하지 않는데 실패: {sql}\n{r.stderr}"
        return r.stdout.strip()

    def fails(self, sql: str, needle: str, *, role: str | None = None) -> None:
        r = self.psql(sql, role=role)
        assert r.returncode != 0, f"거부돼야 하는데 성공: {sql}"
        assert needle in r.stderr, f"오류 문구가 다름: {r.stderr}"

    def apply_file(self, path: Path) -> None:
        r = self.psql(path.read_text(encoding="utf-8"))
        assert r.returncode == 0, r.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pg() -> Iterator[Pg]:
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("PostgreSQL 서버 프로그램(initdb·pg_ctl)이 없다")
    as_user: list[str] = []
    if hasattr(os, "geteuid") and os.geteuid() == 0:     # initdb 는 root 로 돌지 않는다
        if not shutil.which("runuser"):
            pytest.skip("root 인데 runuser 가 없다")
        import pwd  # 유닉스 전용 — root 일 때만 필요

        try:
            pw = pwd.getpwnam("postgres")
        except KeyError:
            pytest.skip("root 인데 postgres 사용자가 없다")
        as_user = ["runuser", "-u", "postgres", "--"]
    base = Path(tempfile.mkdtemp(prefix="studio-pg-", dir="/tmp" if Path("/tmp").is_dir() else None))
    if as_user:
        os.chown(base, pw.pw_uid, pw.pw_gid)
    data, port = base / "data", _free_port()
    try:
        subprocess.run([*as_user, str(bindir / "initdb"), "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                       check=True, capture_output=True, timeout=120)
        subprocess.run([*as_user, str(bindir / "pg_ctl"), "-D", str(data), "-l", str(base / "log"), "-w", "-t", "60",
                        "-o", f"-k {base} -p {port} -c listen_addresses=''", "start"],
                       check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        shutil.rmtree(base, ignore_errors=True)
        pytest.skip(f"임시 PostgreSQL 을 띄우지 못했다: {e}")
    db = Pg(bindir, base, port, as_user)
    try:
        assert db.psql(BOOTSTRAP).returncode == 0
        db.apply_file(MIGRATION)
        yield db
    finally:
        subprocess.run([*as_user, str(bindir / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                       capture_output=True, timeout=60, check=False)
        shutil.rmtree(base, ignore_errors=True)


def test_migration_is_rerunnable(pg):
    pg.apply_file(MIGRATION)   # 두 번째 적용도 성공(if not exists · drop policy if exists · on conflict)
    assert pg.ok("select count(*) from pg_policies where schemaname = 'public'") == "2"


def test_rls_enabled_on_all_tables(pg):
    out = pg.ok("select relname || ':' || relrowsecurity from pg_class where relnamespace = 'public'::regnamespace "
                "and relkind = 'r' order by relname")
    assert out.split() == ["app_events:true", "app_installs:true", "link_clicks:true", "oauth_tokens:true", "purchases:true"]


def test_anon_inserts_install_and_event(pg):
    pg.ok("insert into public.app_installs (app_user_id, os, utm_source, utm_medium, utm_campaign, utm_content, raw_referrer, app_version) "
          "values ('local:pg-1', 'android', 'youtube', 'description', '2026-W42', '2026W42-L1', 'utm_source=youtube', '1.0.0')", role="anon")
    pg.ok("insert into public.app_events (app_user_id, name, props) values ('local:pg-1', 'session_start', '{\"premium\": false}')", role="anon")
    pg.ok("insert into public.app_events (app_user_id, name) values ('local:pg-1', 'paywall_view')", role="anon")
    assert pg.ok("select count(*) from public.app_events where app_user_id = 'local:pg-1'", role="service_role") == "2"
    assert pg.ok("select props from public.app_events where name = 'paywall_view'", role="service_role") == "{}"


def test_duplicate_install_is_unique_violation(pg):
    pg.ok("insert into public.app_installs (app_user_id) values ('local:dup')", role="anon")
    # PostgREST 는 23505 를 409 로 돌려주고, 앱은 409 를 "이미 들어감"으로 처리한다
    pg.fails("insert into public.app_installs (app_user_id) values ('local:dup')", "duplicate key", role="anon")


def test_anon_cannot_read_or_touch_other_columns_and_tables(pg):
    denied = "permission denied"
    pg.fails("select * from public.app_installs", denied, role="anon")
    pg.fails("select * from public.app_events", denied, role="anon")
    pg.fails("insert into public.app_installs (created_at, app_user_id) values (now() - interval '1 year', 'x')", denied, role="anon")
    pg.fails("insert into public.app_events (id, app_user_id, name) values (999999, 'x', 'y')", denied, role="anon")
    pg.fails("update public.app_installs set os = 'ios'", denied, role="anon")
    pg.fails("delete from public.app_events", denied, role="anon")
    pg.fails("insert into public.link_clicks (source) values ('x')", denied, role="anon")
    pg.fails("insert into public.purchases (event_id) values ('x')", denied, role="anon")
    pg.fails("select * from public.oauth_tokens", denied, role="anon")
    pg.fails("select * from public.purchases", denied, role="anon")


def test_authenticated_has_no_access(pg):
    pg.fails("insert into public.app_events (app_user_id, name) values ('u', 'x')", "permission denied", role="authenticated")
    pg.fails("select * from public.oauth_tokens", "permission denied", role="authenticated")


@pytest.mark.parametrize(
    "sql",
    [
        "insert into public.app_events (app_user_id, name) values ('u', 'bad name')",
        "insert into public.app_events (app_user_id, name) values ('u', '')",
        "insert into public.app_events (app_user_id, name, props) values ('u', 'x', '[1,2]')",
        "insert into public.app_events (app_user_id, name, props) values ('u', 'x', jsonb_build_object('a', repeat('z', 9000)))",
        "insert into public.app_events (app_user_id, name) values (repeat('u', 201), 'x')",
        "insert into public.app_installs (app_user_id, utm_source) values ('local:long', repeat('s', 101))",
        "insert into public.app_installs (app_user_id, raw_referrer) values ('local:ref', repeat('r', 1001))",
        "insert into public.app_installs (app_user_id) values ('')",
    ],
)
def test_check_constraints_limit_abuse(pg, sql):
    pg.fails(sql, "violates check constraint", role="anon")


def test_service_role_writes_everything_and_purchases_dedupe(pg):
    q = ("insert into public.purchases (event_id, event_type, price, currency, price_in_usd, raw) "
         "values ('evt-1', 'INITIAL_PURCHASE', {p}, 'KRW', 4.12, '{{\"is_trial_conversion\": false}}') on conflict (event_id) do nothing")
    pg.ok(q.format(p=5900), role="service_role")
    pg.ok(q.format(p=1), role="service_role")    # 같은 event_id 는 무시(웹훅 재전송)
    assert pg.ok("select count(*) || ':' || max(price) from public.purchases where event_id = 'evt-1'", role="service_role") == "1:5900"
    assert pg.ok("select raw->>'is_trial_conversion' from public.purchases where event_id = 'evt-1'", role="service_role") == "false"
    pg.ok("insert into public.link_clicks (source, medium, campaign, content, os, ip_hash) "
          "values ('youtube', 'shorts', '2026-W42', '2026W42-S03', 'ios', repeat('a', 64))", role="service_role")
    pg.ok("insert into public.oauth_tokens (key, value) values ('tiktok', '{\"refresh_token\": \"x\"}') "
          "on conflict (key) do update set value = excluded.value, updated_at = now()", role="service_role")
    assert pg.ok("select value->>'refresh_token' from public.oauth_tokens where key = 'tiktok'", role="service_role") == "x"


def test_media_bucket_public(pg):
    assert pg.ok("select id || ':' || public from storage.buckets") == "media:true"


def test_postgrest_minimal_insert_shape_works_for_anon(pg):
    """앱은 Prefer: return=minimal 로 보낸다. PostgREST 가 만드는 모양(CTE + json_populate_recordset + RETURNING 1)이
    읽기 권한 없이도 통과하고, return=representation(RETURNING *)은 거부되는지 본다."""
    pg.ok("""with pgrst_source as (
      insert into public.app_installs(app_user_id, os, utm_source)
      select pgrst_body.app_user_id, pgrst_body.os, pgrst_body.utm_source
      from json_populate_recordset(null::public.app_installs,
           '[{"app_user_id":"local:rest","os":"android","utm_source":"youtube"}]'::json) pgrst_body
      returning 1)
    select pg_catalog.count(_postgrest_t) from (select * from pgrst_source) _postgrest_t""", role="anon")
    pg.fails("insert into public.app_events (app_user_id, name) values ('u', 'x') returning *", "permission denied", role="anon")
