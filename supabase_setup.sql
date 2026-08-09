-- 使ったニュースの履歴を貯めるテーブル。
-- Supabase のダッシュボード → SQL Editor に貼り付けて実行する。
-- 何度実行しても壊れないようにしてある。

create table if not exists public.used_news (
    id         bigint generated always as identity primary key,
    kind       text        not null,   -- 'topic' | 'sauna' | 'sento'
    title      text        not null,   -- 正規化済みのタイトル。重複判定のキー
    used_on    date,                   -- 使った日
    created_at timestamptz not null default now(),

    -- 同じジャンルで同じ記事は1行だけ。
    -- 書き込み側はこの制約に頼って「重複は黙って捨てる」形にしている
    constraint used_news_kind_title_key unique (kind, title)
);

-- ジャンルごとに新しい順で引くので、その形に合わせた索引
create index if not exists used_news_kind_id_idx
    on public.used_news (kind, id desc);

-- 行レベルセキュリティは有効のままにしておく。
-- アプリは service_role キーで繋ぐので、この制限をすり抜けて読み書きできる。
-- 逆に anon キーが漏れても、このテーブルには触れない。
alter table public.used_news enable row level security;
