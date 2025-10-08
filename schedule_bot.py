import subprocess
import sys
import schedule
import time

#subprocess.run([sys.executable, "run_bot.py"], check=True)

def run():
    subprocess.run([sys.executable, "run_bot.py"], check=True)

schedule.every().day.at("07:05", "Europe/Amsterdam").do(run)

while True:
    schedule.run_pending()
    time.sleep(1)