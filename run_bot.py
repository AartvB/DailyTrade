from dailytradebot import DailyTradeBot
import schedule
import time

def run_single_time_after_failure():
    bot = DailyTradeBot()
    bot.restore_latest_backup()
    change_log = bot.run_bot(keep_open=False)
    bot.publish_post(change_log)

def run():
    bot = DailyTradeBot()
    change_log = bot.run_bot(keep_open=True)
    bot.publish_post(change_log)

#run_single_time_after_failure()

schedule.every().day.at("07:05", "Europe/Amsterdam").do(run)

while True:
    schedule.run_pending()
    time.sleep(1)