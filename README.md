# 積読MVP

Flask と SQLite で動く、個人用の積読管理サイトです。

- `/`：すべての本。ログイン後は同じ画面で登録、状態変更、削除が可能
- `/reading`：読んでいる本
- `/want-to-read`：読みたい本
- `/finished`：読み終えた本
- `/login`：管理用ログイン
- `/register`：管理ユーザーの新規登録
- データ：`instance/books.db`（初回起動時に自動作成）
- 表紙：`static/covers/` に置いた WebP ファイル

## ローカルで起動する

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

アプリを起動します。

```bash
flask --app app run --port 8000
```

初回はブラウザで http://127.0.0.1:8000/register を開き、管理用のIDと
パスワードを登録します。登録後はそのままログイン状態になります。登録できる
管理ユーザーは1人だけで、登録後は新規登録画面が自動的に閉じます。パスワード
そのものではなく、Werkzeugで生成したハッシュがSQLiteへ保存されます。

ブラウザで以下を開きます。

- 一覧・編集：http://127.0.0.1:8000/
- ログイン：http://127.0.0.1:8000/login
- 新規登録：http://127.0.0.1:8000/register

終了するときはターミナルで `Ctrl+C` を押します。SQLite のデータは
`instance/books.db` に残るため、再起動しても消えません。

本の状態は `want_to_read`、`reading`、`finished` の3種類です。以前の
`unread` データがある場合は、起動時に `want_to_read` へ自動移行します。
状態別の一覧を速く取得できるよう、`status` と `updated_at` にインデックスを
作成します。

```text
books
├─ id
├─ title
├─ author
├─ status      # want_to_read / reading / finished
├─ cover
├─ created_at
└─ updated_at

admins
├─ id
├─ username
├─ password_hash
├─ created_at
└─ updated_at

app_settings
├─ key
└─ value          # セッション署名鍵など
```

## 表紙画像を追加する

ログイン後の追加欄で「ファイルを選択」から表紙画像を選びます。JPEG、PNG、
WebPに対応し、向きを補正して最大320×480pxのWebPへ自動変換します。アップロード
上限は8MBです。保存ファイル名は自動生成され、元のファイル名は使いません。
表紙は未指定でも登録できます。本を削除すると、ほかの本から使われていない
対応画像も削除されます。

## Raspberry Pi で実行する

`deploy/tsundoku.service` は配置例です。`User`、`Group`、
`WorkingDirectory`、`Environment`、`ExecStart` のユーザー名とパスを
実際の配置先に合わせてから systemd へ登録してください。Piでも起動後に
`/register`を開いて管理ユーザーを登録します。

Gunicorn 単体で確認する場合は次のコマンドを使えます。

```bash
. .venv/bin/activate
gunicorn --workers 1 --threads 2 --bind 127.0.0.1:8000 app:app
```

SQLite と `.env` は `.gitignore` の対象です。push 前に次を確認してください。

```bash
git status
git ls-files '*.db' '.env'
```

## 公開時の注意

編集操作にはID・パスワード認証が必要です。認証情報とセッション署名鍵は
`instance/books.db` に保存します。パスワードは平文保存しません。外部公開時は
HTTPSを使い、必要に応じてCloudflare Accessも重ねてください。最初の
管理ユーザー登録は、外部公開する前かAccessで保護した状態で行ってください。
