# DailyTrade

DailyTrade is a reddit bot, used to host a game (DailyTrade) on the subreddit r/dailygames.

This code is shared to give people insight in how this bot works. You are free to use (parts of) this code to create a reddit bot of your own!

## Usage

The code cannot be run in its current state. To run it, you need to:

1. Set up a reddit bot (Create app at https://www.reddit.com/prefs/apps/)
2. Fill in the account details in a file called 'praw.ini'
3. Run the function 'create_database()' in the file 'DailyTrade.ipynb
4. Create a first post in the relevant subreddit, and add the post date and post id to the database table 'posts'
5. From now on you can run the bot by simple running the cell with the function 'run_bot()' in the file 'DailyTrade.ipynb'.
6. Each time you run it, you can do a final check (for example, check the change log and check the created images). If you are happy with the result, you can run the cell with the function 'publish_post()'.
7. If an error occured while running the 'run_bot()' function, you need to delete the file 'reddit_game.db', and rename the latest copy of this file to 'reddit_game.db'. This way, the database is reset to the latest working version.

## License

MIT License

Copyright (c) 2025 AartvB

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.