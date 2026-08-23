from google.oauth2.service_account import Credentials
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent
)
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.exceptions import (
    InvalidSignatureError
)
from linebot.v3 import (
    WebhookHandler
)
from flask import Flask, request, abort
import os
import json
import re
import datetime
import gspread
from dotenv import load_dotenv
load_dotenv()


app = Flask(__name__)

configuration = Configuration(
    access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))


credentials_json = os.environ.get('GOOGLE_CREDENTIALS')

if credentials_json:
    # Render環境（本番）の場合
    info = json.loads(credentials_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets',
              'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    gc = gspread.authorize(creds)
else:
    # ローカルPC環境（手元に credentials.json がある場合）
    gc = gspread.service_account(filename='credentials.json')
sheet = gc.open('kakeibo-bot-sheets').sheet1


@app.route("/", methods=['POST'])
def callback():
    # get X-Line-Signature header value
    signature = request.headers['X-Line-Signature']

    # get request body as text
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    # handle webhook body
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.info(
            "Invalid signature. Please check your channel access token/channel secret.")
        abort(400)

    return 'OK'


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    reply_text = ""
    match = re.match(r"^(\S+)\s+(-?\d+)(?:\s+(\d{4}-\d{2}-\d{2}))?$", text)
    if match:
        item, amount, date_str = match.groups()
        JST = datetime.timezone(datetime.timedelta(hours=9))
        now = datetime.datetime.now(JST)
        now_data_str = now.strftime('%Y-%m-%d')
        time_str = 'none'
        if not date_str:
            date_str = now_data_str
            time_str = now.strftime('%H:%M')    
        sheet.append_row([date_str,time_str, item, int(amount)])
        reply_text = f"【記録完了】\n日付: {date_str}\n時間: {time_str}\n用途: {item}\n金額: {amount}円"

    elif text.startswith("合計"):
        parts = text.split()
        target_month = datetime.date.today().strftime('%Y-%m')
        search_keyword = None

        # 引数の解析（"合計 2026-08" や "合計 ガシャポン"、"合計 2026-08 ガシャポン" に対応）
        if len(parts) > 1:
            for p in parts[1:]:
                if re.match(r"^\d{4}-\d{2}$", p):  # YYYY-MM 形式なら年月として指定
                    target_month = p
                else:  # それ以外は用途（キーワード）として指定
                    search_keyword = p

        records = sheet.get_all_records()
        filtered_records = []

        for r in records:
            # 年月の判定
            is_month_match = str(r.get('日付', '')).startswith(target_month)
            # 用途キーワードの判定（指定がなければTrue、指定があれば部分一致）
            is_item_match = True
            if search_keyword:
                is_item_match = search_keyword.lower() in str(r.get('用途', '')).lower()

            if is_month_match and is_item_match:
                filtered_records.append(r)

        total = sum(int(r.get('金額', 0)) for r in filtered_records if str(r.get('金額', '')).lstrip('-').isdigit())

        title = f"【{target_month}の合計収支】"
        if search_keyword:
            title = f"【{target_month} 「{search_keyword}」の合計】"

        reply_text = f"{title}\n合計: {total}円"

    elif text == "一覧":
        parts = text.split()
        search_keyword = parts[1] if len(parts) > 1 else None

        records = sheet.get_all_records()

        # 中身が入っている有効な行だけに絞り込む
        valid_records = [
            r for r in records
            if r.get('日付') or r.get('用途') or r.get('金額')
        ]

        # 用途（キーワード）で絞り込み
        if search_keyword:
            valid_records = [
                r for r in valid_records
                if search_keyword.lower() in str(r.get('用途', '')).lower()
            ]

        if not valid_records:
            if search_keyword:
                reply_text = f"「{search_keyword}」に該当する記録はありません。"
            else:
                reply_text = "まだ記録がありません。"
        else:
            lines = []
            for r in valid_records:
                date_val = r.get('日付', '')
                time_val = r.get('時間', '')
                item_val = r.get('用途', '')
                amount_val = r.get('金額', 0)

                if time_val and time_val != 'none':
                    lines.append(f"{date_val} {time_val} | {item_val} | {amount_val}円")
                else:
                    lines.append(f"{date_val} | {item_val} | {amount_val}円")

            header = f"【「{search_keyword}」の記録一覧】\n" if search_keyword else "【全記録一覧】\n"
            reply_text = header + "\n".join(lines)

            if len(reply_text) > 4000:
                reply_text = reply_text[:3900] + \
                    "\n\n...（文字数制限のため途中省略）\n全データはスプレッドシートをご確認ください。"

    elif text == "編集":
        reply_text = f"スプレッドシートから直接編集できます:\n{sheet.url}"

    else:
        reply_text = "コマンド一覧:\n・[用途] [金額] [日付(任意)] (例: 食費 1200)\n・合計 [YYYY-MM]\n・一覧\n・編集"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


if __name__ == "__main__":
    app.run(port=5000)
