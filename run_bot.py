from dailytradebot import DailyTradeBot
import schedule
import time

def run():
    bot = DailyTradeBot()
    change_log = bot.run_bot(keep_open=True)
    bot.publish_post(change_log)

schedule.every().day.at("07:05", "Europe/Amsterdam").do(run)

while True:
    schedule.run_pending()
    time.sleep(1)
