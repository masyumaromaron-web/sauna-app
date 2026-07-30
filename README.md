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

`APP_PASSCODE` を設定しないと、URLを知っている人は誰でも生成できてしまう
（＝Gemini APIを叩かれる）。厳密な認証ではなく濫用防止の目隠しなので、
覚えやすいもので構わない。未設定なら合言葉なしで動く。

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

- **重複ネタ判定の履歴が残らない。** `used_news_*.json` / `used_theme_*.json` は
  Renderのディスクが揮発するので育たない。今はテーマと記事の選択を
  ランダムにして被りを減らしている。Supabaseに履歴を置く案を検討中
- **月末まとめ（monthly）は載せていない。** `slide_monthly.py` が話題版と同じ
  `posts_topic.txt` を読むうえ、集計元が上記の履歴なので成立しない。
  月イチの作業なので手元のPCで `main_monthly.py` を走らせる
- **無料プランは15分で寝る。** 復帰に1分近くかかるので、画面では
  「サーバーを起こしています…」と出しながら待っている
