"""supabase/ 정적 검사 — 마이그레이션 SQL 이 계약 테이블·열·RLS·인덱스·버킷을 갖췄는지, 함수 설정, 다른 모듈과의 열 이름 일치."""

from __future__ import annotations

import re
import tomllib

import pytest

from studio import paths

SUPA = paths.ROOT / "supabase"
MIGRATION = SUPA / "migrations" / "20261010000000_init.sql"

# 계약(작업 지시서의 Supabase 테이블 계약). 값: (타입, 꼭 있어야 하는 수식어들)
CONTRACT: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
    "link_clicks": {
        "id": ("bigserial", ("primary key",)),
        "created_at": ("timestamptz", ("default now()",)),
        "source": ("text", ()), "medium": ("text", ()), "campaign": ("text", ()), "content": ("text", ()),
        "os": ("text", ()), "user_agent": ("text", ()), "referer": ("text", ()), "ip_hash": ("text", ()),
    },
    "app_installs": {
        "id": ("bigserial", ("primary key",)),
        "created_at": ("timestamptz", ("default now()",)),
        "app_user_id": ("text", ("unique", "not null")),
        "os": ("text", ()), "utm_source": ("text", ()), "utm_medium": ("text", ()), "utm_campaign": ("text", ()),
        "utm_content": ("text", ()), "raw_referrer": ("text", ()), "app_version": ("text", ()),
    },
    "app_events": {
        "id": ("bigserial", ("primary key",)),
        "created_at": ("timestamptz", ("default now()",)),
        "app_user_id": ("text", ("not null",)),
        "name": ("text", ("not null",)),
        "props": ("jsonb", ("default '{}'",)),
    },
    "purchases": {
        "id": ("bigserial", ("primary key",)),
        "created_at": ("timestamptz", ("default now()",)),
        "event_id": ("text", ("unique", "not null")),
        "event_type": ("text", ()), "app_user_id": ("text", ()), "product_id": ("text", ()), "period_type": ("text", ()),
        "price": ("numeric", ()), "currency": ("text", ()), "price_in_usd": ("numeric", ()), "store": ("text", ()),
        "environment": ("text", ()), "event_timestamp": ("timestamptz", ()), "raw": ("jsonb", ()),
    },
    "oauth_tokens": {
        "key": ("text", ("primary key",)),
        "value": ("jsonb", ("not null",)),
        "updated_at": ("timestamptz", ("default now()",)),
    },
}
ANON_INSERT_TABLES = {"app_installs", "app_events"}


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


@pytest.fixture(scope="module")
def sql() -> str:
    return _strip_comments(MIGRATION.read_text(encoding="utf-8")).lower()


def parse_tables(sql: str) -> dict[str, dict[str, str]]:
    tables: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"create table if not exists public\.(\w+)\s*\((.*?)\n\);", sql, re.DOTALL):
        cols: dict[str, str] = {}
        for line in m.group(2).splitlines():
            line = line.strip().rstrip(",").strip()
            if not line or line.startswith("constraint "):
                continue
            name, _, rest = line.partition(" ")
            cols[name] = re.sub(r"\s+", " ", rest.strip())
        tables[m.group(1)] = cols
    return tables


def test_migration_file_name():
    files = sorted(p.name for p in (SUPA / "migrations").glob("*.sql"))
    assert MIGRATION.name in files
    assert all(re.fullmatch(r"\d{14}_[a-z0-9_]+\.sql", f) for f in files)


def test_contract_tables_and_columns(sql):
    tables = parse_tables(sql)
    assert set(CONTRACT) <= set(tables), f"없는 표: {set(CONTRACT) - set(tables)}"
    for table, cols in CONTRACT.items():
        for col, (typ, mods) in cols.items():
            assert col in tables[table], f"{table}.{col} 이 없습니다"
            definition = tables[table][col]
            assert definition.split(" ")[0] == typ, f"{table}.{col} 타입 {definition!r} != {typ}"
            for mod in mods:
                assert mod in definition, f"{table}.{col} 에 '{mod}' 가 없습니다: {definition!r}"


def test_rls_enabled_everywhere(sql):
    enabled = set(re.findall(r"alter table public\.(\w+)\s+enable row level security", sql))
    assert enabled == set(CONTRACT)


def test_anon_can_only_insert_into_app_tables(sql):
    policies = re.findall(r"create policy (\w+) on public\.(\w+)\s+for (\w+)\s+to ([\w, ]+?)\s+with check", sql)
    assert {(t, cmd, roles.strip()) for _, t, cmd, roles in policies} == {(t, "insert", "anon") for t in ANON_INSERT_TABLES}
    assert "for select" not in sql and "for all" not in sql and "for update" not in sql and "for delete" not in sql
    # anon 권한은 먼저 모두 거두고, 정해진 열의 INSERT 와 시퀀스 사용만 준다
    revoked = re.search(r"revoke all on table ([^;]+?) from anon, authenticated;", sql)
    assert revoked and {f"public.{t}" for t in CONTRACT} <= {x.strip() for x in revoked.group(1).split(",")}
    grants = [g.strip() for g in re.findall(r"grant ([^;]+?) to anon;", sql, re.DOTALL)]
    assert grants, "anon 에게 아무 권한도 없으면 앱이 설치·이벤트를 못 쓴다"
    for g in grants:
        g = re.sub(r"\s+", " ", g)
        ok = re.fullmatch(r"insert \([a-z_, ]+\) on public\.(app_installs|app_events)", g) or g.startswith("usage on sequence")
        assert ok, f"anon 에게 허용 범위 밖 권한: {g}"
        assert "select" not in g and "update" not in g and "delete" not in g


def anon_insert_columns(sql: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for cols, table in re.findall(r"grant insert \(([^)]*)\)\s*on public\.(\w+) to anon;", sql):
        out[table] = {c.strip() for c in cols.split(",")}
    return out


def test_anon_cannot_set_id_or_created_at(sql):
    cols = anon_insert_columns(sql)
    assert set(cols) == ANON_INSERT_TABLES
    for table, c in cols.items():
        assert not c & {"id", "created_at"}, table
        assert c <= set(CONTRACT[table])


def test_service_role_has_everything(sql):
    m = re.search(r"grant all on table ([^;]+?) to service_role;", sql)
    assert m and {f"public.{t}" for t in CONTRACT} <= {x.strip() for x in m.group(1).split(",")}


def test_required_indexes(sql):
    idx = [(t, cols.replace(" ", "")) for t, cols in re.findall(r"create index if not exists \w+\s+on public\.(\w+) \(([^)]*)\)", sql)]

    def has(table: str, first_col: str) -> bool:
        return any(t == table and cols.split(",")[0] == first_col for t, cols in idx)

    for table in ("link_clicks", "app_installs", "app_events", "purchases"):
        assert has(table, "created_at"), f"{table}(created_at) 인덱스"
    assert has("link_clicks", "content")
    assert has("app_installs", "utm_content")
    assert has("app_events", "app_user_id")
    assert has("purchases", "app_user_id")
    # app_installs.app_user_id 는 unique 제약이 인덱스를 만든다


def test_length_limits_exist(sql):
    """anon 이 쓰는 표는 모든 text 열에 길이 제한이 있어야 한다(남용 완화)."""
    for table in ANON_INSERT_TABLES:
        body = re.search(rf"create table if not exists public\.{table}\s*\((.*?)\n\);", sql, re.DOTALL).group(1)
        for col, (typ, _) in CONTRACT[table].items():
            if typ == "text":
                limited = re.search(rf"char_length\({col}\)", body) or re.search(rf"\b{col} ~ '\^[^']*\{{\d+,\d+\}}\$'", body)
                assert limited, f"{table}.{col} 길이 제한 없음"
        assert "octet_length(props::text)" in body or table != "app_events"


def test_media_bucket_is_public(sql):
    assert re.search(r"insert into storage\.buckets \(id, name, public\)\s*values \('media', 'media', true\)", sql)
    assert "on conflict (id) do update set public" in sql
    assert "storage.objects" not in sql.replace("storage.objects 에", "")  # anon 쓰기 정책을 만들지 않는다


def test_config_toml_verify_jwt_off_for_public_functions():
    cfg = tomllib.loads((SUPA / "config.toml").read_text(encoding="utf-8"))
    assert cfg["functions"]["go"]["verify_jwt"] is False
    assert cfg["functions"]["revenuecat-webhook"]["verify_jwt"] is False
    assert cfg["api"]["max_rows"] == 1000


def test_function_files():
    for fn in ("go", "revenuecat-webhook"):
        index = (SUPA / "functions" / fn / "index.ts").read_text(encoding="utf-8")
        assert "Deno.serve(" in index
        assert (SUPA / "functions" / fn / "logic.ts").exists()
    shared = (SUPA / "functions" / "_shared" / "rest.ts").read_text(encoding="utf-8")
    assert "SUPABASE_SECRET_KEYS" in shared and "SUPABASE_SERVICE_ROLE_KEY" in shared
    assert "resolution=ignore-duplicates" in shared
    env_example = (SUPA / "functions" / ".env.example").read_text(encoding="utf-8")
    for name in ("REVENUECAT_WEBHOOK_AUTH", "IP_HASH_SALT", "ANDROID_PACKAGE", "IOS_APP_ID", "APPLE_PROVIDER_TOKEN", "LANDING_URL"):
        assert re.search(rf"^{name}=$", env_example, re.MULTILINE)
    assert not re.search(r"^SUPABASE_", env_example, re.MULTILINE)


def _ts_keys(src: str, func: str) -> set[str]:
    body = src[src.index(func):]
    body = body[: body.index("\n}\n")]
    return set(re.findall(r"^\s{4}(\w+)(?::|,$)", body, re.MULTILINE))   # key: value 와 줄임 표기(key,)


def test_go_writes_exactly_contract_columns():
    src = (SUPA / "functions" / "go" / "logic.ts").read_text(encoding="utf-8")
    keys = _ts_keys(src, "export async function buildClickRow")
    assert keys == set(CONTRACT["link_clicks"]) - {"id", "created_at"}
    assert 'CLICK_TABLE = "link_clicks"' in src
    assert "IP_HASH_SALT" in src and "sha256Hex" in src
    assert "status: 302" in src


def test_webhook_writes_exactly_contract_columns():
    src = (SUPA / "functions" / "revenuecat-webhook" / "logic.ts").read_text(encoding="utf-8")
    keys = _ts_keys(src, "export function toPurchaseRow")
    assert keys == set(CONTRACT["purchases"]) - {"id", "created_at"}
    assert 'onConflict: "event_id"' in src
    assert "timingSafeEqual" in src and 'AUTH_ENV = "REVENUECAT_WEBHOOK_AUTH"' in src


# ---------------------------------------------------------------- 다른 모듈과 열 이름 일치

def _all_columns() -> dict[str, set[str]]:
    return {t: set(c) for t, c in CONTRACT.items()}


def test_analytics_reads_only_contract_columns():
    funnel = pytest.importorskip("studio.analytics.collect.funnel")
    cols = _all_columns()

    def names(select: str) -> set[str]:
        out = set()
        for part in select.split(","):
            part = part.split(":", 1)[-1]          # alias:expr
            out.add(re.split(r"->", part)[0].strip())
        return out

    assert names(funnel.CLICK_COLUMNS) <= cols["link_clicks"]
    assert names(funnel.INSTALL_COLUMNS) <= cols["app_installs"]
    assert names(funnel.PURCHASE_COLUMNS) <= cols["purchases"]


def test_token_store_uses_oauth_tokens_columns():
    src = (paths.ROOT / "studio" / "tokens.py").read_text(encoding="utf-8")
    assert "/rest/v1/oauth_tokens" in src
    for col in ("key", "value", "updated_at"):
        assert f'"{col}"' in src


def test_app_inserts_only_granted_columns(sql):
    outbox = paths.ROOT / "app" / "src" / "core" / "outbox.ts"
    if not outbox.exists():
        pytest.skip("앱 코드가 아직 없다")
    src = outbox.read_text(encoding="utf-8")
    granted = anon_insert_columns(sql)
    for table in ANON_INSERT_TABLES:
        m = re.search(rf"{table}: \[([^\]]*)\]", src)
        assert m, f"outbox.ts 에서 {table} 열 목록을 찾지 못함"
        app_cols = set(re.findall(r"'(\w+)'", m.group(1)))
        assert app_cols <= granted[table], f"앱이 권한 없는 열을 보낸다: {app_cols - granted[table]}"


def test_storage_paths_match_bucket():
    """studio.publish.storage 가 쓰는 버킷 이름이 마이그레이션의 버킷과 같다(channel.yaml storage.bucket 기본값)."""
    from studio.config import StorageCfg

    assert StorageCfg().bucket == "media"


def test_go_link_matches_publish_utm():
    """studio.publish.utm.app_link 가 만드는 쿼리 이름(s,m,c,a)과 값 형식을 /go 가 받아들인다."""
    from urllib.parse import parse_qs, urlparse

    from studio.config import Config
    from studio.publish.utm import app_link

    cfg = Config.model_validate({"app": {"link_base": "https://ref.supabase.co/functions/v1/go"}})
    q = parse_qs(urlparse(app_link(cfg, platform="youtube", medium="description", week="2026-W42", asset_id="2026W42-L1")).query)
    src = (SUPA / "functions" / "go" / "logic.ts").read_text(encoding="utf-8")
    rules = dict(re.findall(r"^\s{2}([smca]): /(.+)/,$", src, re.MULTILINE))
    assert set(rules) == {"s", "m", "c", "a"} == set(q)
    for k, (v,) in q.items():
        assert re.fullmatch(rules[k], v.lower() if k in "sm" else v), f"/go 가 {k}={v} 를 버린다"
