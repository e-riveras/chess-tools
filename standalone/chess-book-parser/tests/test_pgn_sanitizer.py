import unittest
from chess_book_parser.parsers.pgn_sanitizer import PGNSanitizer

class TestPGNSanitizer(unittest.TestCase):
    def test_sanitize_single_dot(self):
        self.assertEqual(PGNSanitizer.sanitize("1.c4"), "1. c4")
        self.assertEqual(PGNSanitizer.sanitize("1.c4 e5"), "1. c4 e5")
        self.assertEqual(PGNSanitizer.sanitize("10.e4"), "10. e4")

    def test_sanitize_triple_dot(self):
        self.assertEqual(PGNSanitizer.sanitize("1...e5"), "1... e5")
        self.assertEqual(PGNSanitizer.sanitize("1. c4 1...e5"), "1. c4 1... e5")

    def test_sanitize_mixed(self):
        raw = "1.c4 e5 2.Nc3 Nf6 3.g3 3...d5"
        expected = "1. c4 e5 2. Nc3 Nf6 3. g3 3... d5"
        self.assertEqual(PGNSanitizer.sanitize(raw), expected)

    def test_sanitize_already_correct(self):
        raw = "1. c4 e5 2. Nc3"
        self.assertEqual(PGNSanitizer.sanitize(raw), raw)

if __name__ == '__main__':
    unittest.main()
