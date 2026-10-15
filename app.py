import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import gspread
from markupsafe import escape
from flask import Flask, abort, redirect, render_template, request, url_for

# 自分のPCでは .env ファイルからキーを読む（Render では環境変数に登録する）
if os.path.exists(".env"):
    for line in open(".env", encoding="utf-8"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ.setdefault(k, v)

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1U4RTyzBQED1-zsvUXVCRaCsfpgYgwwuJXgFcFZ1NWpo"
)

# Render のサーバーは海外時間なので、日本時間で「今日」を出す
JST = timezone(timedelta(hours=9))

# 重要度の並び順（小さいほど上に来る）
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}

# 混雑（503）や回数制限（429）のときは、次のモデルで読み直す
GEMINI_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.5-flash-lite",
]
WEEKDAYS = "月火水木金土日"

app = Flask(__name__)


def get_sheet():
    # Render では環境変数から、自分のPCでは credentials.json から鍵を読む
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    if creds_json:
        gc = gspread.service_account_from_dict(json.loads(creds_json))
    else:
        gc = gspread.service_account(filename="credentials.json")
    return gc.open_by_key(SPREADSHEET_ID).sheet1


def find_row(sheet, todo_id):
    # id が一致する行の「行番号」と「中身」を返す（見出しが1行目なので +2）
    for i, todo in enumerate(sheet.get_all_records()):
        if str(todo["id"]) == str(todo_id):
            return i + 2, todo
    abort(404)


@app.route("/")
def index():
    todos = get_sheet().get_all_records()
    sort = request.args.get("sort", "due")
    if sort == "priority":
        # 重要度順 → 同じ重要度の中では期日順
        todos.sort(key=lambda t: (PRIORITY_ORDER.get(t["priority"], 1), str(t["due_date"])))
    else:
        todos.sort(key=lambda t: str(t["due_date"]))
    # 完了したものは下にまとめる（並び順はそのまま保たれる）
    todos.sort(key=lambda t: t["done"] == "済")
    today = datetime.now(JST).date().isoformat()
    return render_template("index.html", todos=todos, sort=sort, today=today)


def gemini_parse(text):
    # 一言の文章を、タイトル・内容・期日・重要度に分けてもらう
    now = datetime.now(JST)
    prompt = (
        f"今日は {now.date().isoformat()}（{WEEKDAYS[now.weekday()]}曜日）です。\n"
        "次の文章はやることリストに登録したい内容です。\n"
        "- title: 短いタイトル（20文字程度まで）\n"
        "- content: 補足があれば書く。なければ空文字\n"
        "- due_date: 期日を YYYY-MM-DD で。「明日」「来週金曜」なども今日から計算する。書かれていなければ空文字\n"
        "- priority: 高・中・低のどれか。「急ぎ」「大事」「必ず」などがあれば高、「いつでも」「余裕があれば」などは低、それ以外は中\n"
        f"文章：{text}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "title": {"type": "STRING"},
                    "content": {"type": "STRING"},
                    "due_date": {"type": "STRING"},
                    "priority": {"type": "STRING", "enum": ["高", "中", "低"]},
                },
                "required": ["title", "content", "due_date", "priority"],
            },
        },
    }
    for model in GEMINI_MODELS:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": os.environ.get("GEMINI_API_KEY", ""),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                result = json.load(res)
        except (urllib.error.URLError, TimeoutError):
            continue  # 混雑・モデル終了など → 次のモデルへ
        return json.loads(result["candidates"][0]["content"]["parts"][0]["text"])
    return None


@app.route("/quick", methods=["POST"])
def quick():
    text = request.form["text"].strip()
    parsed = gemini_parse(text) if text else None
    if parsed is None:
        # 読み取れなかったときは、文章をタイトルに入れて手入力してもらう
        todo = {"title": text, "content": "", "due_date": "", "priority": "中"}
        message = "AIで読み取れませんでした。内容を入力して保存してください。"
    else:
        todo = parsed
        message = "AIが入力しました。内容を確認して「保存する」を押してください。"
    return render_template("form.html", todo=todo, is_new=True, message=message)


@app.route("/new", methods=["GET", "POST"])
def new():
    if request.method == "POST":
        sheet = get_sheet()
        ids = [int(t["id"]) for t in sheet.get_all_records()]
        new_id = max(ids, default=0) + 1
        sheet.append_row([
            new_id,
            request.form["title"],
            request.form["content"],
            request.form["due_date"],
            request.form["priority"],
            "",
        ])
        return redirect(url_for("index"))
    return render_template("form.html", todo=None, is_new=True)


@app.route("/edit/<int:todo_id>", methods=["GET", "POST"])
def edit(todo_id):
    sheet = get_sheet()
    row, todo = find_row(sheet, todo_id)
    if request.method == "POST":
        sheet.update(
            range_name=f"B{row}:E{row}",
            values=[[
                request.form["title"],
                request.form["content"],
                request.form["due_date"],
                request.form["priority"],
            ]],
        )
        return redirect(url_for("index"))
    return render_template("form.html", todo=todo, is_new=False)


def build_notice(todos):
    # 未完了のうち「期限切れ・今日・明日」のものをまとめた文章を作る
    today = datetime.now(JST).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    today = today.isoformat()
    groups = {"⚠️ 期限切れ": [], "🔥 今日まで": [], "📅 明日まで": []}
    for t in sorted(todos, key=lambda t: (str(t["due_date"]), PRIORITY_ORDER.get(t["priority"], 1))):
        due = str(t["due_date"])
        if t["done"] == "済" or not due:
            continue
        line = f"・[{t['priority'] or '中'}] {t['title']}"
        if due < today:
            groups["⚠️ 期限切れ"].append(f"{line}（{due}）")
        elif due == today:
            groups["🔥 今日まで"].append(line)
        elif due == tomorrow:
            groups["📅 明日まで"].append(line)
    blocks = [f"{name}\n" + "\n".join(items) for name, items in groups.items() if items]
    if not blocks:
        return None
    return "おはようございます！やることのお知らせです。\n\n" + "\n\n".join(blocks) + f"\n\n{request.host_url}"


def send_line(text):
    # LINE_USER_ID があれば自分だけに、なければ友だち全員に送る
    user_id = os.environ.get("LINE_USER_ID")
    if user_id:
        endpoint, body = "push", {"to": user_id}
    else:
        endpoint, body = "broadcast", {}
    body["messages"] = [{"type": "text", "text": text}]
    req = urllib.request.Request(
        f"https://api.line.me/v2/bot/message/{endpoint}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')}",
        },
    )
    urllib.request.urlopen(req, timeout=30)


@app.route("/notify")
def notify():
    # 毎朝 Apps Script から呼ばれる。合言葉が違えば何もしない
    if request.args.get("key") != os.environ.get("NOTIFY_SECRET") or not os.environ.get("NOTIFY_SECRET"):
        abort(403)
    text = build_notice(get_sheet().get_all_records())
    if text is None:
        return "お知らせするタスクはありません（LINEは送っていません）"
    if request.args.get("dry") == "1":
        return f"<pre>{escape(text)}</pre>（確認用：LINEは送っていません）"
    send_line(text)
    return "LINEに送りました"


@app.route("/toggle/<int:todo_id>", methods=["POST"])
def toggle(todo_id):
    # 完了 ⇔ 未完了 を切り替える（F列 = done）
    sheet = get_sheet()
    row, todo = find_row(sheet, todo_id)
    sheet.update_acell(f"F{row}", "" if todo["done"] == "済" else "済")
    return redirect(url_for("index", sort=request.args.get("sort", "due")))


if __name__ == "__main__":
    app.run(debug=True, port=8000)
