# monizze-csv

The Monizze website does not give users the option to export transactions.
This script scrapes that data and saves it as a CSV file.

### Setup & usage

1. Clone this repository
2. Install python dependencies
3. Run `playwright install` to set up browsers
4. Run `python monizze_csv.py --email <email>`
   
   You will be prompted to enter your Monizze password. It will be stored
   using [keyring](https://keyring.readthedocs.io/en/latest/), 
   so next time you run the script you won't have to enter your password.
   
5. To clear your password from the keyring, run `python monizze_csv.py --clear`