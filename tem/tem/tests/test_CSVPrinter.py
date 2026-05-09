import unittest

from speciallecture.CSVPrinter import CSVPrinter


class TestCSVPrinter(unittest.TestCase):

    def test_read(self):
        printer = CSVPrinter("sample.csv")
        line = printer.read()
        print(line)
        self.assertEqual(3, len(line))