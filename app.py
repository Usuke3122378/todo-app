import json
import os

import gspread
from flask import Flask, abort, redirect, render_template, request, url_for

SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1U4RTyzBQED1-zsvUXVCRaCsfpgYgwwuJXgFcFZ1NWpo"
)

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
    todos.sort(key=lambda t: str(t["due_date"]))
    return render_template("index.html", todos=todos)


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
        ])
        return redirect(url_for("index"))
    return render_template("form.html", todo=None)


@app.route("/edit/<int:todo_id>", methods=["GET", "POST"])
def edit(todo_id):
    sheet = get_sheet()
    row, todo = find_row(sheet, todo_id)
    if request.method == "POST":
        sheet.update(
            range_name=f"B{row}:D{row}",
            values=[[
                request.form["title"],
                request.form["content"],
                request.form["due_date"],
            ]],
        )
        return redirect(url_for("index"))
    return render_template("form.html", todo=todo)


if __name__ == "__main__":
    app.run(debug=True, port=8000)
