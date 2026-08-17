-- Supabase schema for The Distributor.
-- Run this in the Supabase SQL editor, then: python scripts/seed_supabase.py

create table if not exists festivals (
  id                       text primary key,
  name                     text not null,
  city                     text,
  country                  text,
  region                   text,
  tier                     text,
  category                 text,
  accepts                  jsonb,
  themes                   jsonb,
  month                    text,
  festival_dates           text,
  typical_deadline_month   text,
  submission_open          date,
  next_deadline            date,
  final_deadline           date,
  status                   text,
  premiere_requirement     text,
  premiere_requirement_raw text,
  premiere_territory       text,
  submission_fee           text,
  waiver                   text,
  website                  text,
  company_previous_films   jsonb,
  strategic_value          text,
  focus                    text,
  award_patterns           text,
  notable_past_selections  jsonb,
  notes                    text,
  identity_confidence      text,
  source                   text
);

create table if not exists companies (
  id              text primary key,
  name            text not null,
  country         text,
  profile         text,
  circuit_summary jsonb,
  films           jsonb
);

create table if not exists company_festival_history (
  id            bigserial primary key,
  company_id    text not null references companies(id) on delete cascade,
  festival_id   text not null,
  festival_name text,
  screenings    int,
  films         jsonb,
  years         jsonb,
  awards        jsonb,
  categories    jsonb,
  result        text,
  note          text
);

create index if not exists company_festival_history_lookup
  on company_festival_history (company_id, festival_id);

create table if not exists agent_runs (
  id         bigserial primary key,
  created_at timestamptz not null default now(),
  prompt     text,
  film_title text,
  step_count int
);

-- The GUI is public and read-only against these tables.
alter table festivals                enable row level security;
alter table companies                enable row level security;
alter table company_festival_history enable row level security;
alter table agent_runs               enable row level security;

drop policy if exists festivals_read on festivals;
create policy festivals_read on festivals for select using (true);

drop policy if exists companies_read on companies;
create policy companies_read on companies for select using (true);

drop policy if exists history_read on company_festival_history;
create policy history_read on company_festival_history for select using (true);

drop policy if exists agent_runs_insert on agent_runs;
create policy agent_runs_insert on agent_runs for insert with check (true);
