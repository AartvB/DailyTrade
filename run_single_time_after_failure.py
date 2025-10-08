from dailytradebot import DailyTradeBot

bot = DailyTradeBot()
bot.restore_latest_backup()
change_log = bot.run_bot(keep_open=False)
bot.publish_post(change_log)