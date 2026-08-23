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
        target_month = parts[1] if len(
            parts) > 1 else datetime.date.today().strftime('%Y-%m')

        records = sheet.get_all_records()
        total = sum(int(r['金額']) for r in records if str(
            r['日付']).startswith(target_month))
        reply_text = f"【{target_month}の合計収支】\n合計: {total}円"

    elif text == "一覧":
        records = sheet.get_all_records()

        # 中身が入っている有効な行だけに絞り込む
        valid_records = [
            r for r in records
            if r.get('日付') or r.get('用途') or r.get('金額')
        ]

        if not valid_records:
            reply_text = "まだ記録がありません。"
        else:
            lines = []
            # 件数制限を解除し、全件ループ処理
            for r in valid_records:
                date_val = r.get('日付', '')
                time_val = r.get('時間', '')
                item_val = r.get('用途', '')
                amount_val = r.get('金額', 0)
                if time_val and time_val != 'none':
                    lines.append(f"{date_val} {time_val} | {item_val} | {amount_val}円")
                else:
                    lines.append(f"{date_val} | {item_val} | {amount_val}円")
            # 全件メッセージを作成
            reply_text = "【全記録一覧】\n" + "\n".join(lines)

            # LINEの1通の上限（5,000文字）を超えそうな場合の安全対策
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
