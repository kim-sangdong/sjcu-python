-- 초기 스키마: 추적 링크 클릭·앱 설치·앱 이벤트·결제(RevenueCat)·OAuth 토큰 보관 + 공개 미디어 버킷.
--
-- 이 파일의 테이블·열 이름은 백엔드(supabase/functions)·분석(studio.analytics)·앱(app/)·토큰 보관(studio.tokens)이
-- 함께 쓰는 계약이다. 열 이름을 바꾸거나 지우지 말고, 바꿔야 하면 새 마이그레이션 파일로 추가한다.
--
-- 권한 요약
--   anon(앱에 들어가는 공개 키)  : app_installs·app_events 에 INSERT 만(정해진 열만). 읽기·수정·삭제 불가.
--   authenticated               : 아무 권한 없음(앱은 로그인 없이 anon 키만 쓴다).
--   service_role(서버 비밀 키)   : 전부(RLS 를 우회한다). Edge Function·파이썬 도구가 쓴다.
-- 남용 완화: 값 길이·형식 check 제약 + anon 은 id·created_at 을 직접 넣을 수 없게 열 단위 권한만 준다.

-- ---------------------------------------------------------------- link_clicks (Edge Function /go 가 기록)

create table if not exists public.link_clicks (
  id          bigserial primary key,
  created_at  timestamptz not null default now(),
  source      text,          -- utm_source  (예: youtube)
  medium      text,          -- utm_medium  (예: description, shorts)
  campaign    text,          -- utm_campaign(예: 2026-W42)
  content     text,          -- utm_content (콘텐츠 ID, 예: 2026W42-S03)
  os          text,          -- android | ios | other
  user_agent  text,
  referer     text,
  ip_hash     text,          -- sha256(IP_HASH_SALT|ip) 16진수. 원래 IP 는 저장하지 않는다
  constraint link_clicks_source_len     check (source     is null or char_length(source)     <= 64),
  constraint link_clicks_medium_len     check (medium     is null or char_length(medium)     <= 64),
  constraint link_clicks_campaign_len   check (campaign   is null or char_length(campaign)   <= 64),
  constraint link_clicks_content_len    check (content    is null or char_length(content)    <= 64),
  constraint link_clicks_os_len         check (os         is null or char_length(os)         <= 16),
  constraint link_clicks_user_agent_len check (user_agent is null or char_length(user_agent) <= 512),
  constraint link_clicks_referer_len    check (referer    is null or char_length(referer)    <= 1024),
  constraint link_clicks_ip_hash_len    check (ip_hash    is null or char_length(ip_hash)    <= 128)
);

comment on table public.link_clicks is '추적 링크 클릭(/functions/v1/go). service_role 만 쓰고 읽는다.';

create index if not exists link_clicks_created_at_idx on public.link_clicks (created_at);
create index if not exists link_clicks_content_idx    on public.link_clicks (content, created_at);

-- ---------------------------------------------------------------- app_installs (앱이 첫 실행에 한 번 기록)

create table if not exists public.app_installs (
  id            bigserial primary key,
  created_at    timestamptz not null default now(),
  app_user_id   text unique not null,
  os            text,
  utm_source    text,
  utm_medium    text,
  utm_campaign  text,
  utm_content   text,
  raw_referrer  text,
  app_version   text,
  constraint app_installs_app_user_id_len  check (char_length(app_user_id) between 1 and 200),
  constraint app_installs_os_len           check (os           is null or char_length(os)           <= 16),
  constraint app_installs_utm_source_len   check (utm_source   is null or char_length(utm_source)   <= 100),
  constraint app_installs_utm_medium_len   check (utm_medium   is null or char_length(utm_medium)   <= 100),
  constraint app_installs_utm_campaign_len check (utm_campaign is null or char_length(utm_campaign) <= 100),
  constraint app_installs_utm_content_len  check (utm_content  is null or char_length(utm_content)  <= 100),
  constraint app_installs_raw_referrer_len check (raw_referrer is null or char_length(raw_referrer) <= 1000),
  constraint app_installs_app_version_len  check (app_version  is null or char_length(app_version)  <= 50)
);

comment on table public.app_installs is '앱 설치(첫 실행). anon 은 INSERT 만. app_user_id 중복이면 409 — 앱은 이미 들어간 것으로 본다.';

create index if not exists app_installs_created_at_idx  on public.app_installs (created_at);
create index if not exists app_installs_utm_content_idx on public.app_installs (utm_content);
-- app_user_id 는 unique 제약이 인덱스를 만든다.

-- ---------------------------------------------------------------- app_events (앱 사용 이벤트)

create table if not exists public.app_events (
  id           bigserial primary key,
  created_at   timestamptz not null default now(),
  app_user_id  text not null,
  name         text not null,
  props        jsonb default '{}'::jsonb,
  constraint app_events_app_user_id_len check (char_length(app_user_id) between 1 and 200),
  constraint app_events_name_format     check (name ~ '^[A-Za-z0-9_.:-]{1,64}$'),
  constraint app_events_props_shape     check (props is null or (jsonb_typeof(props) = 'object' and octet_length(props::text) <= 8192))
);

comment on table public.app_events is '앱 이벤트(session_start, paywall_view, answers_batch 등). anon 은 INSERT 만. props 는 8KB 이하 객체.';

create index if not exists app_events_created_at_idx  on public.app_events (created_at);
create index if not exists app_events_app_user_id_idx on public.app_events (app_user_id, created_at);
create index if not exists app_events_name_idx        on public.app_events (name, created_at);

-- ---------------------------------------------------------------- purchases (RevenueCat 웹훅이 기록)

create table if not exists public.purchases (
  id               bigserial primary key,
  created_at       timestamptz not null default now(),
  event_id         text unique not null,   -- RevenueCat event.id (중복 수신 무시 기준)
  event_type       text,                   -- INITIAL_PURCHASE, RENEWAL, CANCELLATION ...
  app_user_id      text,
  product_id       text,
  period_type      text,                   -- TRIAL | INTRO | NORMAL | PROMOTIONAL | PREPAID
  price            numeric,                -- 구매 통화 기준 금액(event.price_in_purchased_currency)
  currency         text,                   -- 구매 통화(event.currency, 예: KRW)
  price_in_usd     numeric,                -- USD 금액(event.price)
  store            text,                   -- APP_STORE | PLAY_STORE | ...
  environment      text,                   -- SANDBOX | PRODUCTION
  event_timestamp  timestamptz,            -- event.event_timestamp_ms
  raw              jsonb,                  -- 받은 event 객체 전체(분석이 raw->>'is_trial_conversion' 등을 읽는다)
  constraint purchases_event_id_len    check (char_length(event_id) between 1 and 200),
  constraint purchases_event_type_len  check (event_type  is null or char_length(event_type)  <= 64),
  constraint purchases_app_user_id_len check (app_user_id is null or char_length(app_user_id) <= 200),
  constraint purchases_product_id_len  check (product_id  is null or char_length(product_id)  <= 200),
  constraint purchases_currency_len    check (currency    is null or char_length(currency)    <= 8)
);

comment on table public.purchases is 'RevenueCat 웹훅 이벤트. service_role 만 쓰고 읽는다.';

create index if not exists purchases_created_at_idx      on public.purchases (created_at);
create index if not exists purchases_app_user_id_idx     on public.purchases (app_user_id);
create index if not exists purchases_event_timestamp_idx on public.purchases (event_timestamp);

-- ---------------------------------------------------------------- oauth_tokens (회전하는 토큰 보관 — studio.tokens)

create table if not exists public.oauth_tokens (
  key         text primary key,            -- instagram | tiktok | facebook ...
  value       jsonb not null,
  updated_at  timestamptz default now()
);

comment on table public.oauth_tokens is '회전하는 OAuth 토큰(studio.tokens.SupabaseTokenStore). service_role 만 접근. 비밀값이므로 절대 공개하지 않는다.';

-- ---------------------------------------------------------------- 행 수준 보안(RLS)

alter table public.link_clicks  enable row level security;
alter table public.app_installs enable row level security;
alter table public.app_events   enable row level security;
alter table public.purchases    enable row level security;
alter table public.oauth_tokens enable row level security;

-- 기본 권한(Supabase 는 public 스키마의 새 테이블을 anon·authenticated 에 열어 둔다)을 먼저 모두 거둔다.
revoke all on table public.link_clicks, public.app_installs, public.app_events, public.purchases, public.oauth_tokens
  from anon, authenticated;
revoke all on sequence public.link_clicks_id_seq, public.app_installs_id_seq, public.app_events_id_seq, public.purchases_id_seq
  from anon, authenticated;

-- anon: 정해진 열에만 INSERT (id·created_at 은 서버 기본값만 쓰게 한다). 읽기 권한은 주지 않는다.
grant insert (app_user_id, os, utm_source, utm_medium, utm_campaign, utm_content, raw_referrer, app_version)
  on public.app_installs to anon;
grant insert (app_user_id, name, props)
  on public.app_events to anon;
grant usage on sequence public.app_installs_id_seq, public.app_events_id_seq to anon;   -- bigserial 기본값(nextval)에 필요

drop policy if exists app_installs_anon_insert on public.app_installs;
create policy app_installs_anon_insert on public.app_installs
  for insert to anon
  with check (true);

drop policy if exists app_events_anon_insert on public.app_events;
create policy app_events_anon_insert on public.app_events
  for insert to anon
  with check (true);

-- service_role: 전부. (RLS 는 service_role 의 BYPASSRLS 로 우회된다 — 별도 정책 불필요)
grant all on table public.link_clicks, public.app_installs, public.app_events, public.purchases, public.oauth_tokens
  to service_role;
grant usage, select on sequence public.link_clicks_id_seq, public.app_installs_id_seq, public.app_events_id_seq, public.purchases_id_seq
  to service_role;

-- ---------------------------------------------------------------- 스토리지: 공개 버킷 media
-- 객체 경로: <week>/<asset_id>/<파일명>  (studio.publish.storage), 문항 팩: bank/<version>/..., bank/latest/...
-- 공개 버킷이라 /storage/v1/object/public/media/<경로> 로 누구나 읽을 수 있다(인스타·페북이 가져갈 수 있게).
-- 업로드는 service_role 키로만 한다 — storage.objects 에 anon 쓰기 정책을 만들지 않는다.

insert into storage.buckets (id, name, public)
values ('media', 'media', true)
on conflict (id) do update set public = excluded.public;
