import csv
import json
from argparse import ArgumentParser
from enum import Enum
from getpass import getpass
from pathlib import Path
from typing import List, Set

from requests import Session


class ANSI(str, Enum):
    end = "\033[0m"
    bold = "\033[01m"
    black = "\033[30m"
    red = "\033[31m"
    orange = "\033[93m"


def style(text: str, *ansi: ANSI) -> str:
    return "".join(ansi) + text + ANSI.end


class MonizzeTransaction:
    date: str
    voucher: str
    amount: float
    detail: str

    def __init__(self, date: str, voucher: str, amount: str, detail: str):
        self.date = date
        self.voucher = voucher
        self.amount = float(amount)
        self.detail = detail

    def __hash__(self):
        return hash(self.date) \
               ^ hash(self.voucher) \
               ^ hash(self.amount) \
               ^ hash(self.detail)

    def __eq__(self, other):
        return hash(self) == hash(other)

    @property
    def row(self) -> List[str]:
        return [
            self.date,
            self.voucher,
            self.amount,
            self.detail,
        ]


class MonizzeClient:
    endpoint: str = "https://happy.monizze.be/api/services/my-monizze/voucher/history"
    vouchers = (
        "emv",
        "ecv",
        "eco"
    )
    _token: str
    _known: list[MonizzeTransaction]
    _session: Session
    _verbose: bool = False

    def __init__(self, token: str, store: Path, verbose=False):
        print("Starting Monizze session")
        self._verbose = verbose
        self._token = token
        self._session = Session()
        self._session.headers["Authorization"] = self._token

    def get_history(self) -> List[MonizzeTransaction]:
        print("Retrieving transaction history...")
        history: Set[MonizzeTransaction] = set()

        page = 0
        stop = False
        while not stop:
            response = self._session.get(f"{self.endpoint}/{page}")
            body = json.loads(response.content)

            size = 0

            for voucher, transactions in body["data"].items():
                size += len(transactions)

                for transaction in transactions:
                    history.add(MonizzeTransaction(
                        date=transaction["date"],
                        voucher=voucher,
                        amount=transaction["amount"],
                        detail=transaction["detail"],
                    ))

            if size == 0:
                stop = True

            page += 1

        return sorted(history, key=lambda t: t.date)


def save_csv(path: Path, history: List[MonizzeTransaction]) -> None:
    print("Saving to CSV...")
    with open(path, "w+", newline="") as f:
        writer = csv.writer(
            f, delimiter=",", quotechar="\"", quoting=csv.QUOTE_ALL
        )
        writer.writerow(["Date", "Monizze Voucher", "Amount", "Detail"])

        for transaction in history:
            writer.writerow(transaction.row)


if __name__ == '__main__':
    parser = ArgumentParser(
        prog="monizze-csv",
        description="retrieve transaction history from Monizze and save as CSV."
    )
    parser.add_argument(
        "-t", "--token", type=str,
        help="your Monizze authorization token"
    )
    parser.add_argument(
        "-o", "--output-path", type=str, required=True,
        help="where to save the CSV"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="show detailed messages"
    )
    args = parser.parse_args()

    if args.token is not None:
        token = args.token
    else:
        token = getpass("Monizze authorization token (leave blank to skip): ")

        if not token:
            exit(1)

    mc = MonizzeClient(token, args.verbose)
    save_csv(args.output_path, mc.get_history())
    print("Done.")
