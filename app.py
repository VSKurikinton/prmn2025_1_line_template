from flask import Flask

from src import config

app = Flask(__name__)

# config.pyに定義されている、SQLAlchemyの初期化処理を呼び出す。
config.init_sql_alchemy(app)


@app.route("/")
def hello_world():
    return "<p>Hello World!</p>"
