# sauna-app

スマホのブラウザからジャンルを選んでボタンを押すと、ニュースを取ってきて
スライド画像4枚とキャプションを作るアプリ。作った画像はそのままカメラロールに保存できる。

https://sauna-app-n7ga.onrender.com/

## 使い方

1. 上のURLを開く（初回は合言葉を聞かれる。一度入れれば次からは聞かれない）
2. ジャンルを選ぶ … サウナ話題 / サウナ速報 / 銭湯速報
3. 「記事を生成する」を押す。1分ほどかかる
4. できた4枚から要るものにチェックを入れて「選択した◯枚を保存」
5. 共有シートで「画像を保存」→ カメラロールに入る（TikTokへ直接共有も可）
6. キャプションは「コピー」で貼り付けられる

iPhoneなら Safari の共有メニューから「ホーム画面に追加」しておくと、アプリのように開ける。

## Renderの環境変数

| 名前 | 内容 | 必須 |
| --- | --- | --- |
| `GEMINI_API_KEY` | Google AI Studio で発行したAPIキー | 必須 |
| `APP_PASSCODE` | 入口で聞く合言葉 | 推奨 |
| `SUPABASE_URL` | Supabaseプロジェクトのurl | 推奨 |
| `SUPABASE_KEY` | Supabaseの service_role キー | 推奨 |

`APP_PASSCODE` を設定しないと、URLを知っている人は誰でも生成できてしまう
（＝Gemini APIを叩かれる）。厳密な認証ではなく濫用防止の目隠しなので、
覚えやすいもので構わない。未設定なら合言葉なしで動く。

`SUPABASE_*` は使ったニュースの履歴を残すために使う（下の「履歴」を参照）。
未設定でもアプリは動くが、同じネタが繰り返し出やすくなる。

## 履歴（同じネタを繰り返さないために）

Renderはディスクが揮発するので、`used_news_*.json` がその場限りで消える。
放っておくと同じ記事が何度も選ばれるため、履歴だけ Supabase に置いている。

### 用意するもの

1. Supabase のダッシュボード → SQL Editor で `supabase_setup.sql` を実行する
2. Project Settings → API から次の2つを控える
   - Project URL → `SUPABASE_URL`
   - `service_role` キー → `SUPABASE_KEY`
3. Renderの環境変数に入れる

`service_role` キーはこのアプリのサーバー側でしか使わない。ブラウザには
一切渡していないので外に出ることはないが、扱いはAPIキーと同じで、
リポジトリにも画面にも書かないこと。

### 手元の履歴を引き継ぐ

これまでPCで貯めた `used_news_*.json` があるなら、流し込んでおくとその分から
重複を避けられる。`~/kansai-sauna` 側にあるファイルも自動で探す。

```bash
.venv/bin/python tools/history_setup.py            # 今どうなっているか見る
.venv/bin/python tools/history_setup.py --import   # 手元のJSONを流し込む
```

同じ記事は `(kind, title)` の一意制約で弾かれるので、何度実行しても増えない。

### しくみ

既存スクリプトには手を入れていない。`pipelines.py` が

1. 走らせる前に、Supabaseの履歴を作業ディレクトリの `used_news_*.json` に書く
2. 走り終わったら、増えた分だけをSupabaseに戻す

という面倒を見るので、スクリプトからは「いつもの場所にいつものファイルがある」
だけに見える。Supabaseが未設定でも、繋がらなくても、生成そのものは止まらない。

## 構成

```
app.py         窓口。ジョブの受け付けと画面
pipelines.py   どのスクリプトをどの順で走らせるか
jptext.py      日本語の折り返し（3系統で共用）
fonts/         同梱の日本語フォント。Renderには日本語フォントが無いので必須
backgrounds/   スライドの背景
static/        PWAのアイコンと manifest.json
```

生成そのものは既存のスクリプトがそのまま担当している。

| ジャンル | ニュース | スライド | キャプション |
| --- | --- | --- | --- |
| サウナ話題 | `news_topic.py` | `slide_topic.py` | `caption_topic.py` |
| サウナ速報 | `news_gemini.py` | `slide.py` | `caption.py` |
| 銭湯速報 | `news_sento.py` | `slide_sento.py` | `caption_sento.py` |

`pipelines.py` はジョブごとに一時ディレクトリを作り、`subprocess` の `cwd=` に
渡して実行する。スクリプトが `posts_topic.txt` のような決め打ちの名前で
やりとりしていても、ジョブ同士がぶつからない。`backgrounds/` と `fonts/` は
シンボリックリンクで見せている。

## API

| | |
| --- | --- |
| `GET /api/config` | 合言葉の要否と、選べるジャンル |
| `POST /api/verify` | 合言葉が合っているか |
| `POST /api/generate` | 生成を始めて job_id を返す。`X-Passcode` ヘッダが要る |
| `GET /api/status/<job_id>` | 進み具合 |
| `GET /api/image/<job_id>/<n>` | できた画像 |
| `GET /api/ping` | 起きているかの確認 |

生成は一度に1件だけ。動いている最中に投げると409を返す。

## 手元で動かす

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
echo 'GEMINI_API_KEY=xxxxx' > .env
.venv/bin/uvicorn app:app --port 8931
```

`.env` は `.gitignore` 済み。合言葉を試すときは `APP_PASSCODE=xxxx` を付けて起動する。

## 積み残し

- **テーマの履歴（`used_theme_*.json`）は残していない。** テーマは候補のある
  ものからランダムに選ぶ作りなので、履歴が無くても偏りにくい。記事の重複を
  防ぐほうが効くので、そちらだけSupabaseに置いている
- **月末まとめ（monthly）は載せていない。** `slide_monthly.py` が話題版と同じ
  `posts_topic.txt` を読むうえ、集計元が上記の履歴なので成立しない。
  月イチの作業なので手元のPCで `main_monthly.py` を走らせる
- **無料プランは15分で寝る。** 復帰に1分近くかかるので、画面では
  「サーバーを起こしています…」と出しながら待っている
