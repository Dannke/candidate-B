"""Отчёт по расчётам с подрядчиками на дату.

загрузка и проверка файлов -> состояние на дату -> распределение платежей -> печать.
Решения и допущения — в DECISIONS.md.
"""
import csv
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
ZERO = Decimal("0.00")

ACCEPTED, PLANNED, CANCELLED = "принята", "запланирована", "отменена"

# статус в файле -> (какие даты обязаны быть, каких дат быть не должно); DECISIONS.md, запись 11
WORK_DATES = {
    "planned": ((), ("completed_at", "cancelled_at")),
    "done": (("completed_at",), ("cancelled_at",)),
    "cancelled": (("cancelled_at",), ()),  # приняли, а потом отменили — бывает
}
PAYMENT_DATES = {
    "paid": (("paid_at",), ("refunded_at",)),
    "queued": ((), ("paid_at", "refunded_at")),
    "refunded": (("paid_at", "refunded_at"), ()),
}


class DataError(Exception):
    """Плохие данные: сообщение «файл, строка: что не так»."""


# ---------- загрузка и проверка ----------

def read_csv(folder, name, columns):
    """Строки файла как [(где, {колонка: текст})]. Пустые строки файла — не данные, их нет."""
    rows = []
    try:
        with open(Path(folder, name), encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = [h.strip() for h in next(reader, [])]
            for column in columns:
                if column not in header:
                    raise DataError(f"{name}: нет колонки «{column}»")
            for values in reader:
                if not values:
                    continue
                where = f"{name}, строка {reader.line_num}"
                if len(values) != len(header):
                    raise DataError(f"{where}: значений {len(values)}, а колонок {len(header)}")
                rows.append((where, dict(zip(header, (v.strip() for v in values)))))
    except UnicodeDecodeError:
        raise DataError(f"{name}: файл не в кодировке UTF-8, сохраните его как «CSV UTF-8»") from None
    except OSError as e:
        raise DataError(f"{name}: не удалось прочитать файл ({e})") from None
    return rows


def parse_money(where, row, column):
    text = row[column]
    if not re.fullmatch(r"\d+(\.\d{1,2})?", text):
        raise DataError(f"{where}: в колонке {column} сумма «{text}», нужно число без минуса "
                        f"и не больше двух знаков после точки, например 1500.00")
    return Decimal(text)


def parse_date(where, row, column):
    text = row[column]
    if not text:
        return None
    try:
        if not re.fullmatch(r"\d{4}-\d\d-\d\d", text):
            raise ValueError
        return date.fromisoformat(text)
    except ValueError:
        raise DataError(f"{where}: в колонке {column} дата «{text}», нужна запись вида 2026-11-10") from None


def parse_status(where, row, rules):
    """Статус из файла нужен только для проверки дат — состояние на дату берём из дат."""
    status = row["status"]
    if status not in rules:
        raise DataError(f"{where}: неизвестный статус «{status}», допустимы: {', '.join(rules)}")
    must, empty = rules[status]
    for column in must:
        if row[column] is None:
            raise DataError(f"{where}: статус «{status}», но колонка {column} пустая")
    for column in empty:
        if row[column] is not None:
            raise DataError(f"{where}: статус «{status}», но в колонке {column} есть дата")


def check_id(where, value, seen):
    if not value:
        raise DataError(f"{where}: пустой номер")
    if value in seen:
        raise DataError(f"{where}: номер {value} уже встречался выше")
    seen.add(value)


def load(folder):
    contracts, works, payments = [], [], []

    ids = set()
    for where, row in read_csv(folder, "contracts.csv", ("contract_id", "contractor", "amount", "signed_at")):
        check_id(where, row["contract_id"], ids)
        signed_at = parse_date(where, row, "signed_at")
        if signed_at is None:
            raise DataError(f"{where}: пустая колонка signed_at")
        contracts.append({"contract_id": row["contract_id"], "contractor": row["contractor"],
                          "amount": parse_money(where, row, "amount"), "signed_at": signed_at})
    contract_ids = ids

    columns = ("work_id", "contract_id", "title", "price", "status", "completed_at", "cancelled_at")
    work_contract = {}
    ids = set()
    for where, row in read_csv(folder, "works.csv", columns):
        check_id(where, row["work_id"], ids)
        if row["contract_id"] not in contract_ids:
            raise DataError(f"{where}: работа {row['work_id']} относится к неизвестному договору «{row['contract_id']}»")
        work = {"work_id": row["work_id"], "contract_id": row["contract_id"], "title": row["title"],
                "price": parse_money(where, row, "price"),
                "completed_at": parse_date(where, row, "completed_at"),
                "cancelled_at": parse_date(where, row, "cancelled_at")}
        parse_status(where, {**row, **work}, WORK_DATES)
        work_contract[work["work_id"]] = work["contract_id"]
        works.append(work)

    columns = ("payment_id", "contract_id", "work_id", "purpose", "amount", "status",
               "created_at", "paid_at", "refunded_at")
    ids = set()
    for where, row in read_csv(folder, "payments.csv", columns):
        check_id(where, row["payment_id"], ids)
        if row["contract_id"] not in contract_ids:
            raise DataError(f"{where}: платёж {row['payment_id']} по неизвестному договору «{row['contract_id']}»")
        work_id = row["work_id"] or None
        if work_id is not None and work_id not in work_contract:
            raise DataError(f"{where}: платёж {row['payment_id']} привязан к неизвестной работе «{work_id}»")
        if work_id is not None and work_contract[work_id] != row["contract_id"]:
            raise DataError(f"{where}: платёж {row['payment_id']} по договору {row['contract_id']}, "
                            f"а работа {work_id} — из договора {work_contract[work_id]}")
        payment = {"payment_id": row["payment_id"], "contract_id": row["contract_id"], "work_id": work_id,
                   "purpose": row["purpose"], "amount": parse_money(where, row, "amount"),
                   "created_at": parse_date(where, row, "created_at"),
                   "paid_at": parse_date(where, row, "paid_at"),
                   "refunded_at": parse_date(where, row, "refunded_at")}
        if payment["created_at"] is None:
            raise DataError(f"{where}: пустая колонка created_at")
        parse_status(where, {**row, **payment}, PAYMENT_DATES)
        if payment["refunded_at"] is not None and payment["refunded_at"] < payment["paid_at"]:
            raise DataError(f"{where}: дата возврата refunded_at раньше даты оплаты paid_at")
        payments.append(payment)

    return contracts, works, payments


# ---------- состояние на дату (включительно) ----------

def is_paid(payment, day):
    return (payment["paid_at"] is not None and payment["paid_at"] <= day
            and (payment["refunded_at"] is None or payment["refunded_at"] > day))


def is_queued(payment, day):
    """Справочно: платёж заведён в учёт, а деньги ещё не ушли (DECISIONS.md, запись 2)."""
    return payment["created_at"] <= day and (payment["paid_at"] is None or payment["paid_at"] > day)


def work_state(work, day):
    if work["cancelled_at"] is not None and work["cancelled_at"] <= day:
        return CANCELLED
    if work["completed_at"] is not None and work["completed_at"] <= day:
        return ACCEPTED
    return PLANNED


# ---------- распределение платежей: правило меняется только здесь ----------

def work_number(work):
    return int(re.sub(r"\D", "", work["work_id"]) or 0), work["work_id"]


def distribute(works, states, paid):
    """Платежи договора по работам (DECISIONS.md, записи 4, 6, 8, 9, 14).

    paid — [(номер работы или None, сумма)] выплаченных на дату платежей.
    Возвращает ({номер работы: выплачено}, нераспределённый остаток).
    """
    price = {w["work_id"]: w["price"] for w in works}
    got = {w["work_id"]: ZERO for w in works}
    pool = ZERO
    for work_id, amount in paid:
        if work_id is None or states[work_id] == CANCELLED:
            pool += amount
            continue
        take = min(amount, price[work_id] - got[work_id])
        got[work_id] += take
        pool += amount - take

    accepted = sorted((w for w in works if states[w["work_id"]] == ACCEPTED),
                      key=lambda w: (w["completed_at"], work_number(w)))
    planned = sorted((w for w in works if states[w["work_id"]] == PLANNED), key=work_number)
    for w in accepted + planned:
        take = min(pool, price[w["work_id"]] - got[w["work_id"]])
        got[w["work_id"]] += take
        pool -= take
    return got, pool


def build_report(contracts, works, payments, day):
    report = []
    for c in contracts:
        if c["signed_at"] > day:
            continue
        own_works = [w for w in works if w["contract_id"] == c["contract_id"]]
        own_payments = [p for p in payments if p["contract_id"] == c["contract_id"]]
        states = {w["work_id"]: work_state(w, day) for w in own_works}
        got, rest = distribute(own_works, states,
                               [(p["work_id"], p["amount"]) for p in own_payments if is_paid(p, day)])
        rows = []
        for w in own_works:
            state = states[w["work_id"]]
            rows.append({
                "work_id": w["work_id"], "title": w["title"], "state": state,
                "since": {ACCEPTED: w["completed_at"], CANCELLED: w["cancelled_at"]}.get(state),
                "price": w["price"], "paid": got[w["work_id"]],
                "left": ZERO if state == CANCELLED else w["price"] - got[w["work_id"]],
            })
        report.append({
            "contract_id": c["contract_id"], "contractor": c["contractor"],
            "signed_at": c["signed_at"], "amount": c["amount"], "rows": rows,
            "price": sum((r["price"] for r in rows if r["state"] != CANCELLED), ZERO),
            "paid": sum((r["paid"] for r in rows), ZERO),
            "left": sum((r["left"] for r in rows), ZERO),
            "unallocated": rest,
            "queued": sum((p["amount"] for p in own_payments if is_queued(p, day)), ZERO),
        })
    return report


# ---------- печать ----------

def money(x):
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def table(lines):
    widths = [max(len(line[i]) for line in lines) for i in range(len(lines[0]))]
    out = []
    for line in lines:
        cells = [cell.ljust(w) if i < 3 else cell.rjust(w) for i, (cell, w) in enumerate(zip(line, widths))]
        out.append("  ".join(cells).rstrip())
    return out


def render(report, day):
    out = [f"Расчёты с подрядчиками на {day:%d.%m.%Y}", ""]
    if not report:
        out.append("На эту дату подписанных договоров нет.")
        return "\n".join(out) + "\n"

    for c in report:
        out.append(f"Договор {c['contract_id']} — {c['contractor']}, подписан {c['signed_at']:%d.%m.%Y}")
        lines = [("Работа", "Название", "Состояние на дату", "Цена", "Выплачено", "Осталось")]
        for r in c["rows"]:
            state = f"{r['state']} {r['since']:%d.%m.%Y}" if r["since"] else r["state"]
            lines.append((r["work_id"], r["title"], state, money(r["price"]), money(r["paid"]), money(r["left"])))
        has_cancelled = any(r["state"] == CANCELLED for r in c["rows"])
        lines.append(("Итого", "(без отменённых работ)" if has_cancelled else "", "",
                      money(c["price"]), money(c["paid"]), money(c["left"])))
        rendered = table(lines)
        out += rendered[:-1] + ["-" * max(len(s) for s in rendered), rendered[-1]]

        out.append(f"  Сумма договора: {money(c['amount'])}")
        if c["amount"] > c["price"]:
            out.append(f"  Сумма договора больше цены работ на {money(c['amount'] - c['price'])}")
        elif c["amount"] < c["price"]:
            out.append(f"  Цена работ больше суммы договора на {money(c['price'] - c['amount'])}")
        # эту строку сверяют с банком: в «Итого» переплата не входит
        out.append(f"  Всего выплачено по договору: {money(c['paid'] + c['unallocated'])}")
        if c["unallocated"]:
            out.append(f"  Переплата / нераспределено: {money(c['unallocated'])}")
        if c["queued"]:
            out.append(f"  В очереди на оплату (деньги ещё не ушли, в «Выплачено» не входит): {money(c['queued'])}")
        out.append("")

    out.append("Платежи без привязки к работе зачтены по порядку: сначала принятые работы по дате приёмки,")
    out.append("затем запланированные по номеру. Каждая работа закрывается полностью, прежде чем деньги идут на следующую.")
    return "\n".join(out) + "\n"


def last_date(contracts, works, payments):
    """Последняя дата в файлах: что было позже, отчёт не знает (DECISIONS.md, запись 16)."""
    return max((v for row in contracts + works + payments for v in row.values() if isinstance(v, date)),
               default=None)


USAGE = "Как запустить: python report.py --date 2026-11-10  (дата отчёта ГГГГ-ММ-ДД, включительно)"


def parse_day(text):
    if re.fullmatch(r"\d{4}-\d\d-\d\d", text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return None


def main(argv=None):
    sys.stdout.reconfigure(encoding="utf-8")  # иначе консоль Windows ломает кавычки «»
    sys.stderr.reconfigure(encoding="utf-8")
    # argparse не берём: его служебные сообщения на английском, а читает бухгалтер
    argv = sys.argv[1:] if argv is None else argv
    if argv in (["-h"], ["--help"]):
        print(USAGE)
        return 0
    if len(argv) == 1 and argv[0].startswith("--date="):
        argv = argv[0].split("=", 1)
    if len(argv) != 2 or argv[0] != "--date":
        problem = "Команда записана неверно." if argv else "Не указана дата отчёта."
        print(f"{problem} {USAGE}", file=sys.stderr)
        return 2
    day = parse_day(argv[1])
    if day is None:
        print(f"Дата «{argv[1]}» записана неверно. {USAGE}", file=sys.stderr)
        return 2
    try:
        contracts, works, payments = load(DATA_DIR)
    except DataError as e:
        print(f"Отчёт не построен — ошибка в данных: {e}", file=sys.stderr)
        return 1
    print(render(build_report(contracts, works, payments, day), day), end="")
    last = last_date(contracts, works, payments)
    if last is not None and day > last:
        print(f"\nВнимание: последняя запись в файлах — от {last:%d.%m.%Y}. Что было позже, отчёт не знает.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
