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
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

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

    ignore_templates = [
        "[用途] [金額] [YYYY-MM-DD]",
        "一覧 [YYYY-MM] [用途]",
        "合計 [YYYY-MM] [用途]",
    ]

    if text in ignore_templates:
        return 'OK'

    # ① 「合計」コマンド
    if text.startswith("合計"):
        parts = text.split()
        target_month = None
        search_keyword = None

        if len(parts) > 1:
            for p in parts[1:]:
                if re.match(r"^\d{4}-\d{2}$", p):  # YYYY-MM 形式なら年月
                    target_month = p
                else:  # それ以外は用途キーワード
                    search_keyword = p

        records = sheet.get_all_records()
        filtered_records = []

        for r in records:
            # 年月の判定（指定があれば判定、無ければ全期間）
            is_month_match = True
            if target_month:
                is_month_match = str(r.get('日付', '')).startswith(target_month)

            # 用途キーワードの判定（指定があれば部分一致）
            is_item_match = True
            if search_keyword:
                is_item_match = search_keyword.lower() in str(r.get('用途', '')).lower()

            if is_month_match and is_item_match:
                filtered_records.append(r)

        total = sum(int(r.get('金額', 0)) for r in filtered_records if str(r.get('金額', '')).lstrip('-').isdigit())

        # タイトル構築
        title_parts = []
        if target_month:
            title_parts.append(target_month)
        if search_keyword:
            title_parts.append(f"「{search_keyword}」")
        
        header_title = " ".join(title_parts) if title_parts else "全期間"
        reply_text = f"【{header_title} の合計収支】\n合計: {total}円"

    # ② 「一覧」コマンド（年月・用途・その両方に対応）
    elif text.startswith("一覧"):
        parts = text.split()
        target_month = None
        search_keyword = None

        # 引数の解析（"一覧 2026-08" / "一覧 ガシャポン" / "一覧 2026-08 ガシャポン"）
        if len(parts) > 1:
            for p in parts[1:]:
                if re.match(r"^\d{4}-\d{2}$", p):
                    target_month = p
                else:
                    search_keyword = p

        records = sheet.get_all_records()

        # 有効な行だけに絞り込み
        valid_records = [
            r for r in records
            if r.get('日付') or r.get('用途') or r.get('金額')
        ]

        # 絞り込み処理
        filtered_records = []
        for r in valid_records:
            is_month_match = True
            if target_month:
                is_month_match = str(r.get('日付', '')).startswith(target_month)

            is_item_match = True
            if search_keyword:
                is_item_match = search_keyword.lower() in str(r.get('用途', '')).lower()

            if is_month_match and is_item_match:
                filtered_records.append(r)

        if not filtered_records:
            conditions = []
            if target_month:
                conditions.append(target_month)
            if search_keyword:
                conditions.append(f"「{search_keyword}」")
            
            cond_str = " ".join(conditions) if conditions else ""
            reply_text = f"{cond_str} に該当する記録はありません。" if cond_str else "まだ記録がありません。"
        else:
            lines = []
            for r in filtered_records:
                date_val = r.get('日付', '')
                time_val = r.get('時間', '')
                item_val = r.get('用途', '')
                amount_val = r.get('金額', 0)

                if time_val and time_val != 'none':
                    lines.append(f"{date_val} {time_val} | {item_val} | {amount_val}円")
                else:
                    lines.append(f"{date_val} | {item_val} | {amount_val}円")

            title_parts = []
            if target_month:
                title_parts.append(target_month)
            if search_keyword:
                title_parts.append(f"「{search_keyword}」")
            
            header_title = " ".join(title_parts) if title_parts else "全"
            reply_text = f"【{header_title} 記録一覧】\n" + "\n".join(lines)

            if len(reply_text) > 4000:
                reply_text = reply_text[:3900] + \
                    "\n\n...（文字数制限のため途中省略）\n全データはスプレッドシートをご確認ください。"

    # ③ 「編集」コマンド
    elif text == "編集":
        reply_text = f"スプレッドシートから直接編集できます:\n{sheet.url}"

    # ④ 通常のデータ記録（[用途] [金額] [日付(任意)]）
    else:
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
            sheet.append_row([date_str, time_str, item, int(amount)])
            reply_text = f"【記録完了】\n日付: {date_str}\n時間: {time_str}\n用途: {item}\n金額: {amount}円"
        else:
            reply_text = (
                "コマンド一覧:\n"
                "・[用途] [金額] (例: ガシャポン 500)\n"
                "・合計 [YYYY-MM(任意)] [用途(任意)] (例: 合計 2026-08 ガシャポン)\n"
                "・一覧 [YYYY-MM(任意)] [用途(任意)] (例: 一覧 2026-08 ガシャポン)\n"
                "・編集"
            )

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