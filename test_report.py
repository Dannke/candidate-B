import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

import report

CONTRACTS, WORKS, PAYMENTS = report.load(report.DATA_DIR)


def build(day):
    return report.build_report(CONTRACTS, WORKS, PAYMENTS, date.fromisoformat(day))


def rows(contract):
    """Строки работ в виде (номер, цена, выплачено, осталось) — как в эталоне PLAN.md."""
    return [(r["work_id"], r["price"], r["paid"], r["left"]) for r in contract["rows"]]


class EtalonTest(unittest.TestCase):
    """Контрольные цифры из PLAN.md, шаг 2, на настоящих файлах data/."""

    def test_10_11_2026(self):
        d1, d2 = build("2026-11-10")
        self.assertEqual(rows(d1), [
            ("W-11", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-12", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-13", D("100000.00"), D("0.00"), D("0.00")),
            ("W-14", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-15", D("100000.00"), D("15000.00"), D("85000.00")),
            ("W-16", D("100000.00"), D("0.00"), D("100000.00")),
        ])
        self.assertEqual((d1["price"], d1["paid"], d1["left"]),
                         (D("500000.00"), D("315000.00"), D("185000.00")))
        self.assertEqual((d1["amount"], d1["queued"], d1["unallocated"]),
                         (D("600000.00"), D("150000.00"), D("0.00")))
        self.assertEqual(rows(d2), [
            ("W-21", D("20000.00"), D("20000.00"), D("0.00")),
            ("W-22", D("30000.00"), D("30000.00"), D("0.00")),
            ("W-23", D("40000.00"), D("25000.00"), D("15000.00")),
        ])
        self.assertEqual((d2["price"], d2["paid"], d2["left"], d2["unallocated"]),
                         (D("90000.00"), D("75000.00"), D("15000.00"), D("0.00")))

    def test_10_11_2026_состояние_работ_из_дат_а_не_из_status(self):
        d1, d2 = build("2026-11-10")
        states = {r["work_id"]: r["state"] for r in d1["rows"] + d2["rows"]}
        self.assertEqual(states["W-13"], report.CANCELLED)
        self.assertEqual(states["W-14"], report.ACCEPTED)
        self.assertEqual(states["W-23"], report.PLANNED)  # в файле done, но принята 11.11

    def test_05_09_2026_возврат_ещё_выплачен_и_W13_не_отменена(self):
        (d1,) = build("2026-09-05")  # D-2 подписан 20.09 — его в отчёте нет
        self.assertEqual(d1["contract_id"], "D-1")
        self.assertEqual(rows(d1), [
            ("W-11", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-12", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-13", D("100000.00"), D("100000.00"), D("0.00")),
            ("W-14", D("100000.00"), D("50000.00"), D("50000.00")),
            ("W-15", D("100000.00"), D("0.00"), D("100000.00")),
            ("W-16", D("100000.00"), D("0.00"), D("100000.00")),
        ])
        self.assertEqual((d1["price"], d1["paid"], d1["left"]),
                         (D("600000.00"), D("350000.00"), D("250000.00")))
        self.assertEqual(d1["rows"][2]["state"], report.PLANNED)
        self.assertEqual(d1["queued"], D("0.00"))  # P-4 заведён только 01.11

    def test_12_11_2026_переплата_по_D2(self):
        d2 = build("2026-11-12")[1]
        self.assertEqual(rows(d2), [
            ("W-21", D("20000.00"), D("20000.00"), D("0.00")),
            ("W-22", D("30000.00"), D("30000.00"), D("0.00")),
            ("W-23", D("40000.00"), D("40000.00"), D("0.00")),
        ])
        self.assertEqual((d2["paid"], d2["left"], d2["unallocated"]),
                         (D("90000.00"), D("0.00"), D("5000.00")))

    def test_24_08_2026_договоров_нет_отчёт_пустой(self):
        self.assertEqual(build("2026-08-24"), [])
        self.assertIn("подписанных договоров нет", report.render([], date(2026, 8, 24)))


class InvariantTest(unittest.TestCase):
    """Инварианты из CLAUDE.md на каждой дате с 24.08 по 15.11.2026."""

    def test_инварианты_на_каждой_дате(self):
        day = date(2026, 8, 24)
        while day <= date(2026, 11, 15):
            for c in report.build_report(CONTRACTS, WORKS, PAYMENTS, day):
                with self.subTest(day=day, contract=c["contract_id"]):
                    # сумма платежей, выплаченных на дату, посчитана здесь независимо от report.py
                    money_out = sum(
                        (p["amount"] for p in PAYMENTS
                         if p["contract_id"] == c["contract_id"]
                         and p["paid_at"] is not None and p["paid_at"] <= day
                         and (p["refunded_at"] is None or p["refunded_at"] > day)),
                        D("0.00"))
                    self.assertEqual(sum(r["paid"] for r in c["rows"]) + c["unallocated"], money_out)

                    active = [r for r in c["rows"] if r["state"] != report.CANCELLED]
                    for r in c["rows"]:
                        self.assertTrue(D("0.00") <= r["paid"] <= r["price"], r)
                        if r["state"] == report.CANCELLED:
                            self.assertEqual((r["paid"], r["left"]), (D("0.00"), D("0.00")), r)
                        else:
                            self.assertEqual(r["left"], r["price"] - r["paid"], r)

                    self.assertEqual(c["price"], sum(r["price"] for r in active))
                    self.assertEqual(c["paid"], sum(r["paid"] for r in c["rows"]))
                    self.assertEqual(c["left"], sum(r["left"] for r in c["rows"]))
                    self.assertGreaterEqual(c["unallocated"], D("0.00"))

                    partly = [r["work_id"] for r in active if D("0.00") < r["paid"] < r["price"]]
                    self.assertLessEqual(len(partly), 1, partly)  # user story 2
            day += timedelta(days=1)


def work(work_id, price, state, completed_at=None):
    return {"work_id": work_id, "price": D(price), "completed_at": completed_at}, state


def distribute(works_with_states, paid):
    works = [w for w, _ in works_with_states]
    states = {w["work_id"]: s for w, s in works_with_states}
    return report.distribute(works, states, [(wid, D(amount)) for wid, amount in paid])


class DistributeTest(unittest.TestCase):
    """Правило распределения (DECISIONS.md, записи 4, 6, 8, 9, 14) на данных в памяти."""

    def test_принятые_по_дате_приёмки_затем_запланированные_по_номеру(self):
        paid, rest = distribute([
            work("W-1", "100.00", report.PLANNED),
            work("W-2", "100.00", report.ACCEPTED, date(2026, 2, 1)),
            work("W-3", "100.00", report.ACCEPTED, date(2026, 1, 1)),
        ], [(None, "250.00")])
        self.assertEqual(paid, {"W-3": D("100.00"), "W-2": D("100.00"), "W-1": D("50.00")})
        self.assertEqual(rest, D("0.00"))

    def test_запланированные_по_числу_в_номере_W9_раньше_W10(self):
        paid, _ = distribute([
            work("W-10", "100.00", report.PLANNED),
            work("W-9", "100.00", report.PLANNED),
        ], [(None, "100.00")])
        self.assertEqual(paid, {"W-9": D("100.00"), "W-10": D("0.00")})

    def test_привязанный_платёж_идёт_своей_работе(self):
        paid, _ = distribute([
            work("W-1", "100.00", report.PLANNED),
            work("W-2", "100.00", report.PLANNED),
        ], [("W-2", "30.00")])
        self.assertEqual(paid, {"W-1": D("0.00"), "W-2": D("30.00")})

    def test_излишек_привязанного_платежа_уходит_в_общий_остаток(self):
        paid, rest = distribute([
            work("W-1", "100.00", report.PLANNED),
            work("W-2", "100.00", report.PLANNED),
        ], [("W-2", "130.00")])
        self.assertEqual(paid, {"W-1": D("30.00"), "W-2": D("100.00")})
        self.assertEqual(rest, D("0.00"))

    def test_платёж_отменённой_работе_уходит_в_общий_остаток(self):
        paid, rest = distribute([
            work("W-1", "100.00", report.CANCELLED),
            work("W-2", "100.00", report.PLANNED),
        ], [("W-1", "70.00")])
        self.assertEqual(paid, {"W-1": D("0.00"), "W-2": D("70.00")})
        self.assertEqual(rest, D("0.00"))

    def test_переплата_остаётся_нераспределённой(self):
        paid, rest = distribute([work("W-1", "100.00", report.PLANNED)], [(None, "100.01")])
        self.assertEqual(paid, {"W-1": D("100.00")})
        self.assertEqual(rest, D("0.01"))


class StateOnDateTest(unittest.TestCase):
    """Границы дат: дата отчёта включительно."""

    refunded = {"created_at": date(2026, 9, 2), "paid_at": date(2026, 9, 2), "refunded_at": date(2026, 9, 10)}
    queued = {"created_at": date(2026, 11, 1), "paid_at": None, "refunded_at": None}

    def test_платёж_выплачен_со_дня_оплаты(self):
        self.assertFalse(report.is_paid(self.refunded, date(2026, 9, 1)))
        self.assertTrue(report.is_paid(self.refunded, date(2026, 9, 2)))

    def test_платёж_выплачен_до_дня_возврата_а_в_день_возврата_уже_нет(self):
        self.assertTrue(report.is_paid(self.refunded, date(2026, 9, 9)))
        self.assertFalse(report.is_paid(self.refunded, date(2026, 9, 10)))

    def test_платёж_в_очереди_не_выплачен_никогда(self):
        self.assertFalse(report.is_paid(self.queued, date(2099, 1, 1)))

    def test_работа_принята_и_отменена_в_свой_день_включительно(self):
        done = {"completed_at": date(2026, 11, 11), "cancelled_at": None}
        gone = {"completed_at": None, "cancelled_at": date(2026, 10, 12)}
        self.assertEqual(report.work_state(done, date(2026, 11, 10)), report.PLANNED)
        self.assertEqual(report.work_state(done, date(2026, 11, 11)), report.ACCEPTED)
        self.assertEqual(report.work_state(gone, date(2026, 10, 11)), report.PLANNED)
        self.assertEqual(report.work_state(gone, date(2026, 10, 12)), report.CANCELLED)


GOOD = {
    "contracts.csv": "contract_id,contractor,amount,signed_at\n"
                     "D-1,Студия,1000.00,2026-01-01\n",
    "works.csv": "work_id,contract_id,title,price,status,completed_at,cancelled_at\n"
                 "W-1,D-1,Съёмка,1000.00,done,2026-02-01,\n",
    "payments.csv": "payment_id,contract_id,work_id,purpose,amount,status,created_at,paid_at,refunded_at\n"
                    "P-1,D-1,W-1,Аванс,500.00,paid,2026-01-10,2026-01-11,\n",
}


class BadDataTest(unittest.TestCase):
    """Плохие данные — остановка с сообщением «файл, строка, что не так» (DECISIONS.md, запись 11)."""

    def load(self, **replaced):
        files = dict(GOOD)
        for name, (old, new) in replaced.items():
            name += ".csv"
            self.assertIn(old, files[name])
            files[name] = files[name].replace(old, new)
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in files.items():
                Path(tmp, name).write_text(text, encoding="utf-8")
            return report.load(tmp)

    def assertStops(self, where, what, **replaced):
        with self.assertRaises(report.DataError) as caught:
            self.load(**replaced)
        self.assertIn(where, str(caught.exception))
        self.assertIn(what, str(caught.exception))

    def test_хорошие_данные_загружаются(self):
        contracts, works, payments = self.load()
        self.assertEqual(payments[0]["amount"], D("500.00"))
        self.assertEqual(works[0]["completed_at"], date(2026, 2, 1))

    def test_работа_с_неизвестным_договором(self):
        self.assertStops("works.csv, строка 2", "D-9", works=("W-1,D-1", "W-1,D-9"))

    def test_платёж_с_неизвестным_договором(self):
        self.assertStops("payments.csv, строка 2", "D-9", payments=("P-1,D-1", "P-1,D-9"))

    def test_платёж_с_неизвестной_работой(self):
        self.assertStops("payments.csv, строка 2", "W-9", payments=("D-1,W-1", "D-1,W-9"))

    def test_платёж_привязан_к_работе_другого_договора(self):
        self.assertStops(
            "payments.csv, строка 2", "W-1",
            contracts=("D-1,Студия,1000.00,2026-01-01\n",
                       "D-1,Студия,1000.00,2026-01-01\nD-2,Иван,1.00,2026-01-01\n"),
            payments=("P-1,D-1", "P-1,D-2"))

    def test_неизвестный_статус_работы(self):
        self.assertStops("works.csv, строка 2", "finished", works=("done", "finished"))

    def test_неизвестный_статус_платежа(self):
        self.assertStops("payments.csv, строка 2", "sent", payments=(",paid,", ",sent,"))

    def test_paid_без_даты_оплаты(self):
        self.assertStops("payments.csv, строка 2", "paid_at", payments=(",2026-01-11,", ",,"))

    def test_queued_с_датой_оплаты(self):
        self.assertStops("payments.csv, строка 2", "paid_at", payments=(",paid,", ",queued,"))

    def test_done_без_даты_приёмки(self):
        self.assertStops("works.csv, строка 2", "completed_at", works=("2026-02-01", ""))

    def test_сумма_без_копеек_с_одним_знаком_и_с_пробелом_годится(self):
        contracts, works, payments = self.load(payments=("500.00", " 500.5"), works=("1000.00", "1000"))
        self.assertEqual((payments[0]["amount"], works[0]["price"]), (D("500.50"), D("1000.00")))

    def test_сумма_с_тремя_знаками(self):
        self.assertStops("payments.csv, строка 2", "500.005", payments=("500.00", "500.005"))

    def test_работа_принята_а_потом_отменена(self):
        works = self.load(works=("done,2026-02-01,", "cancelled,2026-02-01,2026-03-01"))[1]
        self.assertEqual(report.work_state(works[0], date(2026, 2, 15)), report.ACCEPTED)
        self.assertEqual(report.work_state(works[0], date(2026, 3, 1)), report.CANCELLED)

    def test_отрицательная_сумма(self):
        self.assertStops("contracts.csv, строка 2", "-1000.00", contracts=("1000.00", "-1000.00"))

    def test_дата_не_в_том_виде(self):
        self.assertStops("contracts.csv, строка 2", "01.01.2026", contracts=("2026-01-01", "01.01.2026"))

    def test_возврат_раньше_оплаты(self):
        self.assertStops("payments.csv, строка 2", "refunded_at",
                         payments=("paid,2026-01-10,2026-01-11,", "refunded,2026-01-10,2026-01-11,2026-01-05"))

    def test_повтор_номера_платежа(self):
        line = "P-1,D-1,W-1,Аванс,500.00,paid,2026-01-10,2026-01-11,\n"
        self.assertStops("payments.csv, строка 3", "P-1", payments=(line, line + line))

    def test_нет_колонки(self):
        self.assertStops("works.csv", "price", works=("title,price,status", "title,cost,status"))

    def test_в_строке_не_хватает_значений(self):
        self.assertStops("works.csv, строка 2", "колон", works=("done,2026-02-01,", "done"))


class RenderTest(unittest.TestCase):
    """Печать: русские подписи, суммы с разделителем тысяч и запятой."""

    def test_отчёт_на_10_11(self):
        text = report.render(build("2026-11-10"), date(2026, 11, 10))
        self.assertIn("10.11.2026", text)
        self.assertIn("Студия «Свет»", text)
        self.assertIn("Всего выплачено по договору: 315 000,00", text)
        self.assertIn("В очереди на оплату", text)
        self.assertNotIn("Переплата", text)

    def test_переплата_видна_на_12_11(self):
        text = report.render(build("2026-11-12"), date(2026, 11, 12))
        self.assertIn("Переплата / нераспределено: 5 000,00", text)
        self.assertIn("Всего выплачено по договору: 95 000,00", text)  # в «Итого» только 90 000

    def test_в_отчёте_нет_технических_слов(self):
        text = report.render(build("2026-11-10"), date(2026, 11, 10))
        for word in ("status", "paid", "queued", "planned", "done", "None", "Decimal"):
            self.assertNotIn(word, text)


def run_report(*args):
    """Запуск как у бухгалтера: отдельный процесс, из папки программы."""
    return subprocess.run([sys.executable, "report.py", *args], cwd=Path(report.__file__).parent,
                          capture_output=True, encoding="utf-8")


class CommandLineTest(unittest.TestCase):
    """Сообщения командной строки — по-русски, без служебных английских слов."""

    def assertRussianError(self, result, text):
        self.assertEqual(result.returncode, 2)
        self.assertIn(text, result.stderr)
        self.assertIn("python report.py --date 2026-11-10", result.stderr)
        for word in ("usage", "error", "required", "argument"):
            self.assertNotIn(word, result.stderr)

    def test_отчёт_строится(self):
        result = run_report("--date", "2026-11-10")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("315 000,00", result.stdout)

    def test_дата_через_знак_равенства(self):
        self.assertEqual(run_report("--date=2026-11-10").returncode, 0)

    def test_без_даты(self):
        self.assertRussianError(run_report(), "Не указана дата отчёта")

    def test_неверная_дата(self):
        self.assertRussianError(run_report("--date", "10.11.2026"), "«10.11.2026» записана неверно")

    def test_несуществующая_дата(self):
        self.assertRussianError(run_report("--date", "2026-02-30"), "«2026-02-30» записана неверно")

    def test_лишнее_в_команде(self):
        self.assertRussianError(run_report("--date", "2026-11-10", "--csv"), "Команда записана неверно")

    def test_дата_позже_последней_записи_в_файлах(self):
        self.assertIn("последняя запись в файлах — от 12.11.2026", run_report("--date", "2030-01-01").stdout)
        self.assertNotIn("последняя запись", run_report("--date", "2026-11-12").stdout)

    def test_подсказка(self):
        result = run_report("-h")
        self.assertEqual(result.returncode, 0)
        self.assertIn("python report.py --date 2026-11-10", result.stdout)
        self.assertNotIn("usage", result.stdout)


class RulesTest(unittest.TestCase):
    def test_в_коде_расчёта_нет_дробных_чисел_с_плавающей_точкой(self):
        source = Path(report.__file__).read_text(encoding="utf-8")
        self.assertNotIn("flo" + "at", source)


if __name__ == "__main__":
    unittest.main()
