from datetime import date, datetime, timedelta
import praw
import re
import pandas as pd
import sqlite3
import shutil
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw, ImageFont
import io
import os
import prawcore
import time

def wrap_method(method):
    def wrapped(self, *args, **kwargs): 
        previously_keep_open = self._keep_open
        self._keep_open = kwargs.pop('keep_open', previously_keep_open) or previously_keep_open

        self._call_stack.append(method.__name__)

        result = method(self, *args, **kwargs)

        self._call_stack.pop()
        if not self._call_stack:
            self.handle_connection(self._keep_open)

        if not previously_keep_open:
            self._keep_open = False

        return result
    return wrapped

class AutoPostCallMeta(type):
    def __new__(cls, name, bases, class_dict):
        new_dict = {}
        for attr_name, attr_value in class_dict.items():
            if callable(attr_value) and not attr_name.startswith("__") and attr_name not in ['handle_connection', 'connection_is_open', 'open_connection', 'close_connection', 'conn', 'cursor']:
                attr_value = wrap_method(attr_value)
            new_dict[attr_name] = attr_value

        return type.__new__(cls, name, bases, new_dict)

class DailyTradeBot(metaclass=AutoPostCallMeta):
    def __init__(self):
        # Setup reddit bot connection
        self.reddit = praw.Reddit('bot1')
        self.reddit.validate_on_submit = True
        self.subreddit = self.reddit.subreddit("dailygames")
    
    _connection_is_open = False
    _keep_open = False
    _call_stack = []

    def __del__(self):
        self.close_connection()

    def connection_is_open(self):
        return self._connection_is_open
    
    def open_connection(self):
        if self.connection_is_open():
            return
        self._connection_is_open = True
        self._conn = sqlite3.connect("reddit_game.db")
        self._cursor = self._conn.cursor()

    def handle_connection(self, keep_open):
        if not keep_open:
            self.close_connection()

    def close_connection(self):
        if self.connection_is_open():
            self._conn.close()
            self._connection_is_open = False
    
    def conn(self):
        self.open_connection()
        return self._conn

    def cursor(self):
        self.open_connection()
        return self._cursor

    def setup_database(self):
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS posts (
                post_id TEXT PRIMARY KEY,
                date DATE
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS gems (
                username TEXT,
                gems TEXT,
                date DATE,
                PRIMARY KEY (username, date)
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS stocks (
                username TEXT,
                subreddit TEXT,
                amount TEXT,
                value FLOAT,
                PRIMARY KEY (username, subreddit)
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS trades (
                username TEXT,
                subreddit TEXT,
                amount TEXT,
                value FLOAT,
                date DATE,
                type TEXT,
                PRIMARY KEY (username, subreddit, date, type)
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS loans (
                username TEXT PRIMARY KEY,
                amount TEXT
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS loans_backup (
                username TEXT,
                amount TEXT,
                type TEXT,
                date DATE,
                PRIMARY KEY (username, date, type)
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS comments (
                comment_id TEXT,
                date DATE,
                PRIMARY KEY (comment_id, date)
            )
        ''')
        self.cursor().execute('''
            CREATE TABLE IF NOT EXISTS posts_per_subreddit (
                subreddit TEXT,
                date DATE,
                posts INT,
                PRIMARY KEY (subreddit, date)
            )
        ''')
        self.conn().commit()

    def run_sql_queries(self, queries):
        for query in queries:
            self.run_sql_query(query)

    def run_sql_query(self, query):
        print("Running SQL query:", query)
        self.cursor(keep_open=True).execute(query)
        self.conn().commit()

    def backup_database(self, for_restoration=False):
        addition = " for restoration" if for_restoration else ""
        shutil.copy2('reddit_game.db', f"reddit_game {datetime.now().strftime('%Y-%m-%d %H.%M.%S')}{addition}.db")
        print("Created database backup")

    def restore_latest_backup(self):
        proceed = input("Do you want to restore the latest backup? (y/n): ").strip().lower()
        if proceed != 'y':
            print("Restoration cancelled.")
            return

        backups = [f for f in os.listdir('.') if f.startswith('reddit_game ') and f.endswith('.db')]
        if not backups:
            print("No backups found.")
            return
        latest_backup = max(backups, key=os.path.getctime)
        self.backup_database(for_restoration=True)
        shutil.copy2(latest_backup, 'reddit_game.db')
        print(f"Database restored from {latest_backup}.")

    def isfloat(self, num):
        try:
            float(num)
            return True
        except ValueError:
            return False
        
    def add_message(self, df, username, message):
        new_row = pd.DataFrame([[username, message]], columns=df.columns)
        return pd.concat([df, new_row], ignore_index=True)
    
    def get_today(self):
        return date.today().isoformat()
    
    def get_latest_post(self):
        self.cursor().execute("""
            SELECT post_id, date
            FROM posts 
            ORDER BY date DESC 
            LIMIT 1
        """)
        latest_post = self.cursor().fetchone()
        post_id, post_date = latest_post
        return (post_id, post_date)
    
    def extract_commands(self, text):
        text = text.replace("\\","")
        commands = []

        pattern = re.compile(r'\[\s*(sell|buy)\s+(\d+|all)(?:\s+r/(\w+))?\s*\]|\[\s*(loan|pay)\s+(\d+|all)\s*\]|\[\s*(exit)\s*\]|\[(.*?)\]')
        for match in pattern.finditer(text.lower()):
            if match.group(1):  # sell or buy
                commands.append({
                    'command': match.group(1),
                    'amount': match.group(2),
                    'subreddit': match.group(3),
                    'unrecognized': None
                })
            elif match.group(4):  # loan or pay
                commands.append({
                    'command': match.group(4),
                    'amount': match.group(5),
                    'subreddit': None,
                    'unrecognized': None
                })
            elif match.group(6):  # exit
                commands.append({
                    'command': match.group(6),
                    'amount': None,
                    'subreddit': None,
                    'unrecognized': None
                })
            else:  # Unrecognized command
                commands.append({
                    'command': None,
                    'amount': None,
                    'subreddit': None,
                    'unrecognized': match.group(7)
                })
            
        return pd.DataFrame(commands)
    
    def user_is_player(self, username):
        self.cursor().execute("SELECT 1 FROM gems WHERE username = ? LIMIT 1", (username,))
        result = self.cursor().fetchone()
        return result is not None

    def current_gems(self, username):
        self.cursor().execute("""
            SELECT gems FROM gems
            WHERE username = ?
            ORDER BY date DESC
            LIMIT 1
        """, (username,))
        gems = self.cursor().fetchone()[0]
        return int(gems)
    
    def add_gems(self, username, amount):    
        gems = self.current_gems(username)

        self.cursor().execute("""
            SELECT date FROM gems
            WHERE username = ?
            ORDER BY date DESC
            LIMIT 1
        """, (username,))
        last_date = self.cursor().fetchone()[0]
        today = self.get_today()

        if today == last_date:     
            self.cursor().execute("UPDATE gems SET gems = ? WHERE username = ? AND date = ?", (str(gems + amount), username, today))
        else:
            self.cursor().execute("INSERT INTO gems (username, gems, date) VALUES (?, ?, ?)", (username, str(gems + amount), today))
        self.conn().commit()

    def has_stocks(self, username, subreddit):
        self.cursor().execute("""
            SELECT 1 FROM stocks
            WHERE username = ?
            AND subreddit = ?
        """, (username,subreddit,))
        stocks_bool = self.cursor().fetchone() is not None
        return stocks_bool
    
    def has_loan(self, username):
        self.cursor().execute("""
            SELECT 1 FROM loans
            WHERE username = ?
        """, (username,))
        loan_bool = self.cursor().fetchone() is not None
        return loan_bool
    
    def to_unix_timestamp(self, date_string):
        # Format: 'YYYY-MM-DD HH:MM' (e.g., '2025-02-01 10:00')
        dt_object = datetime.strptime(date_string, '%Y-%m-%d %H:%M')
        return int(dt_object.timestamp())

    def get_posts_before_date(self, subreddit, date, username = None):
        self.cursor().execute("""
            SELECT 1 FROM posts_per_subreddit
            WHERE LOWER(subreddit) = LOWER(?) AND date = ?
        """, (subreddit,date,))
        has_been_found = self.cursor().fetchone() is not None

        if has_been_found:
            self.cursor().execute("""
                SELECT posts FROM posts_per_subreddit
                WHERE LOWER(subreddit) = LOWER(?) AND date = ?
            """, (subreddit,date,))
            n_posts = self.cursor().fetchone()[0]
            if username is None:
                return n_posts
        elif username is not None:
            raise Exception(f'I cannot find the post count of this subreddit ({subreddit}) and this user ({username}) on this date ({date}) yet!')

        nTries = 0
        while nTries < 20:
            try:
                end_date = datetime.strptime(date, "%Y-%m-%d") 

                end_datetime = datetime(end_date.year, end_date.month, end_date.day, 5, 0)
                start_datetime = end_datetime - timedelta(hours=24)

                start_timestamp = int(start_datetime.timestamp())
                end_timestamp = int(end_datetime.timestamp())

                # Count the number of posts in the time range
                post_count = 0
                
                if has_been_found:
                    user = self.reddit.redditor(username)
                    for submission in user.submissions.new():
                        if submission.subreddit == subreddit and start_timestamp <= submission.created_utc < end_timestamp:
                            post_count += 1
                    return n_posts - post_count
                else:
                    praw_subreddit = self.reddit.subreddit(subreddit)

                    # Loop through submissions in the subreddit
                    for submission in praw_subreddit.new(limit=1000):  # Use .new() to iterate through posts
                        if start_timestamp <= submission.created_utc < end_timestamp:
                            post_count += 1
                        if submission.created_utc < start_timestamp:  # Stop early if past range
                            break
                    return post_count
            except prawcore.exceptions.ServerError:
                print('Server error occurred, trying again in 10 seconds')
                time.sleep(10)
                nTries += 1
        raise Exception('20 server errors occured!')

    def allowed_subreddits(self):
        words = ['dailygames','notinteresting', 'learnpython', 'mildlyinfuriating', '196', '3Blue1Brown', 'AmIOverreacting', 'AmITheAsshole', 'Angryupvote', 'Animal', 'animation', 'antimeme', 'anythingbutmetric', 'AskOuija', 'assholedesign', 'BeAmazed', 'birdification', 'birthofasub', 'blursedimages', 'brandnewsentence', 'capybara', 'chemistrymemes', 'clevercomebacks', 'confidentlyincorrect', 'copypasta', 'countablepixels', 'Damnthatsinteresting', 'dataisbeautiful', 'DnD', 'dndmemes', 'ExplainTheJoke', 'facepalm', 'Fantasy', 'foundsatan', 'foundthemobileuser', 'FreeCompliments', 'gameofthrones', 'geocaching', 'girlsarentreal', 'GuysBeingDudes', 'iamverysmart', 'ididnthaveeggs', 'ihadastroke', 'im14andthisisdeep', 'interesting', 'interestingasfuck', 'LeftTheBurnerOn', 'LetGirlsHaveFun', 'lfg', 'lgbt', 'lies', 'linguisticshumor', 'LinkedInLunatics', 'lostredditors', 'MadeMeSmile', 'mapporncirclejerk', 'MathJokes', 'mathmemes', 'meirl', 'meme', 'memes', 'mildlyinteresting', 'MurderedByWords', 'nature', 'Nicegirls', 'NoahGetTheBoat', 'NonPoliticalTwitter', 'oddlyspecific', 'offmychest', 'onejob', 'penpals', 'PeterExplainsTheJoke', 'pettyrevenge', 'physicsmemes', 'politics', 'PrematureTruncation', 'rareinsults', 'rpg', 'screenshotsarehard', 'softwaregore', 'sssdfg', 'SUBREDDITNAME', 'technicallythetruth', 'teenagersbutbetter', 'thatHappened', 'theydidthemath', 'Tinder', 'trolleyproblem', 'TwoSentenceHorror', 'vexillologycirclejerk', 'circlejerk', 'WeirdEggs', 'Whatcouldgowrong', 'whatisthisthing', 'woosh', 'wordle', 'AnarchyChess', 'shittydarksouls', 'KitchenConfidential', 'CountOnceADay', 'countwithchickenlady', 'SquaredCircle', 'chess', 'Warhammer40k', 'PrimarchGFs', 'SpeedOfLobsters']
        return sorted(words, key=str.lower)

    def is_allowed_subreddit(self, subreddit):
        return subreddit.lower() in [word.lower() for word in self.allowed_subreddits()]
    
    def get_posts_per_subreddit(self, date):
        print("Get posts per subreddit")

        for i, subreddit in enumerate(self.allowed_subreddits()):
            print(f"Checking subreddit {i+1} out of {len(self.allowed_subreddits())}")
            n_posts = self.get_posts_before_date(subreddit, date)
            self.cursor().execute("INSERT OR IGNORE INTO posts_per_subreddit (subreddit, date, posts) VALUES (?, ?, ?)", (subreddit, date, n_posts))
            self.conn().commit()

    def add_player(self, username):
        self.cursor().execute("INSERT INTO gems (username, gems, date) VALUES (?, 1000, ?)", (username, self.get_today()))
        self.conn().commit()
        return f"A new player: {username}, has joined. Welcome! You received 1000 gems."
    
    def unknown_command(self, username, command):
        return f"{username} gave me the following command: '{command}'. I do not know what to do, so no action has been taken."
    
    def buy(self, username, amount, subreddit, date):
        if not self.isfloat(amount):
            return f"{username} tried to buy {amount} stocks from r/{subreddit}, but this is not a whole number. The purchase has been cancelled."    
        amount = int(amount)

        if not self.is_allowed_subreddit(subreddit):
            return f"{username} tried to buy stocks from r/{subreddit}, but this subreddit is not in the list of allowed subreddits. The purchase has been cancelled. If you want to be able to buy stocks from this subreddit, please respond to this message with your request."

        gems = self.current_gems(username)
        number_of_posts = self.get_posts_before_date(subreddit, date, username)
        
        self.cursor().execute("""
            SELECT 1 FROM trades
            WHERE username = ?
            AND subreddit = ?
            AND date = ?
            AND type = ?
        """, (username,subreddit,date,"purchase",))
        has_already_bought_today = self.cursor().fetchone() is not None
            
        total_text = ""
        if has_already_bought_today:
            return f"{username} tried to buy stocks from r/{subreddit}, but has already done so below the same post. This is not possible, so the purchase has been cancelled."    
        if amount > gems:
            total_text = f"{username} tried to buy {amount} stocks from r/{subreddit}, but only had {gems} gems. The purchase has been cancelled. Only {gems} stocks were bought.\n\n"
            amount = gems
        if self.has_stocks(username, subreddit):
            return f"{username} tried to buy stocks from r/{subreddit}, but already has stocks from this subreddit. The purchase has been cancelled."
        if number_of_posts == 0:
            return f"{username} tried to buy stocks from r/{subreddit}, but there were 0 posts on this subreddit. This makes it impossible to determine the stock value. The purchase has been cancelled."
        
        self.add_gems(username,-1*amount)
        
        self.cursor().execute("INSERT INTO trades (username, subreddit, amount, value, date, type) VALUES (?, ?, ?, ?, ?, ?)", (username, subreddit, str(amount), 1/number_of_posts, date, "purchase"))
        self.cursor().execute("INSERT INTO stocks (username, subreddit, amount, value) VALUES (?, ?, ?, ?)", (username, subreddit, str(amount), 1/number_of_posts))
        self.conn().commit()
        
        post_number_text = f"There have been {number_of_posts} posts (not posted by {username})"
        if number_of_posts == 1:
            post_number_text = f"There has been 1 post (not posted by {username})"

        return total_text + f"{username} bought {amount} stocks from r/{subreddit}. {post_number_text}, so that means each post is worth {1/number_of_posts:.5f} gems per stock."
    
    def sell_all(self, username, date):
        self.cursor().execute("""
            SELECT 1 FROM trades
            WHERE username = ?
            AND date = ?
            AND type = ?
        """, (username,date,"sale",))
        has_already_sold_today = self.cursor().fetchone() is not None
        if has_already_sold_today:
            return f"{username} tried to sell all of their stocks, but they already sold one or more stocks today. This is not possible, so the sale has been cancelled."

        stocks = pd.read_sql_query("SELECT subreddit, amount, value FROM stocks WHERE username = ?", self.conn(), params=(username,))
        if stocks.shape[0] == 0:
            return f"{username} tried to sell all of their stocks, but they do not own any stocks."

        total_amount = 0
        total_gems = 0
        for index, row in stocks.iterrows():
            row_amount = int(row['amount'])
            number_of_posts = self.get_posts_before_date(row['subreddit'], date, username)
            gems = round(row_amount*number_of_posts*row['value'])

            total_amount += row_amount
            total_gems += gems

            self.add_gems(username, gems)
            
            if number_of_posts == 0:
                current_value = 0
            else:
                current_value = 1/number_of_posts
            self.cursor().execute("INSERT INTO trades (username, subreddit, amount, value, date, type) VALUES (?, ?, ?, ?, ?, ?)", (username, row['subreddit'], str(-1*row_amount), current_value, date, "sale"))
            self.cursor().execute("DELETE FROM stocks WHERE username = ? AND subreddit = ?", (username, row['subreddit']))
            self.conn().commit()
            
        end_of_message = ""
        if total_gems-total_amount == 1:
            end_of_message = f" This is a profit of 1 gem."
        elif total_gems-total_amount > 0:
            end_of_message = f" This is a profit of {total_gems-total_amount} gems."
        elif total_gems-total_amount == -1:
            end_of_message = f" This is a loss of 1 gem."
        elif total_gems-total_amount < 0:
            end_of_message = f" This is a loss of {total_amount-total_gems} gems."
        return f"{username} sold all of their stocks. They had a total of {total_amount} stocks, divided over {stocks.shape[0]} subreddits. This sale gave {username} {total_gems} gems." + end_of_message
    
    def sell(self,username, amount, subreddit, date):
        if subreddit is None:
            if amount != "all":
                return self.unknown_command(username, f"[sell {amount}]")
            result = self.sell_all(username, date)
            return result

        if not self.has_stocks(username, subreddit):
            return f"{username} tried to sell stocks from r/{subreddit}, but does not own any stocks from this subreddit. The sale has been cancelled."

        self.cursor().execute("""
            SELECT amount, value FROM stocks
            WHERE username = ?
            AND subreddit = ?
        """, (username,subreddit,))
        number_of_stocks, value = self.cursor().fetchone()
        number_of_stocks = int(number_of_stocks)
        value = float(value)
        if amount == "all":
            amount = number_of_stocks
        if not self.isfloat(amount):
            return f"{username} tried to sell {amount} stocks from r/{subreddit}, but this is not a whole number. The sale has been cancelled."    
        amount = int(amount)
        total_text = ""
        if amount > number_of_stocks:
            total_text = f"{username} tried to sell {amount} stocks from r/{subreddit}, but does only own {number_of_stocks} stocks. All stocks of this subreddit will be sold.\n\n"        
            amount = number_of_stocks

        self.cursor().execute("""
            SELECT 1 FROM trades
            WHERE username = ?
            AND subreddit = ?
            AND date = ?
            AND type = ?
        """, (username,subreddit,date,"sale",))
        has_already_sold_today = self.cursor().fetchone() is not None
            
        if has_already_sold_today:
            return f"{username} tried to sell stocks from r/{subreddit}, but has already done so below the same post. This is not possible, so the sale has been cancelled."
        
        number_of_posts = self.get_posts_before_date(subreddit, date, username)
        gems = round(amount*number_of_posts*value)

        self.add_gems(username, gems)
        
        if number_of_posts == 0:
            current_value = 0
        else:
            current_value = 1/number_of_posts
        self.cursor().execute("INSERT INTO trades (username, subreddit, amount, value, date, type) VALUES (?, ?, ?, ?, ?, ?)", (username, subreddit, str(-1*amount), current_value, date, "sale"))
        if amount == number_of_stocks:   
            self.cursor().execute("DELETE FROM stocks WHERE username = ? AND subreddit = ?", (username, subreddit))
        else:
            self.cursor().execute("UPDATE stocks SET amount = ? WHERE username = ? AND subreddit = ?", (number_of_stocks - amount, username, subreddit))                
        self.conn().commit()
        
        end_of_message = ""
        if gems-amount == 1:
            end_of_message = f" This is a profit of 1 gem."
        elif gems-amount > 0:
            end_of_message = f" This is a profit of {gems-amount} gems."
        elif gems-amount == -1:
            end_of_message = f" This is a loss of 1 gem."
        elif gems-amount < 0:
            end_of_message = f" This is a loss of {amount-gems} gems."
        post_number_text = f"There have been {number_of_posts} posts (not posted by {username})."
        if number_of_posts == 1:
            post_number_text = f"There has been 1 post (not posted by {username})."
        return total_text + f"{username} sold {amount} stocks from r/{subreddit}. {post_number_text} Each post was worth {value:.5f} gems per stock. This means that this sale gave {username} {gems} gems." + end_of_message
    
    def loan(self, username, amount, date):
        if not self.isfloat(amount):
            return f"{username} tried to take a loan of {amount} gems, but this is not a whole number. The loan has not been granted."    
        amount = int(amount)
            
        self.cursor().execute("""
            SELECT 1 FROM loans_backup
            WHERE username = ?
            AND date = ?
            AND NOT type = ?
        """, (username,date,'interest',))
        has_already_worked_on_loan_today = self.cursor().fetchone() is not None
            
        if has_already_worked_on_loan_today:
            return f"{username} tried to get a loan, but they already got a loan/bought off a loan today. This is not possible on the same day, so no loan has been granted."
        
        self.add_gems(username, amount)

        self.cursor().execute("INSERT INTO loans_backup (username, amount, type, date) VALUES (?, ?, ?, ?)", (username, str(amount), 'loan', date))
        self.conn().commit()
        if self.has_loan(username):
            self.cursor().execute("""
                SELECT amount FROM loans
                WHERE username = ?
            """, (username,))
            current_loan = int(self.cursor().fetchone()[0])
            self.cursor().execute("UPDATE loans SET amount = ? WHERE username = ?", (str(current_loan + amount), username))                        
        else:
            self.cursor().execute("INSERT INTO loans (username, amount) VALUES (?, ?)", (username, str(amount)))
        self.conn().commit()

        return f"{username} took a loan of {amount} gems. They will have to pay an interest of {round(amount*0.05)} gems each day."

    def pay(self, username, amount):
        self.cursor().execute("""
            SELECT 1 FROM loans_backup
            WHERE username = ?
            AND date = ?
            AND NOT type = ?
        """, (username,date,'interest',))
        has_already_worked_on_loan_today = self.cursor().fetchone() is not None
            
        if has_already_worked_on_loan_today:
            return f"{username} tried to pay off a loan, but they already got a loan/bought off a loan today. This is not possible on the same day, so the payment has not been granted."
        
        gems = self.current_gems(username)
        
        if not self.has_loan(username):
            return f"{username} tried to pay back part of their loan, but they don't have a loan. The payback has been cancelled."
        
        self.cursor().execute("""
            SELECT amount FROM loans
            WHERE username = ?
        """, (username,))
        current_loan = int(self.cursor().fetchone()[0])

        if amount == "all":
            amount = current_loan
        if not self.isfloat(amount):
            return f"{username} tried to pay off {amount} gems from their loan, but this is not a whole number. This payment has not been granted."
        amount = int(amount)

        if amount > current_loan:
            return f"{username} tried to pay back {amount} gems of their loan, but only {current_loan} gems of the loan were left. The payback has been cancelled."
        if amount > gems:
            return f"{username} tried to pay back {amount} gems of their loan, but only had {gems} gems. The payback has been cancelled."
        
        self.add_gems(username, amount*-1)

        self.cursor().execute("INSERT INTO loans_backup (username, amount, type, date) VALUES (?, ?, ?, ?)", (username, str(amount), 'payment', date))
        self.conn().commit()
        if amount == current_loan:
            self.cursor().execute("DELETE FROM loans WHERE username = ?", (username,))
        else:
            self.cursor().execute("UPDATE loans SET amount = ? WHERE username = ?", (str(current_loan - amount), username))                        
        self.conn().commit()
        
        return f"{username} paid off {amount} gems of their loan. Now {current_loan - amount} gems are left in their loan. They will have to pay an interest of {round((current_loan-amount)*0.05)} gems each day."
    
    def exit_game(self, username):
        self.cursor().execute("DELETE FROM gems WHERE username = ?", (username,))
        self.cursor().execute("DELETE FROM stocks WHERE username = ?", (username,))
        self.cursor().execute("DELETE FROM trades WHERE username = ?", (username,))
        self.cursor().execute("DELETE FROM loans WHERE username = ?", (username,))
        self.cursor().execute("DELETE FROM loans_backup WHERE username = ?", (username,))
        self.conn().commit()

        return f"{username} decided to exit the game. Their information has been deleted. Sorry to see you go. You're always welcome to join and start over again!"
    
    def execute_commands(self, username, commands):
        if username in ['B0tRank', 'WhyNotCollegeBoard', 'sneakpeekbot']:
            return
        df = pd.DataFrame(columns=["username", "message"])
        if len(commands) == 0:
            return
        if not self.user_is_player(username):
            result = self.add_player(username)
            df = self.add_message(df,username,result)
        for index, row in commands.iterrows():
            print(f"Currently working on command {index+1} out of {len(commands)}")
            _, latest_post_date = self.get_latest_post(keep_open=True)
            if row['command'] == 'buy':
                result = self.buy(username, row['amount'], row['subreddit'], latest_post_date)
                df = self.add_message(df,username,result)
            elif row['command'] == 'sell':
                result = self.sell(username, row['amount'], row['subreddit'], latest_post_date)
                df = self.add_message(df,username,result)
            elif row['command'] == 'loan':
                result = self.loan(username, row['amount'], self.get_today())
                df = self.add_message(df,username,result)
            elif row['command'] == 'pay':
                result = self.pay(username, row['amount'], self.get_today())
                df = self.add_message(df,username,result)
            elif row['command'] == 'exit':
                result = self.exit_game(username)
                df = self.add_message(df,username,result)            
            elif row['command'] is None:
                result = self.unknown_command(username, row['unrecognized'])
                df = self.add_message(df,username,result)
        return df
    
    def format_messages(self,df):
        grouped = df.groupby('username')['message'].apply('\n\n'.join).reset_index()
        formatted_strings = grouped.apply(lambda row: f"u/{row['username']}\n\n{row['message']}", axis=1)
        return '\n\n---\n'.join(formatted_strings)
    
    def increase_counter(self,i):
        i[0] += 1

    def pay_interest(self,execution_date):
        df = pd.read_sql_query("SELECT username, amount FROM loans", self.conn())
        
        messages = pd.DataFrame(columns=["username", "message"])
        
        for _, row in df.iterrows():
            username = row['username']
            amount = int(row['amount'])
            interest = round(amount*0.05)
            gems = self.current_gems(username)
            if gems >= interest:
                self.add_gems(username, interest*-1)
                messages = self.add_message(messages,username,f"{username} has paid {interest} gems as interest on their loan.")
            else:
                self.add_gems(username, gems*-1)
                loan_increase = interest - gems
                self.cursor().execute("INSERT INTO loans_backup (username, amount, type, date) VALUES (?, ?, ?, ?)", (username, str(loan_increase), 'interest', execution_date))
                self.conn().commit()
                self.cursor().execute("UPDATE loans SET amount = ? WHERE username = ?", (str(amount + loan_increase), username))                        
                self.conn().commit()
                messages = self.add_message(messages,username,f"{username} had to pay {interest} gems as interest on their loan. They only had {gems} gems. The rest has been added to their loan. Their loan is now {amount + loan_increase} gems, so they have to pay {round((amount + loan_increase)*0.05)} gems interest per day.")   
        return messages
    
    def get_virtual_worth(self, username, date):
        worth = self.current_gems(username)
        query = "SELECT subreddit, amount, value FROM stocks WHERE username = ?"
        df = pd.read_sql_query(query, self.conn(), params=(username,))
    
        print(f'getting virtual worth of {username}.')
        for index, row in df.iterrows():
            print(f"stock {index+1} out of {len(df)}.")
            subreddit = row['subreddit']
            amount = int(row['amount'])
            value = row['value']
        
            number_of_posts = self.get_posts_before_date(subreddit, date, username)
            worth += round(amount*number_of_posts*value)
        print("\n")
        return worth
    
    def get_current_rate(self, username, subreddit, amount, value):
        number_of_posts = self.get_posts_before_date(subreddit, self.get_today(), username)
        rate = round(amount*number_of_posts*value) - amount
        if rate > 0:
            rate = "+" + str(rate)
        else:
            rate = str(rate)
        return rate
    
    def create_gem_table(self):
        print("Creating gem table.")
        
        query = "SELECT username, gems, date FROM gems"
        df = pd.read_sql_query(query, self.conn())

        df['date'] = pd.to_datetime(df['date'])

        latest_df = df.sort_values(['username', 'date']).groupby('username').last().reset_index()
        latest_df = latest_df.drop('date',axis=1)

        latest_df = latest_df.sort_values(by="gems", key=lambda s: s.str.lstrip('0').replace('', '0').map(lambda x: (len(x), x)), ascending = False)        
        df = pd.read_sql_query("SELECT username, amount FROM loans", self.conn())

        n_columns = 2
        if len(df) > 0:
            latest_df["gems after interest"] = "-"
            n_columns = 3
            for _, row in df.iterrows():
                interest = round(row['amount']*0.05)
                gems = self.current_gems(row['username'])
                latest_df.loc[latest_df["username"] == row['username'], "gems after interest"] = round(gems-interest)

        # Format gems columns with comma
        if 'gems' in latest_df.columns:
            latest_df['gems'] = latest_df['gems'].apply(lambda s: ','.join([s[max(i - 3, 0):i] for i in range(len(s), 0, -3)][::-1]))
        if 'gems after interest' in latest_df.columns:
            latest_df['gems after interest'] = latest_df['gems after interest'].apply(lambda s: ','.join([s[max(i - 3, 0):i] for i in range(len(s), 0, -3)][::-1]))

        # Calculate column widths dynamically
        col_widths = []
        for col in df.columns:
            max_len = max([len(str(val)) for val in df[col]] + [len(str(col))])
            col_widths.append(max_len * 0.13)  # 0.13 is an empirical scaling factor for width

        # Create table image with dynamic width
        total_width = sum(col_widths)
        fig_width = max(5, total_width)  # Minimum width 5 inches        fig.patch.set_facecolor('white')  # Ensure full white background
        fig, ax = plt.subplots(figsize=(fig_width, 3))  # Height is fixed, width is dynamic
        ax.set_facecolor('white')  # Set axis background to white
        ax.set_title("Gems", fontsize=14, fontweight="bold", pad=15)  # **Title**
        ax.axis('tight')
        ax.axis('off')
        table = ax.table(cellText=latest_df.values, colLabels=latest_df.columns, cellLoc='center', loc='center')
        
        # Set column widths
        for i, width in enumerate(col_widths):
            table.auto_set_column_width(i)
            for j in range(len(df) + 1):  # +1 for header
                table[j, i].set_width(width)

        # Format the Table
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        for j in range(len(df.columns)):
            table[0, j].set_text_props(fontweight="bold")

        # Save image
        plt.savefig("gems.png", dpi=300, bbox_inches="tight")
        
        plt.clf()
        plt.close('all')

    def create_stock_table(self, test = False):
        print("Creating stock table.")

        query = "SELECT username, subreddit, amount, value FROM stocks"
        df = pd.read_sql_query(query, self.conn())

        df = df.sort_values(['username', 'subreddit'])

        if test:
            df['current rate'] = str(5)
        else:        
            j = [0]
            df['current rate'] = [[print(f"row {j[0]+1} out of {len(df)}."), self.increase_counter(j), self.get_current_rate(row['username'], row['subreddit'], int(row['amount']), row['value'])][2]
                for i, row in df.iterrows()]

        df['value'] = df['value'].round(5)
        df = df.rename(columns = {'value':'gems/post/stock'})

        # Convert all numbers to string without scientific notation
        for col in df.columns:
            if col == 'amount':
                df[col] = df[col].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else '')
            elif col == 'current rate':
                # Try to format as int with comma, fallback to float with comma
                def format_rate(val):
                    try:
                        ival = int(float(val))
                        if float(val) == ival:
                            return f"{ival:,}"
                        else:
                            return f"{float(val):,.5f}".rstrip('0').rstrip('.')
                    except Exception:
                        return str(val)
                df[col] = df[col].apply(format_rate)
            elif df[col].dtype == float or df[col].dtype == int:
                df[col] = df[col].apply(lambda x: f"{x:.5f}" if isinstance(x, float) else str(x))

        # Calculate column widths dynamically
        col_widths = []
        for col in df.columns:
            max_len = max([len(str(val)) for val in df[col]] + [len(str(col))])
            col_widths.append(max_len * 0.13)  # 0.13 is an empirical scaling factor for width

        # Create table image with dynamic width
        total_width = sum(col_widths)
        fig_width = max(5, total_width)  # Minimum width 5 inches
        fig, ax = plt.subplots(figsize=(fig_width, 6))  # Height is fixed, width is dynamic
        fig.patch.set_facecolor('white')
        ax.set_facecolor('white')
        ax.set_title("Stocks", fontsize=14, fontweight="bold", pad=15)  # **Title**
        ax.axis('tight')
        ax.axis('off')    
        table = ax.table(cellText=df.values, colLabels=df.columns, cellLoc='center', loc='center')

        # Set column widths
        for i, width in enumerate(col_widths):
            table.auto_set_column_width(i)
            for j in range(len(df) + 1):  # +1 for header
                table[j, i].set_width(width)

        # Format the Table
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        for j in range(len(df.columns)):
            table[0, j].set_text_props(fontweight="bold")

        plt.savefig("stocks.png", dpi=300, bbox_inches="tight")
        plt.clf()
        plt.close('all')

    def create_loan_table(self):
        print("Creating loan table.")

        df = pd.read_sql_query("SELECT username, amount FROM loans", self.conn())
        
        if len(df) == 0:
            return
        
        # Create table image
        fig, ax = plt.subplots(figsize=(5, 2))  # Adjust size as needed
        fig.patch.set_facecolor('white')  # Ensure full white background
        ax.set_facecolor('white')  # Set axis background to white
        ax.set_title("Loans", fontsize=14, fontweight="bold", pad=15)  # **Title**
        ax.axis('tight')
        ax.axis('off')
        table = ax.table(cellText=df.values, colLabels=df.columns, cellLoc='center', loc='center')
        
        # **Format the Table**
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        
        # Make the first row bold
        for j in range(2):  # 2 columns
            table[0, j].set_text_props(fontweight="bold")

        # Save image
        plt.savefig("loans.png", dpi=300, bbox_inches="tight")
        
        plt.clf()
        plt.close('all')

    def create_virtual_worth_table(self):
        print("Creating virtual worth table.")

        df = pd.read_sql_query("SELECT DISTINCT username FROM gems", self.conn())

        df['virtual worth'] = df['username'].apply(lambda user: self.get_virtual_worth(user, self.get_today()))
        df = df.sort_values(['virtual worth', 'username'], ascending = False)
        
        # Format virtual worth with comma
        if 'virtual worth' in df.columns:
            df['virtual worth'] = df['virtual worth'].apply(lambda x: f"{int(x):,}" if pd.notnull(x) else '')

        # Calculate column widths dynamically
        col_widths = []
        for col in df.columns:
            max_len = max([len(str(val)) for val in df[col]] + [len(str(col))])
            col_widths.append(max_len * 0.13)  # 0.13 is an empirical scaling factor for width

        # Create table image with dynamic width
        total_width = sum(col_widths)
        fig_width = max(5, total_width)  # Minimum width 5 inches        fig.patch.set_facecolor('white')  # Ensure full white background
        fig, ax = plt.subplots(figsize=(fig_width, 3))  # Height is fixed, width is dynamic
        ax.set_facecolor('white')  # Set axis background to white
        ax.set_title("Virtual worth (gems + current stock value)", fontsize=14, fontweight="bold", pad=15)  # **Title**
        ax.axis('tight')
        ax.axis('off')
        table = ax.table(cellText=df.values, colLabels=df.columns, cellLoc='center', loc='center')
        
        # Set column widths
        for i, width in enumerate(col_widths):
            table.auto_set_column_width(i)
            for j in range(len(df) + 1):  # +1 for header
                table[j, i].set_width(width)

        # Format the Table
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        for j in range(len(df.columns)):
            table[0, j].set_text_props(fontweight="bold")

        # Save image
        plt.savefig("virtual worth.png", dpi=300, bbox_inches="tight")
        
        plt.clf()
        plt.close('all')

    def create_trend_image(self):
        print("Creating subreddit trend image.")            

        # Fetch data
        query = """
        SELECT subreddit, date, posts
        FROM posts_per_subreddit
        ORDER BY LOWER(subreddit), date;
        """
        df = pd.read_sql(query, self.conn())

        # Process data
        df['date'] = pd.to_datetime(df['date'])
        latest_data = df[df['date'] == df['date'].max()].set_index('subreddit')

        # Define font size
        title_font_size = 50  # Larger font for title
        font_size = 24  # Larger text for content
        title_font = ImageFont.truetype("arial.ttf", title_font_size)
        font = ImageFont.truetype("arial.ttf", font_size)  # Change if needed
        
        summary = []
        for i, subreddit in enumerate(latest_data.index):
            if (i+1)%25 == 0:
                print(f"Working on subreddit {i+1} of {len(latest_data.index)}")
            subset = df[df['subreddit'] == subreddit].sort_values('date')[-7:]

            count_today = subset.iloc[-1]['posts']
            count_yesterday = subset.iloc[-2]['posts'] if len(subset) > 1 else 0
            change = count_today - count_yesterday

            # Dynamic Y-limits for better scaling
            min_y, max_y = subset['posts'].min(), subset['posts'].max()
            buffer = (max_y - min_y) * 0.1  # Add a 10% buffer
            min_y -= buffer
            max_y += buffer

            # Create mini trend plot with smaller size
            fig_width, fig_height = 1.2, 0.6  # Maintain aspect ratio but smaller
            fig, ax = plt.subplots(figsize=(fig_width, fig_height))
            ax.plot(subset['date'], subset['posts'], color='blue', marker='o', markersize=3, linewidth=1)
            ax.set_ylim(min_y, max_y)  # Prevent cutoff
            ax.set_xticks([])  # Remove ticks for clarity
            ax.set_yticks([])
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_linewidth(0.5)
            ax.spines['bottom'].set_linewidth(0.5)
            plt.tight_layout()

            # Save plot as image without resizing
            buf = io.BytesIO()
            plt.savefig(buf, format='PNG', dpi=100, bbox_inches='tight', pad_inches=0.1)
            plt.close(fig)
            buf.seek(0)
            trend_img = Image.open(buf)

            summary.append((subreddit, count_today, change, trend_img))

        # Create final image with 3 columns
        cols = 3
        row_height = 50
        col_width = 450
        img_width = cols * col_width + 20
        img_height = ((len(summary) + cols - 1) // cols) * row_height + 90
        final_img = Image.new("RGB", (img_width, img_height), "white")
        draw = ImageDraw.Draw(final_img)

        # Add title
        title_text = "Allowed Subreddits (and post trends)"
        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        draw.text(((img_width - title_width) // 2, 10), title_text, fill="black", font=title_font)
        
        # Populate the image
        x_offsets = [i * col_width for i in range(cols)]
        y_offset = 90
        for index, (subreddit, count, change, trend_img) in enumerate(summary):
            col = index % cols
            row = index // cols
            x_offset = x_offsets[col] + 10
            y_pos = y_offset + row * row_height
            color = "black" if change == 0 else "green" if change > 0 else "red"
            draw.text((x_offset, y_pos), f"{subreddit}: {count} ({'+' if change >= 0 else ''}{change})", fill=color, font=font)
            final_img.paste(trend_img, (x_offset + 340, y_pos))  # Adjust position without resizing

        # Save or display
        final_img.save("subreddit summary.png")   

    def display_table(self,table_name,order_by=None):        
        self.cursor().execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = self.cursor().fetchall()
        for table in tables:
            if table[0] != table_name:
                continue
            print(f"Table: {table[0]}")
            if order_by is not None:
                self.cursor().execute(f"SELECT * FROM {table[0]} ORDER BY {order_by}")
            else:
                self.cursor().execute(f"SELECT * FROM {table[0]}")
            rows = self.cursor().fetchall()
            for row in rows:
                print(row)
            print("-" * 40)
        self.conn().close()

    def display_all_tables(self):
        self.cursor().execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = self.cursor().fetchall()
        for table in tables:
            print(f"Table: {table[0]}")
            self.cursor().execute(f"SELECT * FROM {table[0]}")
            rows = self.cursor().fetchall()
            for row in rows:
                print(row)
            print("-" * 40)
        self.conn().close()

    def check_sub(self):
        subreddit_name = input('Subreddit name: ')
        print(f"Getting post numbers of the r/{subreddit_name} subreddit from the past few days.")

        for i in range(6):
            handle_date = datetime.today() - timedelta(days=i)
            date_text = f"{handle_date.year}-{handle_date.month}-{handle_date.day}"
            print(f"{date_text}: {str(self.get_posts_before_date(subreddit_name, date_text))}")

    def split_change_log(self, text, max_length=9600):
        parts = []
        part_number = 1

        while len(text) > max_length:
            split_index = text.rfind('\n', 0, max_length)
            if split_index == -1:  # If no newline is found, force split
                split_index = max_length
            
            part_text = text[:split_index]
            formatted_text = (
                f"*These are the actions I performed since last post (part {part_number}).*\n\n"
                "---\n"
                f"{part_text}\n\n"
                "---\n"
                "^(These actions were performed automatically by a bot. If you think I made a mistake, respond to this comment. "
                "This will summon Aart, my creator.)"
            )
            parts.append(formatted_text)
            text = text[split_index+1:]  # Skip the newline character
            part_number += 1

        # Add the last remaining part
        formatted_text = (
            f"*These are the actions I performed since last post (part {part_number}).*\n\n"
            "---\n"
            f"{text}\n\n"
            "---\n"
            "^(These actions were performed automatically by a bot. If you think I made a mistake, respond to this comment. "
            "This will summon Aart, my creator. The code for this bot is fully open source, and can be found [here](https://github.com/AartvB/DailyTrade).)"
        )
        parts.append(formatted_text)

        return parts
    
    def run_bot(self):        
        self.backup_database()

        post_id, post_date = self.get_latest_post()
        
        if (datetime.strptime(self.get_today(), "%Y-%m-%d") - datetime.strptime(post_date, "%Y-%m-%d")).days <= 2:
            self.get_posts_per_subreddit(post_date)
        self.get_posts_per_subreddit(self.get_today())

        submission = self.reddit.submission(id=post_id)
        submission.comments.replace_more(limit=None)  # Load all nested comments

        df = pd.DataFrame(columns=["username", "message"])
        df = pd.concat([df, self.pay_interest(self.get_today())], ignore_index=True)
            
        comments_to_ignore = pd.read_sql_query("SELECT comment_id, date FROM comments", self.conn())
        
        for comment in submission.comments.list():
            ignore_comment = False
            for _, row in comments_to_ignore.iterrows():
                if comment.id == row['comment_id'] and post_date == row['date']:
                    ignore_comment = True
                    break
            if ignore_comment:
                continue

            print("Working on comment by " + comment.author.name + ":\n" + comment.body)

            df = pd.concat([df, self.execute_commands(comment.author.name,self.extract_commands(comment.body))], ignore_index=True)
            print("\n")

        df.sort_values(by=['username'])
        
        self.create_gem_table()
        self.create_stock_table()
        self.create_loan_table()
        self.create_virtual_worth_table()
        self.create_trend_image()

        change_log = self.format_messages(df)
        
        print('\n\n\n\n\n\n\n CHANGELOG')
        print(change_log)
        print("Finished applying commands!")

        return change_log
    
    def publish_post(self, change_log):
        print("Publishing post...")
        explanation_text = '''You are looking at the first fully bot-run daily game: **DailyTrade**!\n\n
**How It Works**\n\n
The rules may seem complicated, but it’s actually pretty simple.\n\n
DailyTrade is a stock trading game—but instead of companies, you’re investing in **subreddits**! Stock values are based on how many posts appeared in that subreddit the previous day. You can join by simply announcing your first trade. You get a free starting budget of **1000 gems** to trade with.\n\n
**Example Trade**\n\n
- On **Day 1**, I buy **400 gems** worth of r/notinteresting stock.\n
- Between **Day 0 and Day 1**, there were **50 posts** on r/notinteresting.\n
- On **Day 3**, I decide to sell my stock.\n
- Between **Day 2 and Day 3**, there were **100 posts** on r/notinteresting.\n
- Since the number of posts **doubled**, my stock value doubles as well—I get **800 gems** back.\n
Of course, stock values can go down too—if the number of posts drops, you’ll lose gems when you sell.
\n\n---\n
**Bot Commands**\n\n
The bot reads and processes comments, so please follow these formatting rules carefully.\n\n
- **Always use square brackets** [ ] around commands so the bot knows you’re talking to it.\n
- You can include **regular text** in your comment too—the bot will only process text inside brackets.\n
- Commands can be **chained** (e.g., [buy 400 r/dailygames] blab la [sell 200 r/notinteresting]), and they will be executed in order.\n
**Available Commands**\n\n
- **Buy Stocks**: [buy AMOUNT r/SUBREDDIT]. Example: [buy 400 r/notinteresting] buys 400 gems worth of r/notinteresting stock. Your stocks are valued based on when *you* buy them (even if someone else buys later at a different price).\n
- **Sell Stocks**: [sell AMOUNT r/SUBREDDIT]. Example: [sell 400 r/notinteresting] sells 400 stocks of r/notinteresting at the current rate. You can also sell everything of one subreddit at once: [sell all r/notinteresting]. You can even sell all of your stocks of all subreddits using [sell all].\n
- **Take a Loan**: [loan AMOUNT]. Example: [loan 1000] takes a 1000-gem loan. Interest is **5% per day**, deducted automatically at the start of each day.\n
- **Repay a Loan**: [pay AMOUNT]. Example: [pay 500] pays back 500 gems toward your loan. You can also repay everything at once: [pay all].
- **Stop the game**: [exit] causes the bot to delete all of your information. You can always join again later.
\n\n---\n
**Game Rules & Notes**
- You **cannot** buy extra stocks from a subreddit if you already own some. You must sell all your stocks in that subreddit first.\n
- You can only buy stocks from certain subreddits, you can find them in one of the images. You can request additional subreddits by contacting me (or responding to this post).\n
- **Stock values update at 5 AM GMT** each day, based on the previous 24 hours. Posting time may vary slightly, but calculations are always consistent.\n
- **You can’t influence stock prices by posting** in a subreddit yourself (your own posts are ignored in the post count). Any attempts at ‘insider trading’—like using an alt account to inflate stock values—will be investigated by the Reddit IRS (a.k.a. me).\n
- You can only trade in **whole** number of gems and stocks.\n
Let me know if you have any questions. Happy trading!\n\n
^(This post, and everything in it, was created automatically by a bot. If you think I made a mistake, respond to this post. This will summon Aart, my creator. The code for this bot is fully open source, and can be found [here](https://github.com/AartvB/DailyTrade).)'''
        self.cursor().execute("""
            SELECT COUNT(post_id)
            FROM posts
            LIMIT 1
        """)
        post_count = self.cursor().fetchone()[0] + 1

        post_id, _ = self.get_latest_post()
        submission = self.reddit.submission(id=post_id)

        flair_template_id = next(item['flair_template_id'] for item in submission.flair.choices() if item['flair_text'] == '[Serious]')

        loans_df = pd.read_sql_query("SELECT username, amount FROM loans", self.conn())
        
        if len(loans_df) > 0:
            images = [{"image_path":"dailytrade logo.png"},
                    {"image_path":"gems.png"},
                    {"image_path":"stocks.png"},
                    {"image_path":"loans.png"},
                    {"image_path":"virtual worth.png"},
                    {"image_path":"subreddit summary.png"}]
        else:
            images = [{"image_path":"dailytrade logo.png"},
                    {"image_path":"gems.png"},
                    {"image_path":"stocks.png"},
                    {"image_path":"virtual worth.png"},
                    {"image_path":"subreddit summary.png"}]

        # Submit a post
        post = self.subreddit.submit_gallery(images=images,
        title="DailyTrade day " + str(post_count),
        flair_id=flair_template_id)

        print(f"Post created: {post.url} - {post.id}")

        submission = self.reddit.submission(id=post.id)
        explanation = submission.reply(explanation_text)
        print(f"Explanation posted: {explanation.id}")

        self.cursor().execute("INSERT INTO posts (post_id, date) VALUES (?, ?)", (post.id, self.get_today()))
        self.cursor().execute("INSERT INTO comments (comment_id, date) VALUES (?, ?)", (explanation.id, self.get_today()))
        self.conn().commit()
        if len(change_log) == 0:
            change_log = (
                f"*These are the actions I performed since last post.*\n\n"
                "---\n"
                f"I did not receive any commands, so I did not perform actions since last post.\n\n"
                "---\n"
                "^(These actions were performed automatically by a bot. If you think I made a mistake, respond to this comment. "
                "This will summon Aart, my creator.)")
            log = submission.reply(change_log)
            print(f"Log posted: {log.id}")
            self.cursor().execute("INSERT INTO comments (comment_id, date) VALUES (?, ?)", (log.id, self.get_today()))
            self.conn().commit()        
        elif len(change_log) > 9600:
            for log_part in self.split_change_log(change_log):
                log = submission.reply(log_part)
                print(f"Part of log posted: {log.id}")
                self.cursor().execute("INSERT INTO comments (comment_id, date) VALUES (?, ?)", (log.id, self.get_today()))
                self.conn().commit()
        else:
            change_log = (
                f"*These are the actions I performed since last post.*\n\n"
                "---\n"
                f"{change_log}\n\n"
                "---\n"
                "^(These actions were performed automatically by a bot. If you think I made a mistake, respond to this comment. This will summon Aart, my creator. The code for this bot is fully open source, and can be found [here](https://github.com/AartvB/DailyTrade).)"
            )
            log = submission.reply(change_log)
            print(f"Log posted: {log.id}")
            self.cursor().execute("INSERT INTO comments (comment_id, date) VALUES (?, ?)", (log.id, self.get_today()))
            self.conn().commit()
        print("Finished!")