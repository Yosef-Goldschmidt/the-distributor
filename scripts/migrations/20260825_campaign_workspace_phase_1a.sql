-- Campaign Workspace Phase 1A: additive schema and transactional RPCs.
-- Definition only. Do not apply to a configured production project in Phase 1A.

create extension if not exists pgcrypto;

create table if not exists workspaces (
  id                text primary key default gen_random_uuid()::text,
  company_id        text references companies(id) on delete restrict,
  capability_digest text not null unique
                    check (capability_digest ~ '^[0-9a-f]{64}$'),
  display_name      text not null,
  created_at        timestamptz not null default now(),
  last_seen_at      timestamptz not null default now()
);

create table if not exists film_projects (
  id           text primary key default gen_random_uuid()::text,
  workspace_id text not null references workspaces(id) on delete restrict,
  title        text not null,
  profile_json jsonb not null,
  profile_hash text not null check (profile_hash ~ '^[0-9a-f]{64}$'),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create index if not exists film_projects_workspace_lookup
  on film_projects (workspace_id, created_at);

create table if not exists campaigns (
  id                         text primary key default gen_random_uuid()::text,
  film_project_id            text not null references film_projects(id) on delete restrict,
  lifecycle                  text not null default 'draft'
                             check (lifecycle in ('draft', 'active', 'post_premiere', 'closed')),
  version                    integer not null default 0 check (version >= 0),
  readiness                  text not null default 'needs_identity'
                             check (readiness in ('needs_identity', 'needs_premiere_clarification', 'ready', 'stale')),
  premiere_ledger_json       jsonb not null,
  ledger_hash                text not null check (ledger_hash ~ '^[0-9a-f]{64}$'),
  aggregate_hash             text not null check (aggregate_hash ~ '^[0-9a-f]{64}$'),
  active_strategy_version_id text,
  strategy_stale             boolean not null default true,
  created_at                 timestamptz not null default now(),
  updated_at                 timestamptz not null default now()
);

create unique index if not exists campaigns_one_open_per_film
  on campaigns (film_project_id)
  where lifecycle <> 'closed';

create table if not exists campaign_constraints (
  id           text primary key,
  campaign_id  text not null references campaigns(id) on delete restrict,
  type         text not null,
  strength     text not null check (strength in ('hard', 'preference')),
  payload_json jsonb not null,
  locked       boolean not null default false,
  active       boolean not null default true,
  source       text not null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (campaign_id, id)
);

create index if not exists campaign_constraints_active_lookup
  on campaign_constraints (campaign_id, active);

create table if not exists campaign_events (
  id                    text primary key,
  campaign_id           text not null references campaigns(id) on delete restrict,
  sequence_no           integer not null check (sequence_no >= 1),
  type                  text not null,
  payload_json          jsonb not null,
  actor                 jsonb not null,
  idempotency_key       text not null,
  before_aggregate_hash text not null check (before_aggregate_hash ~ '^[0-9a-f]{64}$'),
  after_aggregate_hash  text not null check (after_aggregate_hash ~ '^[0-9a-f]{64}$'),
  created_at            timestamptz not null default now(),
  unique (campaign_id, sequence_no),
  unique (campaign_id, idempotency_key)
);

create index if not exists campaign_events_ordered_lookup
  on campaign_events (campaign_id, sequence_no);

create table if not exists campaign_opportunities (
  id                      text primary key,
  campaign_id             text not null references campaigns(id) on delete restrict,
  festival_id             text not null references festivals(id) on delete restrict,
  submission_state        text not null default 'not_submitted'
                          check (submission_state in ('not_submitted', 'submitted', 'rejected', 'invited', 'withdrawn')),
  offer_state             text not null default 'none'
                          check (offer_state in ('none', 'pending', 'accepted', 'declined')),
  policy_state            text not null default 'normal'
                          check (policy_state in ('normal', 'locked', 'excluded')),
  evidence_json           jsonb not null,
  creative_scores_json    jsonb not null,
  risk_json               jsonb not null,
  verification_items_json jsonb not null default '[]'::jsonb,
  evidence_hash           text not null check (evidence_hash ~ '^[0-9a-f]{64}$'),
  creative_hash           text not null check (creative_hash ~ '^[0-9a-f]{64}$'),
  risk_hash               text not null check (risk_hash ~ '^[0-9a-f]{64}$'),
  updated_at              timestamptz not null default now(),
  unique (campaign_id, festival_id)
);

create index if not exists campaign_opportunities_campaign_lookup
  on campaign_opportunities (campaign_id, festival_id);

create table if not exists screenings (
  id              text primary key,
  campaign_id     text not null references campaigns(id) on delete restrict,
  opportunity_id  text references campaign_opportunities(id) on delete restrict,
  festival_id     text references festivals(id) on delete restrict,
  venue           text,
  exhibition_kind text,
  country         text,
  region          text,
  scheduled_at    timestamptz,
  occurred_at     timestamptz,
  state           text not null check (state in ('scheduled', 'occurred', 'cancelled')),
  access          text not null check (access in ('public', 'private', 'unknown')),
  source_refs     jsonb not null default '[]'::jsonb,
  evidence_status text not null default 'unknown'
                  check (evidence_status in ('confirmed', 'asserted', 'inferred', 'unknown', 'contradicted')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists screenings_campaign_chronology
  on screenings (campaign_id, occurred_at, scheduled_at, id);

create table if not exists strategy_versions (
  id                        text primary key,
  campaign_id               text not null references campaigns(id) on delete restrict,
  strategy_no               integer not null check (strategy_no >= 1),
  based_on_campaign_version integer not null check (based_on_campaign_version >= 0),
  outcome                   text not null check (outcome in ('ready', 'failed')),
  input_snapshot_json       jsonb not null,
  input_hash                text not null check (input_hash ~ '^[0-9a-f]{64}$'),
  plan_json                 jsonb,
  diff_json                 jsonb,
  trace_json                jsonb not null default '{}'::jsonb,
  reuse_manifest_json       jsonb not null default '{}'::jsonb,
  usage_json                jsonb not null default '{}'::jsonb,
  policy_versions           jsonb not null default '[]'::jsonb,
  model_versions            jsonb not null default '[]'::jsonb,
  error_json                jsonb,
  created_at                timestamptz not null default now(),
  unique (campaign_id, strategy_no),
  check (
    (outcome = 'ready' and plan_json is not null)
    or (outcome = 'failed' and plan_json is null)
  )
);

create index if not exists strategy_versions_campaign_lookup
  on strategy_versions (campaign_id, strategy_no desc);

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'campaigns_active_strategy_fk'
      and conrelid = 'campaigns'::regclass
  ) then
    alter table campaigns
      add constraint campaigns_active_strategy_fk
      foreign key (active_strategy_version_id)
      references strategy_versions(id)
      on delete restrict;
  end if;
end;
$$;

alter table workspaces             enable row level security;
alter table film_projects          enable row level security;
alter table campaigns              enable row level security;
alter table campaign_constraints   enable row level security;
alter table campaign_events        enable row level security;
alter table campaign_opportunities enable row level security;
alter table screenings             enable row level security;
alter table strategy_versions      enable row level security;

-- No browser policies are created. Campaign access is server-only.
revoke all on table workspaces from anon, authenticated;
revoke all on table film_projects from anon, authenticated;
revoke all on table campaigns from anon, authenticated;
revoke all on table campaign_constraints from anon, authenticated;
revoke all on table campaign_events from anon, authenticated;
revoke all on table campaign_opportunities from anon, authenticated;
revoke all on table screenings from anon, authenticated;
revoke all on table strategy_versions from anon, authenticated;

create or replace function campaign_reject_immutable_change()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'UPDATE'
     and tg_table_name = 'campaign_events'
     and current_setting('campaign.command_event_finalize', true) = 'on'
     and old.after_aggregate_hash = repeat('0', 64)
     and new.after_aggregate_hash <> repeat('0', 64)
     and (to_jsonb(new) - 'after_aggregate_hash') =
         (to_jsonb(old) - 'after_aggregate_hash') then
    return new;
  end if;
  raise exception using
    errcode = '55000',
    message = format('%I is append-only/immutable', tg_table_name);
end;
$$;

do $$
begin
  if not exists (
    select 1 from pg_trigger
    where tgname = 'campaign_events_append_only'
      and tgrelid = 'campaign_events'::regclass
  ) then
    create trigger campaign_events_append_only
    before update or delete on campaign_events
    for each row execute function campaign_reject_immutable_change();
  end if;
  if not exists (
    select 1 from pg_trigger
    where tgname = 'strategy_versions_immutable'
      and tgrelid = 'strategy_versions'::regclass
  ) then
    create trigger strategy_versions_immutable
    before update or delete on strategy_versions
    for each row execute function campaign_reject_immutable_change();
  end if;
end;
$$;

-- Match app.campaign.contracts.canonical_json exactly for aggregate hashes.
-- jsonb::text includes structural spaces and cannot be hashed directly.
create or replace function campaign_canonical_json(p_value jsonb)
returns text
language plpgsql
immutable
strict
parallel safe
as $$
declare
  v_result text;
begin
  if jsonb_typeof(p_value) = 'object' then
    select '{' || coalesce(
      string_agg(
        to_jsonb(item.key)::text || ':' || campaign_canonical_json(item.value),
        ',' order by item.key collate "C"
      ),
      ''
    ) || '}'
    into v_result
    from jsonb_each(p_value) item;
    return v_result;
  end if;
  if jsonb_typeof(p_value) = 'array' then
    select '[' || coalesce(
      string_agg(campaign_canonical_json(item.value), ',' order by item.ordinality),
      ''
    ) || ']'
    into v_result
    from jsonb_array_elements(p_value) with ordinality item(value, ordinality);
    return v_result;
  end if;
  return p_value::text;
end;
$$;

create or replace function campaign_utc_text(p_value timestamptz)
returns text
language sql
immutable
strict
parallel safe
as $$
  select case
    when date_trunc('second', p_value) = p_value then
      to_char(p_value at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    else
      to_char(p_value at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')
  end;
$$;

create or replace function campaign_snapshot_json(
  p_workspace_id text,
  p_campaign_id text
)
returns jsonb
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select jsonb_build_object(
    'schema_version', 1,
    'workspace_id', fp.workspace_id,
    'campaign_id', c.id,
    'campaign_version', c.version,
    'lifecycle', c.lifecycle,
    'readiness', c.readiness,
    'profile', fp.profile_json,
    'premiere_ledger', c.premiere_ledger_json,
    'screenings', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'screening_id', s.id,
          'festival_id', s.festival_id,
          'state', s.state,
          'access', s.access,
          'country', s.country,
          'region', s.region,
          'scheduled_at', case
            when s.scheduled_at is null then null
            else campaign_utc_text(s.scheduled_at)
          end,
          'occurred_at', case
            when s.occurred_at is null then null
            else campaign_utc_text(s.occurred_at)
          end,
          'source_refs', s.source_refs
        ) order by coalesce(s.occurred_at, s.scheduled_at), s.id
      )
      from screenings s
      where s.campaign_id = c.id
    ), '[]'::jsonb),
    'constraints', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'constraint_id', cc.id,
          'constraint_type', cc.type,
          'strength', cc.strength,
          'value', cc.payload_json -> 'value',
          'locked', cc.locked,
          'active', cc.active,
          'candidate_expanding', coalesce((cc.payload_json ->> 'candidate_expanding')::boolean, false),
          'source_ref', cc.source
        ) order by cc.id
      )
      from campaign_constraints cc
      where cc.campaign_id = c.id
    ), '[]'::jsonb),
    'opportunities', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'opportunity_id', co.id,
          'festival_id', co.festival_id,
          'submission_state', co.submission_state,
          'offer_state', co.offer_state,
          'policy_state', co.policy_state,
          'verification_items', co.verification_items_json
        ) order by
          coalesce((co.evidence_json -> 'retrieved' ->> 'retrieval_rank')::integer, 2147483647),
          co.festival_id
      )
      from campaign_opportunities co
      where co.campaign_id = c.id
    ), '[]'::jsonb),
    'candidates', coalesce(
      (
        select sv.input_snapshot_json -> 'candidates'
        from strategy_versions sv
        where sv.id = c.active_strategy_version_id
      ),
      (
        select jsonb_agg(
          co.evidence_json order by
            coalesce((co.evidence_json -> 'retrieved' ->> 'retrieval_rank')::integer, 2147483647),
            co.festival_id
        )
        from campaign_opportunities co
        where co.campaign_id = c.id
      ),
      '[]'::jsonb
    ),
    'active_strategy_ref', c.active_strategy_version_id,
    'aggregate_hash', c.aggregate_hash
  )
  from campaigns c
  join film_projects fp on fp.id = c.film_project_id
  where c.id = p_campaign_id
    and fp.workspace_id = p_workspace_id;
$$;

create or replace function get_campaign_aggregate(
  p_workspace_id text,
  p_campaign_id text
)
returns jsonb
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
declare
  v_snapshot jsonb;
  v_stale boolean;
begin
  v_snapshot := campaign_snapshot_json(p_workspace_id, p_campaign_id);
  if v_snapshot is null then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;

  select c.strategy_stale into v_stale
  from campaigns c
  join film_projects fp on fp.id = c.film_project_id
  where c.id = p_campaign_id and fp.workspace_id = p_workspace_id;

  return jsonb_build_object(
    'snapshot', v_snapshot,
    'events', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'event_id', ce.id,
          'campaign_id', ce.campaign_id,
          'sequence_no', ce.sequence_no,
          'type', ce.type,
          'command', ce.payload_json,
          'before_aggregate_hash', ce.before_aggregate_hash,
          'after_aggregate_hash', ce.after_aggregate_hash,
          'occurred_at', ce.created_at
        ) order by ce.sequence_no
      )
      from campaign_events ce where ce.campaign_id = p_campaign_id
    ), '[]'::jsonb),
    'strategy_versions', coalesce((
      select jsonb_agg(
        jsonb_build_object(
          'strategy_id', sv.id,
          'strategy_no', sv.strategy_no,
          'based_on_campaign_version', sv.based_on_campaign_version,
          'outcome', sv.outcome,
          'input_snapshot_json', sv.input_snapshot_json,
          'input_hash', sv.input_hash,
          'plan_json', sv.plan_json,
          'diff_json', sv.diff_json,
          'trace_json', sv.trace_json,
          'reuse_manifest_json', sv.reuse_manifest_json,
          'usage_json', sv.usage_json,
          'policy_versions', sv.policy_versions,
          'model_versions', sv.model_versions,
          'error_json', sv.error_json,
          'created_at', sv.created_at
        ) order by sv.strategy_no
      )
      from strategy_versions sv where sv.campaign_id = p_campaign_id
    ), '[]'::jsonb),
    'strategy_stale', v_stale
  );
end;
$$;

create or replace function create_campaign_from_snapshot(
  p_workspace_id text,
  p_snapshot jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_campaign_id text := p_snapshot ->> 'campaign_id';
  v_film_project_id text := 'film:' || (p_snapshot ->> 'campaign_id');
begin
  if not exists (select 1 from workspaces where id = p_workspace_id) then
    raise exception using errcode = 'P0002', message = 'workspace_not_found';
  end if;
  if p_snapshot ->> 'workspace_id' is distinct from p_workspace_id
     or coalesce((p_snapshot ->> 'campaign_version')::integer, -1) <> 0
     or jsonb_typeof(p_snapshot -> 'candidates') is distinct from 'array'
     or jsonb_typeof(p_snapshot -> 'opportunities') is distinct from 'array'
     or jsonb_array_length(p_snapshot -> 'candidates') < 1
     or jsonb_array_length(p_snapshot -> 'candidates') > 12
     or jsonb_array_length(p_snapshot -> 'candidates')
        <> jsonb_array_length(p_snapshot -> 'opportunities') then
    raise exception using errcode = '22023', message = 'invalid_initial_campaign_snapshot';
  end if;

  insert into film_projects (
    id, workspace_id, title, profile_json, profile_hash
  ) values (
    v_film_project_id,
    p_workspace_id,
    coalesce(p_snapshot -> 'profile' -> 'title' ->> 'value', 'Untitled film'),
    p_snapshot -> 'profile',
    p_snapshot -> 'profile' ->> 'profile_hash'
  );

  insert into campaigns (
    id, film_project_id, lifecycle, version, readiness,
    premiere_ledger_json, ledger_hash, aggregate_hash,
    active_strategy_version_id, strategy_stale
  ) values (
    v_campaign_id,
    v_film_project_id,
    p_snapshot ->> 'lifecycle',
    0,
    p_snapshot ->> 'readiness',
    p_snapshot -> 'premiere_ledger',
    p_snapshot -> 'premiere_ledger' ->> 'ledger_hash',
    p_snapshot ->> 'aggregate_hash',
    null,
    true
  );

  insert into campaign_opportunities (
    id, campaign_id, festival_id, submission_state, offer_state,
    policy_state, evidence_json, creative_scores_json, risk_json,
    verification_items_json, evidence_hash, creative_hash, risk_hash
  )
  select
    opportunity.value ->> 'opportunity_id',
    v_campaign_id,
    candidate.value ->> 'festival_id',
    opportunity.value ->> 'submission_state',
    opportunity.value ->> 'offer_state',
    opportunity.value ->> 'policy_state',
    candidate.value,
    candidate.value -> 'creative',
    candidate.value -> 'risk',
    coalesce(opportunity.value -> 'verification_items', '[]'::jsonb),
    candidate.value ->> 'component_hash',
    candidate.value -> 'creative' ->> 'creative_key',
    candidate.value -> 'risk' ->> 'risk_key'
  from jsonb_array_elements(p_snapshot -> 'candidates') candidate
  join jsonb_array_elements(p_snapshot -> 'opportunities') opportunity
    on opportunity.value ->> 'festival_id' = candidate.value ->> 'festival_id';

  insert into campaign_constraints (
    id, campaign_id, type, strength, payload_json, locked, active, source
  )
  select
    item.value ->> 'constraint_id', v_campaign_id,
    item.value ->> 'constraint_type', item.value ->> 'strength',
    jsonb_build_object(
      'value', item.value -> 'value',
      'candidate_expanding', item.value -> 'candidate_expanding'
    ),
    (item.value ->> 'locked')::boolean,
    (item.value ->> 'active')::boolean,
    item.value ->> 'source_ref'
  from jsonb_array_elements(coalesce(p_snapshot -> 'constraints', '[]'::jsonb)) item;

  insert into screenings (
    id, campaign_id, festival_id, country, region, scheduled_at,
    occurred_at, state, access, source_refs, evidence_status
  )
  select
    item.value ->> 'screening_id', v_campaign_id,
    nullif(item.value ->> 'festival_id', ''),
    item.value ->> 'country', item.value ->> 'region',
    nullif(item.value ->> 'scheduled_at', '')::timestamptz,
    nullif(item.value ->> 'occurred_at', '')::timestamptz,
    item.value ->> 'state', item.value ->> 'access',
    coalesce(item.value -> 'source_refs', '[]'::jsonb), 'asserted'
  from jsonb_array_elements(coalesce(p_snapshot -> 'screenings', '[]'::jsonb)) item;

  return get_campaign_aggregate(p_workspace_id, v_campaign_id);
end;
$$;

create or replace function rederive_campaign_premiere_ledger(p_campaign_id text)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_profile jsonb;
  v_existing jsonb;
  v_film_country text;
  v_country_confirmed boolean;
  v_unscreened boolean;
  v_assertion_at timestamptz;
  v_assertion_refs jsonb;
  v_public_count integer;
  v_public_before_assertion integer;
  v_foreign_count integer;
  v_foreign_before_assertion integer;
  v_unknown_country_count integer;
  v_unknown_access_count integer;
  v_contradicted_count integer;
  v_public_refs jsonb;
  v_unresolved_refs jsonb;
  v_scopes jsonb := '[]'::jsonb;
  v_world jsonb;
  v_international jsonb;
  v_scope jsonb;
  v_scope_name text;
  v_territory text;
  v_matching integer;
  v_unknown_location integer;
  v_input_hash text;
  v_ledger jsonb;
begin
  select fp.profile_json, c.premiere_ledger_json
    into v_profile, v_existing
  from campaigns c
  join film_projects fp on fp.id = c.film_project_id
  where c.id = p_campaign_id;

  if v_profile is null then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;

  v_country_confirmed := v_profile #>> '{country,status}' = 'confirmed';
  v_film_country := lower(v_profile #>> '{country,value}');

  select
    coalesce(bool_or(
      assertion ->> 'status' in ('confirmed', 'asserted')
      and coalesce(assertion ->> 'value', '') ~* '(no prior public|never.*screen|world premiere.*available|unscreened)'
    ), false),
    max((assertion ->> 'observed_at')::timestamptz) filter (
      where assertion ->> 'status' in ('confirmed', 'asserted')
      and coalesce(assertion ->> 'value', '') ~* '(no prior public|never.*screen|world premiere.*available|unscreened)'
    )
  into v_unscreened, v_assertion_at
  from jsonb_array_elements(coalesce(v_profile -> 'premiere_assertions', '[]'::jsonb)) assertion;

  select coalesce(
    jsonb_agg(to_jsonb(source_ref.value) order by assertion ->> 'observed_at', source_ref.value),
    '[]'::jsonb
  )
  into v_assertion_refs
  from jsonb_array_elements(coalesce(v_profile -> 'premiere_assertions', '[]'::jsonb)) assertion
  cross join lateral jsonb_array_elements_text(
    coalesce(assertion -> 'source_refs', '[]'::jsonb)
  ) source_ref(value)
  where assertion ->> 'status' in ('confirmed', 'asserted')
    and coalesce(assertion ->> 'value', '') ~* '(no prior public|never.*screen|world premiere.*available|unscreened)';

  select
    count(*) filter (
      where state = 'occurred' and access = 'public'
        and occurred_at is not null and evidence_status = 'confirmed'
    ),
    count(*) filter (
      where state = 'occurred' and access = 'public'
        and occurred_at is not null and evidence_status = 'confirmed'
        and v_assertion_at is not null and occurred_at <= v_assertion_at
    ),
    count(*) filter (
      where state = 'occurred' and access = 'public'
        and occurred_at is not null and evidence_status = 'confirmed'
        and v_country_confirmed and country is not null
        and lower(country) <> v_film_country
    ),
    count(*) filter (
      where state = 'occurred' and access = 'public'
        and occurred_at is not null and evidence_status = 'confirmed'
        and v_country_confirmed and country is not null
        and lower(country) <> v_film_country
        and v_assertion_at is not null and occurred_at <= v_assertion_at
    ),
    count(*) filter (
      where state = 'occurred' and access = 'public'
        and occurred_at is not null and evidence_status = 'confirmed'
        and country is null
    ),
    count(*) filter (where state = 'occurred' and access = 'unknown'),
    count(*) filter (where evidence_status = 'contradicted')
  into
    v_public_count,
    v_public_before_assertion,
    v_foreign_count,
    v_foreign_before_assertion,
    v_unknown_country_count,
    v_unknown_access_count,
    v_contradicted_count
  from screenings
  where campaign_id = p_campaign_id;

  select coalesce(
    jsonb_agg(to_jsonb(source_ref.value) order by s.occurred_at, s.id, source_ref.value),
    '[]'::jsonb
  )
  into v_public_refs
  from screenings s
  cross join lateral jsonb_array_elements_text(s.source_refs) source_ref(value)
  where s.campaign_id = p_campaign_id
    and s.state = 'occurred' and s.access = 'public'
    and s.occurred_at is not null and s.evidence_status = 'confirmed';

  select coalesce(
    jsonb_agg(to_jsonb(source_ref.value) order by s.occurred_at, s.id, source_ref.value),
    '[]'::jsonb
  )
  into v_unresolved_refs
  from screenings s
  cross join lateral jsonb_array_elements_text(s.source_refs) source_ref(value)
  where s.campaign_id = p_campaign_id
    and s.state = 'occurred'
    and (
      s.access = 'unknown'
      or (s.access = 'public' and (s.occurred_at is null or s.evidence_status <> 'confirmed'))
    );

  if v_contradicted_count > 0 or (v_unscreened and v_public_before_assertion > 0) then
    v_world := jsonb_build_object(
      'scope', 'world', 'territory', null, 'availability', 'unknown',
      'contradiction', true,
      'reason_code', 'unscreened_assertion_conflicts_with_public_occurrence',
      'evidence_refs', v_assertion_refs || v_public_refs
    );
  elsif v_public_count > 0 then
    v_world := jsonb_build_object(
      'scope', 'world', 'territory', null, 'availability', 'consumed',
      'contradiction', false, 'reason_code', 'confirmed_occurred_public',
      'evidence_refs', v_public_refs
    );
  elsif v_unknown_access_count > 0 then
    v_world := jsonb_build_object(
      'scope', 'world', 'territory', null, 'availability', 'unknown',
      'contradiction', false, 'reason_code', 'screening_access_verify',
      'evidence_refs', v_unresolved_refs
    );
  elsif v_unscreened then
    v_world := jsonb_build_object(
      'scope', 'world', 'territory', null, 'availability', 'available',
      'contradiction', false, 'reason_code', 'sourced_unscreened_assertion',
      'evidence_refs', v_assertion_refs
    );
  else
    v_world := jsonb_build_object(
      'scope', 'world', 'territory', null, 'availability', 'unknown',
      'contradiction', false, 'reason_code', 'absence_is_not_availability',
      'evidence_refs', '[]'::jsonb
    );
  end if;

  if v_contradicted_count > 0 or (v_unscreened and v_foreign_before_assertion > 0) then
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'unknown',
      'contradiction', true,
      'reason_code', 'unscreened_assertion_conflicts_with_foreign_public_occurrence',
      'evidence_refs', v_assertion_refs || v_public_refs
    );
  elsif v_public_count > 0 and not v_country_confirmed then
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'unknown',
      'contradiction', false, 'reason_code', 'film_country_unknown',
      'evidence_refs', v_public_refs
    );
  elsif v_foreign_count > 0 then
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'consumed',
      'contradiction', false, 'reason_code', 'confirmed_foreign_public',
      'evidence_refs', v_public_refs
    );
  elsif v_unknown_country_count > 0 or v_unknown_access_count > 0 then
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'unknown',
      'contradiction', false, 'reason_code', 'screening_country_or_access_verify',
      'evidence_refs', v_public_refs || v_unresolved_refs
    );
  elsif v_unscreened then
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'available',
      'contradiction', false,
      'reason_code', case when v_public_count > 0 then 'domestic_public_only' else 'sourced_unscreened_assertion' end,
      'evidence_refs', v_assertion_refs || v_public_refs || v_unresolved_refs
    );
  else
    v_international := jsonb_build_object(
      'scope', 'international', 'territory', null, 'availability', 'unknown',
      'contradiction', false, 'reason_code', 'international_history_unsupported',
      'evidence_refs', v_public_refs
    );
  end if;

  v_scopes := jsonb_build_array(v_world, v_international);

  for v_scope in
    select value
    from jsonb_array_elements(coalesce(v_existing -> 'scopes', '[]'::jsonb))
    where value ->> 'scope' in ('continental', 'territorial')
  loop
    v_scope_name := v_scope ->> 'scope';
    v_territory := v_scope ->> 'territory';
    select
      count(*) filter (
        where state = 'occurred' and access = 'public'
          and occurred_at is not null and evidence_status = 'confirmed'
          and (
            (v_scope_name = 'territorial' and lower(coalesce(country, '')) = lower(v_territory))
            or
            (v_scope_name = 'continental' and lower(coalesce(region, '')) like '%' || lower(v_territory) || '%')
          )
      ),
      count(*) filter (
        where state = 'occurred' and access = 'public'
          and occurred_at is not null and evidence_status = 'confirmed'
          and (
            (v_scope_name = 'territorial' and country is null)
            or (v_scope_name = 'continental' and region is null)
          )
      )
    into v_matching, v_unknown_location
    from screenings where campaign_id = p_campaign_id;

    v_scopes := v_scopes || jsonb_build_array(jsonb_build_object(
      'scope', v_scope_name,
      'territory', v_territory,
      'availability', case
        when v_contradicted_count > 0 then 'unknown'
        when v_matching > 0 then 'consumed'
        when v_unknown_location > 0 or v_unknown_access_count > 0 then 'unknown'
        when v_unscreened then 'available'
        else 'unknown'
      end,
      'contradiction', v_contradicted_count > 0,
      'reason_code', case
        when v_contradicted_count > 0 then 'contradictory_screening_evidence'
        when v_matching > 0 and v_scope_name = 'continental' then 'confirmed_continental_public'
        when v_matching > 0 then 'confirmed_territorial_public'
        when v_unknown_location > 0 or v_unknown_access_count > 0 then 'territorial_scope_unresolved'
        when v_unscreened then 'sourced_unscreened_assertion'
        else 'territorial_history_unsupported'
      end,
      'evidence_refs', v_assertion_refs || v_public_refs
    ));
  end loop;

  select encode(digest(convert_to(
    v_profile::text || coalesce(jsonb_agg(to_jsonb(s) order by s.id)::text, '[]'),
    'UTF8'
  ), 'sha256'), 'hex')
  into v_input_hash
  from screenings s where s.campaign_id = p_campaign_id;

  v_ledger := jsonb_build_object(
    'schema_version', 1,
    'scopes', v_scopes,
    'derivation_policy_version', 'premiere-ledger-v1',
    'input_hash', v_input_hash,
    'ledger_hash', repeat('0', 64)
  );
  v_ledger := jsonb_set(
    v_ledger,
    '{ledger_hash}',
    to_jsonb(encode(digest(convert_to(v_ledger::text, 'UTF8'), 'sha256'), 'hex'))
  );
  return v_ledger;
end;
$$;

create or replace function apply_campaign_command(
  p_workspace_id text,
  p_campaign_id text,
  p_expected_version integer,
  p_idempotency_key text,
  p_command jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_campaign campaigns%rowtype;
  v_workspace text;
  v_existing campaign_events%rowtype;
  v_prior campaign_events%rowtype;
  v_type text := p_command ->> 'type';
  v_payload jsonb := p_command -> 'payload';
  v_before_hash text;
  v_after_hash text;
  v_sequence integer;
  v_event_id text;
  v_rows integer;
  v_constraint jsonb;
  v_fact_key text;
  v_screening_id text;
  v_replacement jsonb;
  v_ledger jsonb;
  v_snapshot jsonb;
  v_event jsonb;
begin
  -- 1. Resolve and lock campaign.
  select * into v_campaign from campaigns where id = p_campaign_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;

  -- 2. Workspace scope check after the row lock; campaign IDs are not authority.
  select fp.workspace_id into v_workspace
  from film_projects fp where fp.id = v_campaign.film_project_id;
  if v_workspace is distinct from p_workspace_id then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;

  if p_command ->> 'idempotency_key' is distinct from p_idempotency_key
     or coalesce((p_command ->> 'expected_version')::integer, -1) <> p_expected_version
     or p_command #>> '{actor,kind}' <> 'human' then
    raise exception using errcode = '22023', message = 'invalid_command_envelope';
  end if;

  -- 3. Expected-version check. An exact committed retry is allowed to proceed
  -- to the idempotency result instead of creating a stale-version conflict.
  if v_campaign.version <> p_expected_version then
    select * into v_existing
    from campaign_events
    where campaign_id = p_campaign_id and idempotency_key = p_idempotency_key;
    if found then
      if v_existing.payload_json <> p_command then
        raise exception using errcode = '23505', message = 'idempotency_conflict';
      end if;
      return jsonb_build_object(
          'aggregate', get_campaign_aggregate(p_workspace_id, p_campaign_id),
          'event', jsonb_build_object(
            'event_id', v_existing.id,
            'campaign_id', v_existing.campaign_id,
            'sequence_no', v_existing.sequence_no,
            'type', v_existing.type,
            'command', v_existing.payload_json,
            'before_aggregate_hash', v_existing.before_aggregate_hash,
            'after_aggregate_hash', v_existing.after_aggregate_hash,
            'occurred_at', v_existing.created_at
          ),
          'idempotent_replay', true
        );
    end if;
    raise exception using
      errcode = '40001',
      message = 'version_conflict',
      detail = v_campaign.version::text;
  end if;

  -- 4. Idempotency check at the current version.
  select * into v_existing
  from campaign_events
  where campaign_id = p_campaign_id and idempotency_key = p_idempotency_key;
  if found then
    if v_existing.payload_json <> p_command then
      raise exception using errcode = '23505', message = 'idempotency_conflict';
    end if;
    return jsonb_build_object(
      'aggregate', get_campaign_aggregate(p_workspace_id, p_campaign_id),
      'event', jsonb_build_object(
        'event_id', v_existing.id,
        'campaign_id', v_existing.campaign_id,
        'sequence_no', v_existing.sequence_no,
        'type', v_existing.type,
        'command', v_existing.payload_json,
        'before_aggregate_hash', v_existing.before_aggregate_hash,
        'after_aggregate_hash', v_existing.after_aggregate_hash,
        'occurred_at', v_existing.created_at
      ),
      'idempotent_replay', true
    );
  end if;

  if v_campaign.lifecycle = 'closed' then
    raise exception using errcode = '55000', message = 'campaign_closed';
  end if;

  if v_type not in (
    'update_profile_fact', 'set_constraint', 'remove_constraint',
    'lock_opportunity', 'unlock_opportunity', 'exclude_opportunity',
    'include_opportunity', 'mark_submitted', 'record_rejection',
    'record_invitation', 'accept_offer', 'decline_offer', 'withdraw',
    'schedule_screening', 'confirm_screening', 'cancel_screening',
    'verify_opportunity_fact', 'correct_record', 'close_campaign'
  ) then
    raise exception using errcode = '22023', message = 'unknown_command_type';
  end if;

  v_before_hash := v_campaign.aggregate_hash;

  select coalesce(max(sequence_no), 0) + 1 into v_sequence
  from campaign_events where campaign_id = p_campaign_id;
  v_event_id := 'event:' || p_campaign_id || ':' || lpad(v_sequence::text, 6, '0');

  -- 6. Append the sole event before projection work. Its after hash is
  -- finalized inside this transaction after the authoritative projection hash
  -- is known; the immutable trigger permits only that one internal field fill.
  insert into campaign_events (
    id, campaign_id, sequence_no, type, payload_json, actor,
    idempotency_key, before_aggregate_hash, after_aggregate_hash
  ) values (
    v_event_id, p_campaign_id, v_sequence,
    case v_type
      when 'update_profile_fact' then 'profile_fact_updated'
      when 'set_constraint' then 'constraint_set'
      when 'remove_constraint' then 'constraint_removed'
      when 'lock_opportunity' then 'opportunity_locked'
      when 'unlock_opportunity' then 'opportunity_unlocked'
      when 'exclude_opportunity' then 'opportunity_excluded'
      when 'include_opportunity' then 'opportunity_included'
      when 'mark_submitted' then 'submission_marked'
      when 'record_rejection' then 'rejection_recorded'
      when 'record_invitation' then 'invitation_recorded'
      when 'accept_offer' then 'offer_accepted'
      when 'decline_offer' then 'offer_declined'
      when 'withdraw' then 'opportunity_withdrawn'
      when 'schedule_screening' then 'screening_scheduled'
      when 'confirm_screening' then 'screening_confirmed'
      when 'cancel_screening' then 'screening_cancelled'
      when 'verify_opportunity_fact' then 'opportunity_fact_verified'
      when 'correct_record' then 'record_corrected'
      when 'close_campaign' then 'campaign_closed'
    end,
    p_command, p_command -> 'actor', p_idempotency_key,
    v_before_hash, repeat('0', 64)
  );

  -- 5 and 7. Validate the transition and update only its current projection.
  if v_type = 'update_profile_fact' then
    v_fact_key := v_payload ->> 'fact_key';
    if v_fact_key in ('title', 'synopsis', 'format', 'country', 'themes', 'runtime_minutes') then
      update film_projects
      set profile_json = jsonb_set(profile_json, array[v_fact_key], v_payload -> 'fact', true),
          title = case when v_fact_key = 'title' then v_payload #>> '{fact,value}' else title end,
          updated_at = now()
      where id = v_campaign.film_project_id;
    elsif v_fact_key = 'premiere_assertion' then
      update film_projects
      set profile_json = jsonb_set(
            profile_json,
            '{premiere_assertions}',
            coalesce(profile_json -> 'premiere_assertions', '[]'::jsonb) || jsonb_build_array(v_payload -> 'fact'),
            true
          ),
          updated_at = now()
      where id = v_campaign.film_project_id;
    elsif v_fact_key = 'target_region' and v_payload #>> '{fact,value}' is not null then
      update film_projects
      set profile_json = jsonb_set(
            profile_json,
            '{target_regions}',
            coalesce(profile_json -> 'target_regions', '[]'::jsonb)
              || jsonb_build_array(v_payload #>> '{fact,value}'),
            true
          ),
          updated_at = now()
      where id = v_campaign.film_project_id;
    else
      raise exception using errcode = '22023', message = 'invalid_profile_fact';
    end if;
    update film_projects
    set profile_hash = encode(digest(convert_to((profile_json - 'profile_hash')::text, 'UTF8'), 'sha256'), 'hex'),
        profile_json = jsonb_set(
          profile_json,
          '{profile_hash}',
          to_jsonb(encode(digest(convert_to((profile_json - 'profile_hash')::text, 'UTF8'), 'sha256'), 'hex')),
          true
        )
    where id = v_campaign.film_project_id;

  elsif v_type = 'set_constraint' then
    v_constraint := v_payload -> 'constraint';
    if coalesce((v_constraint ->> 'active')::boolean, true) = false then
      raise exception using errcode = '55000', message = 'constraint_deactivation_requires_remove';
    end if;
    if exists (
      select 1 from campaign_constraints
      where id = v_constraint ->> 'constraint_id'
        and campaign_id = p_campaign_id and locked
        and payload_json <> v_constraint
    ) then
      raise exception using errcode = '55000', message = 'locked_constraint';
    end if;
    insert into campaign_constraints (
      id, campaign_id, type, strength, payload_json, locked, active, source
    ) values (
      v_constraint ->> 'constraint_id', p_campaign_id,
      v_constraint ->> 'constraint_type', v_constraint ->> 'strength',
      v_constraint,
      coalesce((v_constraint ->> 'locked')::boolean, false), true,
      v_constraint ->> 'source_ref'
    )
    on conflict (id) do update set
      type = excluded.type,
      strength = excluded.strength,
      payload_json = excluded.payload_json,
      locked = excluded.locked,
      active = true,
      source = excluded.source,
      updated_at = now()
    where campaign_constraints.campaign_id = p_campaign_id;

  elsif v_type = 'remove_constraint' then
    update campaign_constraints
    set active = false, locked = false, updated_at = now()
    where campaign_id = p_campaign_id
      and id = v_payload ->> 'constraint_id'
      and active
      and (not locked or coalesce((v_payload ->> 'explicit_unlock')::boolean, false));
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'constraint_not_removable';
    end if;

  elsif v_type in ('lock_opportunity', 'unlock_opportunity', 'exclude_opportunity', 'include_opportunity') then
    update campaign_opportunities
    set policy_state = case v_type
          when 'lock_opportunity' then 'locked'
          when 'unlock_opportunity' then 'normal'
          when 'exclude_opportunity' then 'excluded'
          else 'normal'
        end,
        updated_at = now()
    where campaign_id = p_campaign_id
      and festival_id = v_payload ->> 'festival_id'
      and policy_state = case v_type
          when 'lock_opportunity' then 'normal'
          when 'unlock_opportunity' then 'locked'
          when 'exclude_opportunity' then 'normal'
          else 'excluded'
        end;
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_opportunity_policy_transition';
    end if;

  elsif v_type = 'mark_submitted' then
    update campaign_opportunities
    set submission_state = 'submitted', updated_at = now()
    where campaign_id = p_campaign_id
      and festival_id = v_payload ->> 'festival_id'
      and submission_state = 'not_submitted'
      and policy_state <> 'excluded';
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_submission_transition';
    end if;

  elsif v_type = 'record_rejection' then
    update campaign_opportunities
    set submission_state = 'rejected', offer_state = 'none', updated_at = now()
    where campaign_id = p_campaign_id
      and festival_id = v_payload ->> 'festival_id'
      and (
        submission_state = 'submitted'
        or exists (
          select 1 from jsonb_array_elements_text(coalesce(v_payload -> 'source_refs', '[]'::jsonb)) source_ref
          where source_ref like 'import:%'
        )
      );
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_rejection_transition';
    end if;

  elsif v_type = 'record_invitation' then
    update campaign_opportunities
    set submission_state = 'invited', offer_state = 'pending', updated_at = now()
    where campaign_id = p_campaign_id
      and festival_id = v_payload ->> 'festival_id'
      and submission_state in ('not_submitted', 'submitted')
      and policy_state <> 'excluded';
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_invitation_transition';
    end if;

  elsif v_type in ('accept_offer', 'decline_offer') then
    update campaign_opportunities
    set offer_state = case when v_type = 'accept_offer' then 'accepted' else 'declined' end,
        updated_at = now()
    where campaign_id = p_campaign_id
      and festival_id = v_payload ->> 'festival_id'
      and submission_state = 'invited' and offer_state = 'pending';
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_offer_transition';
    end if;

  elsif v_type = 'withdraw' then
    update campaign_opportunities co
    set submission_state = 'withdrawn', updated_at = now()
    where co.campaign_id = p_campaign_id
      and co.festival_id = v_payload ->> 'festival_id'
      and (co.submission_state in ('submitted', 'invited') or co.offer_state = 'accepted')
      and not exists (
        select 1 from screenings s
        where s.campaign_id = p_campaign_id
          and s.festival_id = co.festival_id and s.state = 'occurred'
      );
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_withdrawal_transition';
    end if;

  elsif v_type = 'schedule_screening' then
    insert into screenings (
      id, campaign_id, opportunity_id, festival_id, venue, country, region,
      scheduled_at, state, access, source_refs, evidence_status
    ) values (
      v_payload ->> 'screening_id', p_campaign_id,
      (select id from campaign_opportunities
       where campaign_id = p_campaign_id and festival_id = v_payload ->> 'festival_id'),
      nullif(v_payload ->> 'festival_id', ''), v_payload ->> 'venue',
      v_payload ->> 'country', v_payload ->> 'region',
      (v_payload ->> 'scheduled_at')::timestamptz, 'scheduled',
      coalesce(v_payload ->> 'access', 'unknown'),
      coalesce(v_payload -> 'source_refs', '[]'::jsonb),
      case when jsonb_array_length(coalesce(v_payload -> 'source_refs', '[]'::jsonb)) > 0
        then 'asserted' else 'unknown' end
    );

  elsif v_type = 'confirm_screening' then
    update screenings
    set state = 'occurred',
        access = v_payload ->> 'access',
        country = coalesce(v_payload ->> 'country', country),
        region = coalesce(v_payload ->> 'region', region),
        occurred_at = (v_payload ->> 'occurred_at')::timestamptz,
        source_refs = v_payload -> 'source_refs',
        evidence_status = 'confirmed',
        updated_at = now()
    where campaign_id = p_campaign_id
      and id = v_payload ->> 'screening_id' and state = 'scheduled';
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_screening_confirmation';
    end if;

  elsif v_type = 'cancel_screening' then
    update screenings
    set state = 'cancelled', updated_at = now()
    where campaign_id = p_campaign_id
      and id = v_payload ->> 'screening_id'
      and state <> 'cancelled'
      and (state <> 'occurred' or coalesce((v_payload ->> 'correction')::boolean, false));
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'invalid_screening_cancellation';
    end if;

  elsif v_type = 'verify_opportunity_fact' then
    update campaign_opportunities co
    set verification_items_json = (
          select jsonb_agg(
            case when item ->> 'item_id' = v_payload ->> 'verification_item_id'
              then jsonb_set(
                jsonb_set(
                  jsonb_set(item, '{status}', to_jsonb(v_payload #>> '{result,status}')),
                  '{source_refs}', coalesce(v_payload #> '{result,source_refs}', '[]'::jsonb)
                ),
                '{blocking}',
                to_jsonb(v_payload #>> '{result,status}' in ('unknown', 'contradicted'))
              )
              else item end
          )
          from jsonb_array_elements(co.verification_items_json) item
        ),
        updated_at = now()
    where co.campaign_id = p_campaign_id
      and co.festival_id = v_payload ->> 'festival_id'
      and exists (
        select 1 from jsonb_array_elements(co.verification_items_json) item
        where item ->> 'item_id' = v_payload ->> 'verification_item_id'
      );
    get diagnostics v_rows = row_count;
    if v_rows <> 1 then
      raise exception using errcode = '55000', message = 'verification_item_not_found';
    end if;

  elsif v_type = 'correct_record' then
    select * into v_prior from campaign_events
    where campaign_id = p_campaign_id and id = v_payload ->> 'prior_ref';
    if not found then
      raise exception using errcode = 'P0002', message = 'correction_target_not_found';
    end if;
    v_replacement := v_payload -> 'replacement';
    if v_prior.payload_json ->> 'type' in ('confirm_screening', 'schedule_screening') then
      v_screening_id := v_prior.payload_json #>> '{payload,screening_id}';
      update screenings
      set access = case
            when v_replacement ->> 'status' in ('unknown', 'contradicted') then 'unknown'
            else v_replacement ->> 'value'
          end,
          source_refs = coalesce(v_replacement -> 'source_refs', '[]'::jsonb),
          evidence_status = v_replacement ->> 'status',
          updated_at = now()
      where campaign_id = p_campaign_id and id = v_screening_id;
      get diagnostics v_rows = row_count;
      if v_rows <> 1 then
        raise exception using errcode = 'P0002', message = 'correction_screening_not_found';
      end if;
    elsif v_prior.payload_json ->> 'type' = 'update_profile_fact' then
      v_fact_key := v_prior.payload_json #>> '{payload,fact_key}';
      if v_fact_key not in ('title', 'synopsis', 'format', 'country', 'themes', 'runtime_minutes') then
        raise exception using errcode = '22023', message = 'ambiguous_profile_correction';
      end if;
      update film_projects
      set profile_json = jsonb_set(profile_json, array[v_fact_key], v_replacement, true),
          updated_at = now()
      where id = v_campaign.film_project_id;
      update film_projects
      set profile_hash = encode(digest(convert_to((profile_json - 'profile_hash')::text, 'UTF8'), 'sha256'), 'hex'),
          profile_json = jsonb_set(
            profile_json,
            '{profile_hash}',
            to_jsonb(encode(digest(convert_to((profile_json - 'profile_hash')::text, 'UTF8'), 'sha256'), 'hex')),
            true
          )
      where id = v_campaign.film_project_id;
    else
      raise exception using errcode = '22023', message = 'ambiguous_correction_target';
    end if;

  elsif v_type = 'close_campaign' then
    update campaigns set lifecycle = 'closed' where id = p_campaign_id;
  end if;

  -- 8. Re-derive the ledger only for evidence-affecting commands.
  if v_type in ('update_profile_fact', 'confirm_screening', 'correct_record')
     or (v_type = 'cancel_screening' and coalesce((v_payload ->> 'correction')::boolean, false)) then
    v_ledger := rederive_campaign_premiere_ledger(p_campaign_id);
    update campaigns
    set premiere_ledger_json = v_ledger,
        ledger_hash = v_ledger ->> 'ledger_hash',
        lifecycle = case
          when lifecycle = 'closed' then lifecycle
          when exists (
            select 1 from jsonb_array_elements(v_ledger -> 'scopes') scope
            where scope ->> 'scope' = 'world'
              and scope ->> 'availability' = 'consumed'
          ) then 'post_premiere'
          else 'active'
        end
    where id = p_campaign_id;
  end if;

  -- 9-10. Increment exactly once and mark the prior strategy stale.
  update campaigns
  set version = version + 1,
      readiness = 'stale',
      strategy_stale = true,
      updated_at = now()
  where id = p_campaign_id;

  v_snapshot := campaign_snapshot_json(p_workspace_id, p_campaign_id);
  v_after_hash := encode(
    digest(
      convert_to(
        campaign_canonical_json(
          jsonb_set(v_snapshot, '{aggregate_hash}', to_jsonb(repeat('0', 64)))
        ),
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );
  update campaigns set aggregate_hash = v_after_hash where id = p_campaign_id;

  perform set_config('campaign.command_event_finalize', 'on', true);
  update campaign_events
  set after_aggregate_hash = v_after_hash
  where id = v_event_id and after_aggregate_hash = repeat('0', 64);
  get diagnostics v_rows = row_count;
  perform set_config('campaign.command_event_finalize', 'off', true);
  if v_rows <> 1 then
    raise exception using errcode = '55000', message = 'event_hash_finalize_failed';
  end if;

  select jsonb_build_object(
    'event_id', ce.id,
    'campaign_id', ce.campaign_id,
    'sequence_no', ce.sequence_no,
    'type', ce.type,
    'command', ce.payload_json,
    'before_aggregate_hash', ce.before_aggregate_hash,
    'after_aggregate_hash', ce.after_aggregate_hash,
    'occurred_at', ce.created_at
  ) into v_event
  from campaign_events ce where ce.id = v_event_id;

  -- 11. Return the authoritative aggregate assembled from committed projections.
  return jsonb_build_object(
    'aggregate', get_campaign_aggregate(p_workspace_id, p_campaign_id),
    'event', v_event,
    'idempotent_replay', false
  );
end;
$$;

create or replace function activate_campaign_strategy(
  p_workspace_id text,
  p_campaign_id text,
  p_based_on_campaign_version integer,
  p_attempt jsonb
)
returns jsonb
language plpgsql
volatile
security definer
set search_path = public, pg_temp
as $$
declare
  v_campaign campaigns%rowtype;
  v_workspace text;
  v_strategy_no integer;
  v_strategy_id text;
  v_snapshot jsonb;
  v_hash text;
  v_projection_count integer;
  v_projection_unique_count integer;
begin
  select * into v_campaign from campaigns where id = p_campaign_id for update;
  if not found then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;
  select fp.workspace_id into v_workspace
  from film_projects fp where fp.id = v_campaign.film_project_id;
  if v_workspace is distinct from p_workspace_id then
    raise exception using errcode = 'P0002', message = 'campaign_not_found';
  end if;

  -- Compare-and-set: never activate a strategy computed from stale state.
  if v_campaign.version <> p_based_on_campaign_version then
    raise exception using
      errcode = '40001',
      message = 'strategy_activation_conflict',
      detail = v_campaign.version::text;
  end if;
  if p_attempt ->> 'outcome' not in ('ready', 'failed') then
    raise exception using errcode = '22023', message = 'invalid_strategy_outcome';
  end if;
  if (p_attempt ->> 'outcome' = 'ready') <>
     (coalesce(jsonb_typeof(p_attempt -> 'plan_json'), 'null') <> 'null') then
    raise exception using errcode = '22023', message = 'strategy_plan_outcome_mismatch';
  end if;

  select coalesce(max(strategy_no), 0) + 1 into v_strategy_no
  from strategy_versions where campaign_id = p_campaign_id;
  v_strategy_id := p_attempt ->> 'strategy_id';

  insert into strategy_versions (
    id, campaign_id, strategy_no, based_on_campaign_version, outcome,
    input_snapshot_json, input_hash, plan_json, diff_json, trace_json,
    reuse_manifest_json, usage_json, policy_versions, model_versions, error_json
  ) values (
    v_strategy_id, p_campaign_id, v_strategy_no, p_based_on_campaign_version,
    p_attempt ->> 'outcome', p_attempt -> 'input_snapshot_json',
    p_attempt ->> 'input_hash', p_attempt -> 'plan_json', p_attempt -> 'diff_json',
    coalesce(p_attempt -> 'trace_json', '{}'::jsonb),
    coalesce(p_attempt -> 'reuse_manifest_json', '{}'::jsonb),
    coalesce(p_attempt -> 'usage_json', '{}'::jsonb),
    coalesce(p_attempt -> 'policy_versions', '[]'::jsonb),
    coalesce(p_attempt -> 'model_versions', '[]'::jsonb),
    p_attempt -> 'error_json'
  );

  if p_attempt ->> 'outcome' = 'ready' then
    -- A B-class replan replaces risk/score evidence as one CAS-protected
    -- projection. C-class inputs carry identical candidates, so this is a
    -- deterministic no-op. Candidate IDs must exactly match the strategy's
    -- current opportunity projection; older opportunity history is retained.
    if jsonb_typeof(p_attempt -> 'input_snapshot_json' -> 'candidates') is distinct from 'array'
       or jsonb_typeof(p_attempt -> 'input_snapshot_json' -> 'opportunities') is distinct from 'array' then
      raise exception using errcode = '22023', message = 'strategy_candidate_projection_missing';
    end if;
    select count(*), count(distinct item ->> 'festival_id')
      into v_projection_count, v_projection_unique_count
    from jsonb_array_elements(
      p_attempt -> 'input_snapshot_json' -> 'candidates'
    ) item;
    if v_projection_count < 1
       or v_projection_count <> v_projection_unique_count
       or v_projection_count <> jsonb_array_length(
         p_attempt -> 'input_snapshot_json' -> 'opportunities'
       )
       or exists (
         select 1
         from jsonb_array_elements(
           p_attempt -> 'input_snapshot_json' -> 'candidates'
         ) candidate
         where not exists (
           select 1
           from jsonb_array_elements(
             p_attempt -> 'input_snapshot_json' -> 'opportunities'
           ) opportunity
           where opportunity ->> 'festival_id' = candidate ->> 'festival_id'
         )
       ) then
      raise exception using errcode = '22023', message = 'strategy_candidate_projection_mismatch';
    end if;
    insert into campaign_opportunities (
      id, campaign_id, festival_id, submission_state, offer_state,
      policy_state, evidence_json, creative_scores_json, risk_json,
      verification_items_json, evidence_hash, creative_hash, risk_hash
    )
    select
      opportunity.value ->> 'opportunity_id', p_campaign_id,
      candidate.value ->> 'festival_id',
      opportunity.value ->> 'submission_state',
      opportunity.value ->> 'offer_state',
      opportunity.value ->> 'policy_state',
      candidate.value, candidate.value -> 'creative', candidate.value -> 'risk',
      coalesce(opportunity.value -> 'verification_items', '[]'::jsonb),
      candidate.value ->> 'component_hash',
      candidate.value -> 'creative' ->> 'creative_key',
      candidate.value -> 'risk' ->> 'risk_key'
    from jsonb_array_elements(
      p_attempt -> 'input_snapshot_json' -> 'candidates'
    ) candidate
    join jsonb_array_elements(
      p_attempt -> 'input_snapshot_json' -> 'opportunities'
    ) opportunity
      on opportunity.value ->> 'festival_id' = candidate.value ->> 'festival_id'
    on conflict (campaign_id, festival_id) do update
    set submission_state = excluded.submission_state,
        offer_state = excluded.offer_state,
        policy_state = excluded.policy_state,
        evidence_json = excluded.evidence_json,
        creative_scores_json = excluded.creative_scores_json,
        risk_json = excluded.risk_json,
        verification_items_json = excluded.verification_items_json,
        evidence_hash = excluded.evidence_hash,
        creative_hash = excluded.creative_hash,
        risk_hash = excluded.risk_hash,
        updated_at = now();

    update campaigns
    set active_strategy_version_id = v_strategy_id,
        strategy_stale = false,
        readiness = 'ready',
        updated_at = now()
    where id = p_campaign_id and version = p_based_on_campaign_version;
  else
    -- Failed attempts are immutable and inspectable; the prior active pointer survives.
    update campaigns
    set strategy_stale = true, readiness = 'stale', updated_at = now()
    where id = p_campaign_id and version = p_based_on_campaign_version;
  end if;

  v_snapshot := campaign_snapshot_json(p_workspace_id, p_campaign_id);
  v_hash := encode(
    digest(
      convert_to(
        campaign_canonical_json(
          jsonb_set(v_snapshot, '{aggregate_hash}', to_jsonb(repeat('0', 64)))
        ),
        'UTF8'
      ),
      'sha256'
    ),
    'hex'
  );
  update campaigns set aggregate_hash = v_hash where id = p_campaign_id;
  return get_campaign_aggregate(p_workspace_id, p_campaign_id);
end;
$$;

revoke all on function campaign_snapshot_json(text, text) from public, anon, authenticated;
revoke all on function campaign_canonical_json(jsonb) from public, anon, authenticated;
revoke all on function campaign_utc_text(timestamptz) from public, anon, authenticated;
revoke all on function get_campaign_aggregate(text, text) from public, anon, authenticated;
revoke all on function create_campaign_from_snapshot(text, jsonb) from public, anon, authenticated;
revoke all on function rederive_campaign_premiere_ledger(text) from public, anon, authenticated;
revoke all on function apply_campaign_command(text, text, integer, text, jsonb) from public, anon, authenticated;
revoke all on function activate_campaign_strategy(text, text, integer, jsonb) from public, anon, authenticated;

grant execute on function get_campaign_aggregate(text, text) to service_role;
grant execute on function create_campaign_from_snapshot(text, jsonb) to service_role;
grant execute on function apply_campaign_command(text, text, integer, text, jsonb) to service_role;
grant execute on function activate_campaign_strategy(text, text, integer, jsonb) to service_role;
